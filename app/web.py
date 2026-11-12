"""Outils communs aux pages web : moteur de templates et filtres d'affichage."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.config import settings

TZ = ZoneInfo(settings.tz)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def paris(iso: str | None) -> str:
    """'2026-11-11T04:12:42Z' (UTC) → '11/11/2026 05:12' (heure de Paris)."""
    if not iso:
        return "date inconnue"
    try:
        return datetime.fromisoformat(iso).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso


def safe_url(url: str | None) -> str:
    """N'autorise que les liens http(s) : une source ne doit pas pouvoir injecter un lien javascript:."""
    return url if url and url.startswith(("https://", "http://")) else "#"


templates.env.filters["paris"] = paris
templates.env.filters["safe_url"] = safe_url
