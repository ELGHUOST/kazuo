"""Pages web des profils : liste, correction, validation, pause, relance de l'analyse."""
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db import session
from app.llm import queue
from app.web import templates

router = APIRouter(include_in_schema=False)  # pages HTML : absentes de /docs

LIBELLES = {"draft": "brouillon", "active": "actif", "paused": "en pause"}
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
        {"p": p, "libelles": LIBELLES, "contrats_possibles": CONTRATS, "categories": CATEGORIES_ADZUNA,
         "analyse_en_cours": en_cours, "erreur": erreur, "message": message},
        status_code=400 if erreur else 200,
    )


@router.get("/profils", response_class=HTMLResponse)
def liste(request: Request):
    with session() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM profiles ORDER BY id").fetchall()]
        profils = [_lire(conn, i) for i in ids]
    return templates.TemplateResponse(request, "profiles.html", {"profils": profils, "libelles": LIBELLES})


@router.get("/profils/{profile_id}", response_class=HTMLResponse)
def detail(request: Request, profile_id: int, ok: str | None = None):
    with session() as conn:
        p = _lire(conn, profile_id)
        en_cours = _analyse_en_cours(conn, profile_id)
    return _page(request, p, message=MESSAGES.get(ok), en_cours=en_cours)


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
    ok = {"activer": "active", "pause": "pause"}.get(action, "enregistre")
    return RedirectResponse(f"/profils/{profile_id}?ok={ok}", status_code=303)


@router.post("/profils/{profile_id}/analyse")
def relancer(profile_id: int, objectif: Annotated[str, Form()] = ""):
    with session() as conn:
        _lire(conn, profile_id)  # 404 si le profil n'existe pas
        # Une nouvelle proposition de l'IA doit être revalidée : retour en brouillon
        conn.execute("UPDATE profiles SET status = 'draft', updated_at = ? WHERE id = ?", (_now(), profile_id))
    queue.enqueue("profile_from_cv", {"profile_id": profile_id, "objectif": objectif.strip()})
    return RedirectResponse(f"/profils/{profile_id}?ok=analyse", status_code=303)
