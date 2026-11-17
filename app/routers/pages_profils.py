"""Pages web des profils : liste, correction, validation, pause, relance de l'analyse."""
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db import session
from app.llm import queue
from app.services import profiles as service_profils
from app.services.collect import recalculer_exclusions
from app.services.scoring import enqueue_scoring
from app.web import templates

router = APIRouter(include_in_schema=False)  # pages HTML : absentes de /docs

LIBELLES = {"draft": "brouillon", "active": "actif", "paused": "en pause"}
SCEAUX = {"draft": "下書き", "active": "稼働", "paused": "休止"}  # brouillon, en service, à l'arrêt
CONTRATS = ["CDI", "CDD", "intérim", "alternance", "stage", "freelance"]
ROME = re.compile(r"^[A-N][0-9]{4}$")
CHAMPS_JSON = ("jobs", "keywords", "excluded", "rome_codes", "contracts")
CATEGORIES_ADZUNA = {
    "": "Toutes catégories",
    "it-jobs": "Informatique",
    "retail-jobs": "Commerce de détail",
    "hospitality-catering-jobs": "Hôtellerie et restauration",
    "logistics-warehouse-jobs": "Distribution et entrepôts",
    "customer-services-jobs": "Services client",
    "part-time-jobs": "Temps partiel",
}
MESSAGES = {
    "enregistre": "Profil enregistré.",
    "active": "Profil activé : il sera utilisé par la prochaine collecte.",
    "pause": "Profil mis en pause : la collecte l'ignore.",
    "analyse": "Analyse relancée : le profil repasse en brouillon.",
    "cree": "Profil créé à partir de ton CV.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _lire(conn, profile_id: int) -> dict:
    row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="profil introuvable")
    p = dict(row)
    for k in CHAMPS_JSON:
        p[k] = json.loads(p[k])
    return p


def _analyse_en_cours(conn, profile_id: int) -> bool:
    return conn.execute(
        """SELECT 1 FROM llm_jobs
           WHERE kind = 'profile_from_cv' AND status IN ('pending', 'running')
             AND json_extract(payload, '$.profile_id') = ?""",
        (profile_id,),
    ).fetchone() is not None


def _lignes(texte: str) -> list[str]:
    """Une valeur par ligne : espaces retirés, lignes vides et doublons supprimés."""
    vus, propres = set(), []
    for ligne in texte.splitlines():
        v = ligne.strip()
        if v and v.lower() not in vus:
            vus.add(v.lower())
            propres.append(v)
    return propres


def _page(request: Request, p: dict, *, erreur=None, message=None, en_cours=False):
    return templates.TemplateResponse(
        request, "profile_edit.html",
        {"p": p, "libelles": LIBELLES, "sceaux": SCEAUX, "contrats_possibles": CONTRATS, "categories": CATEGORIES_ADZUNA,
         "analyse_en_cours": en_cours, "erreur": erreur, "message": message},
        status_code=400 if erreur else 200,
    )


@router.get("/profils", response_class=HTMLResponse)
def liste(request: Request):
    with session() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM profiles ORDER BY id").fetchall()]
        profils = [_lire(conn, i) for i in ids]
        # Cartes à trier par profil : mêmes règles que le swipe (groupes de doublons, note de 5 ou plus)
        a_trier = {r["profile_id"]: r["n"] for r in conn.execute(
            """SELECT m.profile_id, COUNT(DISTINCT o.fingerprint) AS n
               FROM matches AS m JOIN offers AS o ON o.id = m.offer_id
               WHERE m.decision IS NULL AND m.excluded_by IS NULL AND m.score >= 5
               GROUP BY m.profile_id""")}
    return templates.TemplateResponse(request, "profiles.html", {
        "profils": profils, "libelles": LIBELLES, "sceaux": SCEAUX,
        "categories": CATEGORIES_ADZUNA, "a_trier": a_trier})


