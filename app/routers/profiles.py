"""API des profils : création à partir d'un CV PDF, consultation."""
import json
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.db import session
from app.llm import queue
from app.services import profiles

router = APIRouter(prefix="/api/profiles", tags=["Profils"])
CHAMPS_JSON = ("jobs", "keywords", "excluded", "rome_codes", "contracts")


def _profil(row) -> dict:
    d = dict(row)
    for k in CHAMPS_JSON:
        d[k] = json.loads(d[k])
    d["cv_text_caracteres"] = len(d.pop("cv_text"))  # le texte complet reste en base
    return d


@router.post("")
def creer_profil(
    nom: Annotated[str, Form(description="ex. « Réseau / Cloud »")],
    prenom: Annotated[str, Form()],
    cv: Annotated[UploadFile, File(description="CV au format PDF (5 Mo max)")],
    objectif: Annotated[str, Form(description="une ou deux phrases")] = "",
    ville: Annotated[str, Form()] = "Bordeaux",
    rayon_km: Annotated[int, Form(ge=5, le=100)] = 30,
) -> dict:
    """Crée un profil brouillon et lance l'analyse du CV par l'IA."""
    data = cv.file.read(profiles.MAX_CV_OCTETS + 1)  # on ne lit jamais plus que la limite + 1 octet
    try:
        profile_id, job_id = profiles.create_profile(nom, prenom, objectif, ville, rayon_km, data)
    except profiles.ProfileError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"profile_id": profile_id, "job_id": job_id}


@router.get("")
def lister_profils() -> list[dict]:
    with session() as conn:
        rows = conn.execute("SELECT * FROM profiles ORDER BY id").fetchall()
    return [_profil(r) for r in rows]


@router.get("/{profile_id}")
def detail_profil(profile_id: int) -> dict:
    with session() as conn:
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="profil introuvable")
    return _profil(row)


@router.post("/{profile_id}/analyse")
def relancer_analyse(profile_id: int, objectif: Annotated[str, Form()] = "") -> dict:
    """Relance l'analyse du CV par l'IA (par exemple avec un objectif), sans renvoyer le PDF."""
    with session() as conn:
        if conn.execute("SELECT 1 FROM profiles WHERE id = ?", (profile_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="profil introuvable")
    job_id = queue.enqueue("profile_from_cv", {"profile_id": profile_id, "objectif": objectif.strip()})
    return {"job_id": job_id}
