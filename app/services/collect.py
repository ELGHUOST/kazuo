"""Collecte des offres : pour chaque profil actif, chaque recherche, chaque source."""
import argparse
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from app.db import migrate, session
from app.dedup import fingerprint, normalize
from app.sources import adzuna
from app.sources.base import Offer

log = logging.getLogger("kazuo.collect")

MAX_RECHERCHES = 25  # garde-fou : au plus 25 recherches par collecte (≤ 75 appels Adzuna)
MAX_ERREURS_RESEAU = 2  # coupe-circuit : au-delà, le réseau est en panne, inutile d'insister
JOURS_RATTRAPAGE = 30   # un profil qui n'a encore aucune offre (tout neuf) est rattrapé sur 30 jours


class CollecteInterrompue(RuntimeError):
    """Collecte arrêtée par le coupe-circuit (réseau en panne, quota atteint)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mot_exclu(titre: str, exclus: list[str]) -> str | None:
    """Premier mot exclu présent dans le titre (mots entiers, sans tenir compte de la casse ni des accents)."""
    t = f" {normalize(titre)} "
    for mot in exclus:
        m = normalize(mot)
        if m and f" {m} " in t:
            return mot
    return None


def save(offers: list[Offer], profile: dict | None = None, query: str = "") -> dict:
    """Enregistre les offres et, si un profil est donné, les rattache à ce profil."""
    now = _now()
    stats = {"recues": len(offers), "nouvelles": 0, "rattachees": 0, "exclues": 0}
    with session() as conn:
        for o in offers:
            cur = conn.execute(
                """INSERT OR IGNORE INTO offers
                   (source, source_id, fingerprint, title, company, location, contract,
                    salary, description, url, published_at, fetched_at, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (o.source, o.source_id, fingerprint(o.title, o.company, o.location), o.title,
                 o.company, o.location, o.contract, o.salary, o.description, o.url,
                 o.published_at, now, json.dumps(o.raw, ensure_ascii=False)),
            )
            stats["nouvelles"] += cur.rowcount
            if profile is None:
                continue
            offer_id = conn.execute("SELECT id FROM offers WHERE source = ? AND source_id = ?",
                                    (o.source, o.source_id)).fetchone()["id"]
            exclu = mot_exclu(o.title, profile["excluded"])
            cur = conn.execute(
                """INSERT OR IGNORE INTO matches (offer_id, profile_id, query, found_at, excluded_by)
                   VALUES (?, ?, ?, ?, ?)""",
                (offer_id, profile["id"], query, now, exclu),
            )
            if cur.rowcount:
                stats["rattachees"] += 1
                stats["exclues"] += exclu is not None
    return stats


def profils_actifs(profile_id: int | None = None) -> list[dict]:
    sql = "SELECT id, name, city, radius_km, keywords, excluded, adzuna_category FROM profiles WHERE status = 'active'"
    args: tuple = ()
    if profile_id is not None:
        sql += " AND id = ?"
        args = (profile_id,)
    with session() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [{**dict(r), "keywords": json.loads(r["keywords"]), "excluded": json.loads(r["excluded"])}
            for r in rows]


