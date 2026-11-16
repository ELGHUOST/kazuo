"""Page du swipe : l'interface mobile. Les données passent par l'API /api/swipe."""
from fastapi import APIRouter, Request

from app.db import session
from app.web import templates

router = APIRouter(include_in_schema=False)

NOTES_MIN = (0, 5, 7)


@router.get("/swipe")
def page_swipe(request: Request, profil: str = "", note_min: int = 5):
    with session() as conn:
        profils = conn.execute(
            "SELECT id, name FROM profiles WHERE status = 'active' ORDER BY id").fetchall()
    # « Tous les profils » envoie profil= (vide) : déclaré en int, il provoquerait une erreur 422
    pid = int(profil) if profil.isdigit() else None
    if pid not in {p["id"] for p in profils}:
        pid = None
    return templates.TemplateResponse(request, "swipe.html", {
        "profils": profils, "profil": pid,
        "note_min": note_min if note_min in NOTES_MIN else 5})
