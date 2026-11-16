"""API du swipe : les cartes à trier, la décision (like / nope), l'annulation, les compteurs."""
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import session
from app.web import safe_url

router = APIRouter(prefix="/api/swipe", tags=["Swipe"])


class Decision(BaseModel):
    offer_id: int
    profile_id: int
    decision: Literal["like", "nope"]


class Annulation(BaseModel):
    profile_id: int | None = None


def _filtre_profil(profil: int | None) -> tuple[str, tuple]:
    """Condition SQL facultative sur le profil (texte constant ; la valeur passe par « ? »)."""
    return ("AND m.profile_id = ?", (profil,)) if profil is not None else ("", ())


@router.get("/cartes")
def cartes(profil: int | None = None, note_min: int = 0, limite: int = 20) -> list[dict]:
    """Prochaines cartes : non décidées, non exclues, notées, d'un profil actif ; une par groupe de doublons."""
    filtre, args = _filtre_profil(profil)
    with session() as conn:
        rows = conn.execute(f"""
            SELECT * FROM (
                SELECT o.id AS offer_id, o.title, o.company, o.location, o.contract, o.salary,
                       o.url, o.published_at, substr(o.description, 1, 700) AS description,
                       m.profile_id, p.name AS profil, m.score, m.explanation, m.factors,
                       COUNT(*) OVER (PARTITION BY m.profile_id, o.fingerprint) AS nb,
                       ROW_NUMBER() OVER (PARTITION BY m.profile_id, o.fingerprint
                                          ORDER BY o.published_at DESC, o.id DESC) AS rang
                FROM matches AS m
                JOIN offers AS o ON o.id = m.offer_id
                JOIN profiles AS p ON p.id = m.profile_id
                WHERE p.status = 'active' AND m.decision IS NULL AND m.excluded_by IS NULL
                  AND m.score >= ? {filtre})
            WHERE rang = 1
            ORDER BY score DESC, published_at DESC
            LIMIT ?""", (note_min, *args, min(limite, 100))).fetchall()
    resultat = []
    for r in rows:
        c = dict(r)
        c.pop("rang")
        c["url"] = safe_url(c["url"])
        c["factors"] = json.loads(c["factors"]) if c["factors"] else None
        resultat.append(c)
    return resultat


@router.post("")
def decider(d: Decision) -> dict:
    """Enregistre la décision pour l'offre ET ses doublons (même empreinte) dans ce profil."""
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    with session() as conn:
        n = conn.execute(
            """UPDATE matches SET decision = ?, decided_at = ?
               WHERE profile_id = ? AND decision IS NULL
                 AND offer_id IN (SELECT id FROM offers WHERE fingerprint =
                                  (SELECT fingerprint FROM offers WHERE id = ?))""",
            (d.decision, now, d.profile_id, d.offer_id)).rowcount
    if n == 0:
        raise HTTPException(status_code=404, detail="carte introuvable ou déjà triée")
    return {"maj": n}


@router.post("/annuler")
def annuler(a: Annulation) -> dict:
    """Annule la dernière décision (et celle de ses doublons). Renvoie l'offre concernée."""
    filtre, args = _filtre_profil(a.profile_id)
    with session() as conn:
        der = conn.execute(
            f"""SELECT m.offer_id, m.profile_id, m.decided_at FROM matches AS m
                WHERE m.decision IS NOT NULL {filtre}
                ORDER BY m.decided_at DESC LIMIT 1""", args).fetchone()
        if der is None:
            raise HTTPException(status_code=404, detail="aucune décision à annuler")
        n = conn.execute(
            """UPDATE matches SET decision = NULL, decided_at = NULL
               WHERE profile_id = ? AND decided_at = ?
                 AND offer_id IN (SELECT id FROM offers WHERE fingerprint =
                                  (SELECT fingerprint FROM offers WHERE id = ?))""",
            (der["profile_id"], der["decided_at"], der["offer_id"])).rowcount
    return {"offer_id": der["offer_id"], "profile_id": der["profile_id"], "annulees": n}


@router.get("/stats")
def stats(profil: int | None = None, note_min: int = 0) -> dict:
    """Compteurs par groupe de doublons : à trier, retenues, écartées."""
    filtre, args = _filtre_profil(profil)
    cle = "m.profile_id || ':' || o.fingerprint"
    with session() as conn:
        r = conn.execute(f"""
            SELECT COUNT(DISTINCT CASE WHEN m.decision IS NULL AND m.excluded_by IS NULL
                                            AND m.score >= ? THEN {cle} END) AS a_trier,
                   COUNT(DISTINCT CASE WHEN m.decision = 'like' THEN {cle} END) AS retenues,
                   COUNT(DISTINCT CASE WHEN m.decision = 'nope' THEN {cle} END) AS ecartees
            FROM matches AS m
            JOIN offers AS o ON o.id = m.offer_id
            JOIN profiles AS p ON p.id = m.profile_id
            WHERE p.status = 'active' {filtre}""", (note_min, *args)).fetchone()
    return dict(r)
