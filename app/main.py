"""Kazuo — point d'entrée de l'application web (FastAPI)."""
import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.config import settings

app = FastAPI(title="Kazuo", version="0.1.0")


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
    return {
        "app": "ok",
        "ollama": _check_ollama(),
        # « configurée » = valeur présente dans kazuo.env, PAS « identifiants valides »
        "sources_configurees": {
            "france_travail": bool(settings.ft_client_id and settings.ft_client_secret),
            "adzuna": bool(settings.adzuna_app_id and settings.adzuna_app_key),
            "gmail": bool(settings.gmail_address and settings.gmail_app_password),
        },
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (
        "<h1>Kazuo</h1>"
        "<p>Le squelette fonctionne.</p>"
        "<ul><li><a href='/health'>/health</a> — état de l'appli</li>"
        "<li><a href='/docs'>/docs</a> — documentation interactive de l'API</li></ul>"
    )