# ---------------------------------------------------------------------------
# Création d'un profil depuis le navigateur
# Ces deux routes doivent être déclarées AVANT /profils/{profile_id} : sinon FastAPI
# essaierait de lire « nouveau » comme un numéro de profil et répondrait 422.
# ---------------------------------------------------------------------------
def _page_nouveau(request: Request, v: dict, erreur: str | None = None):
    return templates.TemplateResponse(request, "profile_new.html", {"v": v, "erreur": erreur},
                                      status_code=400 if erreur else 200)


@router.get("/profils/nouveau", response_class=HTMLResponse)
def nouveau(request: Request):
    v = {"nom": "", "objectif": "", "prenom": "", "ville": "Bordeaux", "rayon_km": 30}
    with session() as conn:
        dernier = conn.execute(
            "SELECT first_name, city, radius_km FROM profiles ORDER BY id DESC LIMIT 1").fetchone()
    if dernier:  # on reprend le prénom et la zone du dernier profil : moins de saisie
        v.update(prenom=dernier["first_name"], ville=dernier["city"], rayon_km=dernier["radius_km"])
    return _page_nouveau(request, v)


@router.post("/profils/nouveau")
def creer(
    request: Request,
    nom: Annotated[str, Form()] = "",
    prenom: Annotated[str, Form()] = "",
    objectif: Annotated[str, Form()] = "",
    ville: Annotated[str, Form()] = "",
    rayon_km: Annotated[int, Form()] = 30,
    cv: Annotated[UploadFile | None, File()] = None,
):
    v = {"nom": nom.strip(), "objectif": objectif.strip(), "prenom": prenom.strip(),
         "ville": ville.strip(), "rayon_km": rayon_km}
    # Mêmes contrôles que le navigateur, refaits ici : le navigateur se contourne, le serveur non
    if not (v["nom"] and v["prenom"] and v["ville"]):
        return _page_nouveau(request, v, "Le nom du profil, le prénom et la ville sont obligatoires.")
    if not 5 <= rayon_km <= 100:
        return _page_nouveau(request, v, "Le rayon doit être compris entre 5 et 100 km.")
    if cv is None or not cv.filename:
        return _page_nouveau(request, v, "Choisis ton CV au format PDF.")
    data = cv.file.read(service_profils.MAX_CV_OCTETS + 1)  # jamais plus que la limite + 1 octet
    try:
        profile_id, _ = service_profils.create_profile(
            v["nom"], v["prenom"], v["objectif"], v["ville"], rayon_km, data)
    except service_profils.ProfileError as e:
        return _page_nouveau(request, v, f"{str(e).rstrip('.')}. Choisis à nouveau ton CV : "
                                         "par sécurité, le navigateur ne le garde pas après une erreur.")
    return RedirectResponse(f"/profils/{profile_id}?ok=cree", status_code=303)


@router.get("/profils/{profile_id}", response_class=HTMLResponse)
def detail(request: Request, profile_id: int, ok: str | None = None, renote: int = 0):
    with session() as conn:
        p = _lire(conn, profile_id)
        en_cours = _analyse_en_cours(conn, profile_id)
    message = MESSAGES.get(ok)
    if message and renote:
        message += (f" {renote} offre(s) en cours de renotation "
                    f"(environ {max(1, round(renote * 5.6 / 60))} min, en arrière-plan).")
    return _page(request, p, message=message, en_cours=en_cours)


