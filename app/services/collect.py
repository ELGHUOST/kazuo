"""Collecte des offres : interroge les sources et enregistre les nouvelles offres."""
import argparse
import json
import time
from datetime import datetime, timezone

import httpx

from app.db import migrate, session
from app.dedup import fingerprint
from app.sources import adzuna
from app.sources.base import Offer

# Phase 1 : recherches de test. En phase 3, elles viendront des profils.
RECHERCHES = ["technicien réseau", "technicien informatique", "employé polyvalent"]


def save(offers: list[Offer]) -> int:
    """Enregistre les offres, ignore celles déjà connues. Renvoie le nombre de nouvelles."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    nouvelles = 0
    with session() as conn:
        for o in offers:
            cur = conn.execute(
                """INSERT OR IGNORE INTO offers
                   (source, source_id, fingerprint, title, company, location, contract,
                    salary, description, url, published_at, fetched_at, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (o.source, o.source_id, fingerprint(o.title, o.company), o.title,
                 o.company, o.location, o.contract, o.salary, o.description, o.url,
                 o.published_at, now, json.dumps(o.raw, ensure_ascii=False)),
            )
            nouvelles += cur.rowcount  # 1 si insérée, 0 si ignorée (déjà en base)
    return nouvelles


def main() -> None:
    p = argparse.ArgumentParser(description="Collecte manuelle des offres")
    p.add_argument("--jours", type=int, default=None, help="seulement les offres des N derniers jours")
    args = p.parse_args()

    print(f"Base : schéma v{migrate()}")
    for i, q in enumerate(RECHERCHES):
        if i:
            time.sleep(adzuna.PAUSE_S)  # on espace aussi les recherches entre elles
        try:
            offers = adzuna.search(q, max_days_old=args.jours)
        except (adzuna.AdzunaError, httpx.HTTPError) as e:
            print(f"[adzuna] « {q} » : ERREUR — {e}")
            continue  # une recherche en échec ne bloque pas les autres
        print(f"[adzuna] « {q} » : {len(offers)} reçues, {save(offers)} nouvelles")


if __name__ == "__main__":
    main()