def run(jours: int | None = 1, profile_id: int | None = None) -> list[dict]:
    """Une collecte complète. Sera aussi appelée par le planificateur de 7h30 (phase 3)."""
    migrate()
    profils = profils_actifs(profile_id)
    with session() as conn:
        deja_servis = {r[0] for r in conn.execute("SELECT DISTINCT profile_id FROM matches")}
    neufs = {p["id"] for p in profils if p["id"] not in deja_servis}
    for p in profils:
        if p["id"] in neufs:
            log.info("profil « %s » sans aucune offre : rattrapage sur %s jours", p["name"], JOURS_RATTRAPAGE)
    taches = [(p, q) for p in profils for q in p["keywords"]]
    if len(taches) > MAX_RECHERCHES:
        log.warning("%s recherches demandées, limitées à %s", len(taches), MAX_RECHERCHES)
        taches = taches[:MAX_RECHERCHES]
    bilan = []
    erreurs_reseau = 0
    for i, (p, q) in enumerate(taches):
        if i:
            time.sleep(adzuna.PAUSE_S)  # on espace les recherches entre elles
        # Fenêtre de recherche : celle demandée, élargie à 30 jours pour un profil tout neuf
        fenetre = max(jours, JOURS_RATTRAPAGE) if (p["id"] in neufs and jours is not None) else jours
        try:
            offers = adzuna.search(q, where=p["city"], distance_km=p["radius_km"], max_days_old=fenetre,
                                   category=p["adzuna_category"])
            stats = save(offers, p, q)
        except httpx.TransportError as e:  # réseau : DNS, TLS, délai dépassé, connexion coupée
            erreurs_reseau += 1
            log.warning("[adzuna] %s / « %s » : erreur réseau %s/%s : %s",
                        p["name"], q, erreurs_reseau, MAX_ERREURS_RESEAU, e)
            bilan.append({"source": "adzuna", "profil": p["name"], "recherche": q, "erreur": f"réseau : {e}"})
            if erreurs_reseau >= MAX_ERREURS_RESEAU:
                raise CollecteInterrompue(f"{erreurs_reseau} erreurs réseau consécutives "
                                          "(réseau coupé ou portail captif expiré ?)")
            continue
        except adzuna.AdzunaError as e:
            if "Quota" in str(e):
                raise CollecteInterrompue(str(e))  # insister ne ferait qu'aggraver le dépassement
            log.warning("[adzuna] %s / « %s » : %s", p["name"], q, e)
            stats = {"erreur": str(e)}  # une recherche en échec ne bloque pas les autres
        except httpx.HTTPError as e:
            log.warning("[adzuna] %s / « %s » : %s", p["name"], q, e)
            stats = {"erreur": str(e)}
        else:
            erreurs_reseau = 0  # une réussite remet le compteur à zéro
        bilan.append({"source": "adzuna", "profil": p["name"], "recherche": q, **stats})
    return bilan


def recalculer_exclusions(profile_id: int) -> tuple[int, int]:
    """Réapplique les mots exclus ACTUELS du profil à toutes ses offres déjà rattachées.

    excluded_by est une donnée dérivée (titre × mots exclus) : quand les mots exclus
    changent, il faut la recalculer. Renvoie (offres exclues au total, changements).
    """
    with session() as conn:
        p = conn.execute("SELECT excluded FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        if p is None:
            return 0, 0
        exclus = json.loads(p["excluded"])
        changements = 0
        for r in conn.execute(
            """SELECT m.offer_id, m.excluded_by, o.title FROM matches AS m
               JOIN offers AS o ON o.id = m.offer_id WHERE m.profile_id = ?""", (profile_id,)
        ).fetchall():
            nouveau = mot_exclu(r["title"], exclus)
            if nouveau != r["excluded_by"]:
                conn.execute("UPDATE matches SET excluded_by = ? WHERE offer_id = ? AND profile_id = ?",
                             (nouveau, r["offer_id"], profile_id))
                changements += 1
        total = conn.execute("SELECT COUNT(*) FROM matches WHERE profile_id = ? AND excluded_by IS NOT NULL",
                             (profile_id,)).fetchone()[0]
    return total, changements


def main() -> None:
    a = argparse.ArgumentParser(description="Collecte des offres pour les profils actifs")
    a.add_argument("--jours", type=int, default=1, help="offres des N derniers jours (défaut : 1)")
    a.add_argument("--profil", type=int, help="un seul profil (id)")
    a.add_argument("--recalculer-exclusions", action="store_true",
                   help="réapplique les mots exclus aux offres déjà rattachées, sans appeler Adzuna")
    args = a.parse_args()
    if args.recalculer_exclusions:
        ids = [args.profil] if args.profil else [p["id"] for p in profils_actifs()]
        for pid in ids:
            total, changements = recalculer_exclusions(pid)
            print(f"Profil {pid} : {changements} changement(s), {total} offre(s) exclue(s) au total")
        return
    try:
        bilan = run(args.jours, args.profil)
    except CollecteInterrompue as e:
        print(f"Collecte interrompue : {e}")
        return
    if not bilan:
        print("Aucun profil actif : rien à collecter.")
    for b in bilan:
        if "erreur" in b:
            print(f"[{b['source']}] {b['profil']} / « {b['recherche']} » : ERREUR — {b['erreur']}")
        else:
            print(f"[{b['source']}] {b['profil']} / « {b['recherche']} » : {b['recues']} reçues, "
                  f"{b['nouvelles']} nouvelles, {b['rattachees']} rattachées dont {b['exclues']} exclues")


if __name__ == "__main__":
    main()