@router.post("/profils/{profile_id}")
def enregistrer(
    request: Request,
    profile_id: int,
    name: Annotated[str, Form()],
    first_name: Annotated[str, Form()],
    city: Annotated[str, Form()],
    radius_km: Annotated[int, Form()],
    action: Annotated[str, Form()] = "enregistrer",
    keywords: Annotated[str, Form()] = "",
    excluded: Annotated[str, Form()] = "",
    jobs: Annotated[str, Form()] = "",
    rome_codes: Annotated[str, Form()] = "",
    contracts: Annotated[list[str] | None, Form()] = None,
    cv_summary: Annotated[str, Form()] = "",
    adzuna_category: Annotated[str, Form()] = "",
):
    with session() as conn:
        p = _lire(conn, profile_id)
    avant = {k: p[k] for k in ("jobs", "contracts", "cv_summary")}  # ce qui influence la note
    # On garde les valeurs saisies : en cas d'erreur, le formulaire est réaffiché tel quel
    p.update(
        name=name.strip(), first_name=first_name.strip(), city=city.strip(), radius_km=radius_km,
        keywords=_lignes(keywords), excluded=_lignes(excluded), jobs=_lignes(jobs),
        rome_codes=[c.upper() for c in _lignes(rome_codes)],
        contracts=[c for c in CONTRATS if c in (contracts or [])],
        cv_summary=cv_summary.strip(), adzuna_category=adzuna_category.strip() or None,
    )

    # Validation côté serveur : le navigateur se contourne, le serveur non
    if not (p["name"] and p["first_name"] and p["city"]):
        return _page(request, p, erreur="Le nom, le prénom et la ville sont obligatoires.")
    if not 5 <= radius_km <= 100:
        return _page(request, p, erreur="Le rayon doit être compris entre 5 et 100 km.")
    mauvais = [c for c in p["rome_codes"] if not ROME.match(c)]
    if mauvais:
        return _page(request, p, erreur=f"Codes ROME invalides : {', '.join(mauvais)} (format A1234).")
    if (p["adzuna_category"] or "") not in CATEGORIES_ADZUNA:
        return _page(request, p, erreur="Catégorie Adzuna inconnue.")
    statut = {"activer": "active", "pause": "paused"}.get(action, p["status"])
    if statut == "active" and not (p["keywords"] and p["contracts"] and p["cv_summary"]):
        return _page(request, p, erreur="Pour activer un profil, il faut au moins une recherche, "
                                         "un contrat et le CV condensé.")

    dumps = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731
    try:
        with session() as conn:
            conn.execute(
                """UPDATE profiles SET name = ?, first_name = ?, city = ?, radius_km = ?,
                       keywords = ?, excluded = ?, jobs = ?, rome_codes = ?, contracts = ?,
                       cv_summary = ?, adzuna_category = ?, status = ?, updated_at = ?
                   WHERE id = ?""",
                (p["name"], p["first_name"], p["city"], radius_km, dumps(p["keywords"]),
                 dumps(p["excluded"]), dumps(p["jobs"]), dumps(p["rome_codes"]),
                 dumps(p["contracts"]), p["cv_summary"], p["adzuna_category"], statut, _now(), profile_id),
            )
    except sqlite3.IntegrityError:
        return _page(request, p, erreur=f"Un autre profil s'appelle déjà « {p['name']} ».")
    recalculer_exclusions(profile_id)  # les nouveaux mots exclus s'appliquent aussi aux offres déjà collectées
    renotation = 0
    if statut == "active" and any(p[k] != v for k, v in avant.items()):
        # La note dépend des métiers visés, des contrats et du CV condensé : s'ils changent,
        # les notes existantes sont périmées (sauf celles des offres déjà swipées).
        with session() as conn:
            conn.execute("UPDATE matches SET score = NULL, explanation = NULL "
                         "WHERE profile_id = ? AND decision IS NULL", (profile_id,))
        renotation = enqueue_scoring(profile_id)
    ok = {"activer": "active", "pause": "pause"}.get(action, "enregistre")
    return RedirectResponse(f"/profils/{profile_id}?ok={ok}&renote={renotation}", status_code=303)


@router.post("/profils/{profile_id}/analyse")
def relancer(profile_id: int, objectif: Annotated[str, Form()] = ""):
    with session() as conn:
        _lire(conn, profile_id)  # 404 si le profil n'existe pas
        # Une nouvelle proposition de l'IA doit être revalidée : retour en brouillon
        conn.execute("UPDATE profiles SET status = 'draft', updated_at = ? WHERE id = ?", (_now(), profile_id))
    queue.enqueue("profile_from_cv", {"profile_id": profile_id, "objectif": objectif.strip()})
    return RedirectResponse(f"/profils/{profile_id}?ok=analyse", status_code=303)
