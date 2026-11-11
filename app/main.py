"""Kazuo — point d'entrée de l'application web (FastAPI)."""
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db import migrate, session
from app.llm import queue, tasks  # noqa: F401  (tasks enregistre les types de tâches)
from app.routers import llm as llm_router
from app.routers import profiles as profiles_router

TZ = ZoneInfo(settings.tz)

logging.basicConfig(level=logging.INFO, format="%(levelname)-9s %(name)s - %(message)s")
# Sans cette ligne, httpx journaliserait chaque URL appelée, clé Adzuna comprise.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate()      # crée la base ou applique les migrations manquantes
    queue.start()  # démarre le worker IA (un seul thread)
    yield
    queue.stop()   # arrêt propre du worker


app = FastAPI(title="Kazuo", version="0.1.0", lifespan=lifespan)
app.include_router(llm_router.router)
app.include_router(profiles_router.router)
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


def _check_ollama() -> dict:
    """Vérifie qu'Ollama répond (délai court : /health doit rester rapide)."""
    try:
        r = httpx.get(f"{settings.ollama_url}/api/version", timeout=3)
        r.raise_for_status()
        return {"ok": True, "version": r.json().get("version")}
    except (httpx.HTTPError, ValueError) as e:
        return {"ok": False, "erreur": f"{type(e).__name__}: {e}"}


@app.get("/health")
def health() -> dict:
    """État de l'appli et de ses dépendances."""
    with session() as conn:
        nb_offres = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
    return {
        "app": "ok",
        "ollama": _check_ollama(),
        "base": {"offres": nb_offres},
        # « configurée » = valeur présente dans kazuo.env, PAS « identifiants valides »
        "sources_configurees": {
            "france_travail": bool(settings.ft_client_id and settings.ft_client_secret),
            "adzuna": bool(settings.adzuna_app_id and settings.adzuna_app_key),
            "gmail": bool(settings.gmail_address and settings.gmail_app_password),
        },
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Liste des offres : une carte par empreinte, les doublons sont regroupés dessous."""
    with session() as conn:
        total = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
        offres = conn.execute(
            """SELECT * FROM (
                   SELECT o.*,
                          COUNT(*) OVER (PARTITION BY fingerprint) AS nb,
                          ROW_NUMBER() OVER (PARTITION BY fingerprint
                                             ORDER BY published_at DESC, id DESC) AS rang
                   FROM offers AS o)
               WHERE rang = 1
               ORDER BY published_at DESC
               LIMIT 300"""
        ).fetchall()
        groupes: dict[str, list] = {}
        for r in conn.execute(
            "SELECT fingerprint, location, url, published_at FROM offers ORDER BY published_at DESC"
        ):
            groupes.setdefault(r["fingerprint"], []).append(r)
    return templates.TemplateResponse(
        request, "offers.html", {"offres": offres, "groupes": groupes, "total": total}
    )
