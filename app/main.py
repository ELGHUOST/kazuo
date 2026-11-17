"""Kazuo — point d'entrée de l'application web (FastAPI)."""
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db import migrate, session
from app.llm import queue, tasks  # noqa: F401  (tasks enregistre les types de tâches)
from app.routers import llm as llm_router
from app.routers import pages_profils
from app.routers import profiles as profiles_router
from app.routers import swipe as swipe_router
from app.routers import pages_swipe
from app.web import templates

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
app.include_router(swipe_router.router)
app.include_router(pages_swipe.router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(profiles_router.router)
app.include_router(pages_profils.router)


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
def index(request: Request, profil: int | None = None, exclues: bool = False):
    """Une carte par empreinte. Sans filtre : toutes les offres, par date.
    Avec ?profil=N : les offres de ce profil, triées par note (&exclues=1 : les offres écartées)."""
    if profil is None:
        source, args = ("SELECT *, NULL AS score, NULL AS explication, NULL AS facteurs, NULL AS decision "
                        "FROM offers"), ()
        tri = "published_at DESC"
    else:
        cond = "IS NOT NULL" if exclues else "IS NULL"  # constante choisie ici ; la valeur passe par « ? »
        source = ("SELECT o.*, m.score AS score, m.explanation AS explication, m.factors AS facteurs, "
                  "m.decision AS decision FROM offers AS o "
                  f"JOIN matches AS m ON m.offer_id = o.id AND m.profile_id = ? WHERE m.excluded_by {cond}")
        args = (profil,)
        tri = "score DESC NULLS LAST, published_at DESC"
    with session() as conn:
        total = conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
        profils = conn.execute("SELECT id, name, status FROM profiles ORDER BY id").fetchall()
        nb_exclues = 0
        motifs: dict[int, str] = {}
        if profil is not None:
            nb_exclues = conn.execute(
                "SELECT COUNT(*) FROM matches WHERE profile_id = ? AND excluded_by IS NOT NULL", (profil,)
            ).fetchone()[0]
            motifs = {r["offer_id"]: r["excluded_by"] for r in conn.execute(
                "SELECT offer_id, excluded_by FROM matches WHERE profile_id = ? AND excluded_by IS NOT NULL",
                (profil,))}
        offres = conn.execute(
            f"""SELECT * FROM (
                    SELECT s.*,
                           COUNT(*) OVER (PARTITION BY fingerprint) AS nb,
                           ROW_NUMBER() OVER (PARTITION BY fingerprint
                                              ORDER BY published_at DESC, id DESC) AS rang
                    FROM ({source}) AS s)
                WHERE rang = 1
                ORDER BY {tri}
                LIMIT 300""", args).fetchall()
        groupes: dict[str, list] = {}
        for r in conn.execute(
            f"SELECT fingerprint, location, url, published_at FROM ({source}) ORDER BY published_at DESC", args
        ):
            groupes.setdefault(r["fingerprint"], []).append(r)
    return templates.TemplateResponse(request, "offers.html", {
        "offres": offres, "groupes": groupes, "total": total, "profils": profils,
        "profil": profil, "exclues": exclues, "nb_exclues": nb_exclues, "motifs": motifs,
    })
