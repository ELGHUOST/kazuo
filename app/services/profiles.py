"""Profils de recherche : enregistrement du CV, extraction du texte, création du brouillon."""
import io
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from pypdf import PdfReader

from app.config import settings
from app.db import session
from app.llm import queue

CV_DIR = settings.data_dir / "cv"
MAX_CV_OCTETS = 5 * 1024 * 1024  # 5 Mo
MIN_TEXTE = 200                  # en dessous : PDF scanné (image), sans texte exploitable


class ProfileError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def extract_text(pdf: bytes) -> str:
    """Texte brut du PDF, espaces normalisés. Refuse les PDF sans texte (scans)."""
    try:
        reader = PdfReader(io.BytesIO(pdf))
        texte = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:  # pypdf lève des erreurs variées sur un fichier abîmé
        raise ProfileError(f"PDF illisible : {type(e).__name__}") from e
    texte = re.sub(r"[ \t]+", " ", texte)
    texte = re.sub(r"\n{3,}", "\n\n", texte).strip()
    if len(texte) < MIN_TEXTE:
        raise ProfileError("Presque aucun texte dans ce PDF : est-ce un scan ? "
                           "Exporte ton CV en PDF depuis ton traitement de texte.")
    return texte


def create_profile(name: str, first_name: str, objectif: str, city: str,
                   radius_km: int, pdf: bytes) -> tuple[int, int]:
    """Enregistre le CV, crée le profil brouillon et met en file la tâche IA."""
    name, first_name = name.strip(), first_name.strip()
    if not name or not first_name:
        raise ProfileError("Le nom du profil et le prénom sont obligatoires")
    if len(pdf) > MAX_CV_OCTETS:
        raise ProfileError("CV trop volumineux (5 Mo maximum)")
    if not pdf.startswith(b"%PDF-"):  # on vérifie le contenu, pas l'extension
        raise ProfileError("Ce fichier n'est pas un PDF")
    texte = extract_text(pdf)

    chemin = CV_DIR / f"{uuid.uuid4().hex}.pdf"  # nom généré : jamais celui fourni par le client
    chemin.write_bytes(pdf)
    now = _now()
    try:
        with session() as conn:
            cur = conn.execute(
                """INSERT INTO profiles (name, first_name, cv_path, cv_text, city, radius_km,
                                         created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, first_name, str(chemin), texte, city.strip() or "Bordeaux",
                 radius_km, now, now),
            )
            profile_id = cur.lastrowid
    except sqlite3.IntegrityError:
        chemin.unlink(missing_ok=True)
        raise ProfileError(f"Un profil « {name} » existe déjà")
    # Mise en file APRÈS la validation du profil en base : la tâche ne peut pas
    # pointer vers un profil qui n'existerait pas.
    job_id = queue.enqueue("profile_from_cv", {"profile_id": profile_id, "objectif": objectif.strip()})
    return profile_id, job_id
