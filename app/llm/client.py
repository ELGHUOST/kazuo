"""Client Ollama : un prompt en entrée, une réponse JSON validée par Pydantic en sortie."""
import argparse
import time
from typing import Literal, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> str:
    """Garde l'objet JSON même si le modèle l'entoure de texte ou de ```json."""
    text = text.strip()
    if text.startswith("{"):
        return text
    debut, fin = text.find("{"), text.rfind("}")
    return text[debut:fin + 1] if debut != -1 and fin > debut else text


def chat_json(system: str, user: str, schema: type[T], *, think: str = "low",
              temperature: float = 0.2, use_format: bool = True, max_tokens: int = 800,
              timeout: float = 300) -> tuple[T, dict]:
    """Envoie un prompt à Ollama et renvoie (réponse validée, statistiques).

    - format = schéma JSON : Ollama contraint la génération pour respecter le schéma ;
    - Pydantic revalide : rien de mal formé ne sort de cette fonction ;
    - keep_alive = -1 : le modèle reste chargé, quel que soit le réglage du serveur ;
    - timeout long : après un redémarrage du CT 210, le chargement prend du temps.
    """
    body = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "think": think,
        "stream": False,
        "keep_alive": -1,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    if use_format:
        body["format"] = schema.model_json_schema()

    t0 = time.monotonic()
    try:
        r = httpx.post(f"{settings.ollama_url}/api/chat", json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise LLMError(f"Ollama injoignable ou en erreur : {type(e).__name__}: {e}") from e

    if data.get("done_reason") == "length":
        raise LLMError(f"Réponse tronquée : limite de {max_tokens} jetons atteinte (emballement probable)")
    content = data.get("message", {}).get("content", "")
    try:
        result = schema.model_validate_json(_extract_json(content))
    except ValidationError as e:
        raise LLMError(f"Réponse non conforme au schéma : {e.errors()[:3]} | début : {content[:200]!r}") from e

    def _vitesse(n, ns):
        return round(n / (ns / 1e9), 1) if n and ns else None

    stats = {
        "duree_s": round(time.monotonic() - t0, 2),
        "tokens_prompt": data.get("prompt_eval_count"),
        "lecture_tok_s": _vitesse(data.get("prompt_eval_count"), data.get("prompt_eval_duration")),
        "tokens_generes": data.get("eval_count"),  # réflexion + réponse
        "generation_tok_s": _vitesse(data.get("eval_count"), data.get("eval_duration")),
    }
    return result, stats


# ---------------------------------------------------------------------------
# Test en ligne de commande : analyse d'une offre en base
# ---------------------------------------------------------------------------
SYSTEME_TEST = (
    "Tu analyses des offres d'emploi en France. Réponds uniquement avec un objet JSON "
    "conforme au schéma fourni. N'invente rien : si une information n'est pas dans "
    "l'offre, utilise « inconnu »."
)


class AnalyseOffre(BaseModel):
    metier: str = Field(description="intitulé de métier court et normalisé")
    contrat: Literal["CDI", "CDD", "intérim", "alternance", "stage", "freelance", "inconnu"]
    niveau_etudes: Literal["aucun", "CAP/BEP", "Bac", "Bac+2", "Bac+3", "Bac+5", "inconnu"]
    competences: list[str] = Field(max_length=8, description="compétences demandées, 8 maximum")
    resume: str = Field(description="une phrase de 25 mots maximum")


def main() -> None:
    p = argparse.ArgumentParser(description="Test du client Ollama sur une offre en base")
    p.add_argument("--offre", type=int, help="id de l'offre (par défaut : la plus récente)")
    p.add_argument("--sans-format", action="store_true", help="sans contrainte de schéma (diagnostic)")
    args = p.parse_args()

    from app.db import session  # import local : le client lui-même ne dépend pas de la base

    with session() as conn:
        if args.offre:
            row = conn.execute("SELECT * FROM offers WHERE id = ?", (args.offre,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM offers ORDER BY published_at DESC LIMIT 1").fetchone()
    if row is None:
        raise SystemExit("Offre introuvable")

    user = (f"Titre : {row['title']}\nEntreprise : {row['company']}\n"
            f"Description : {row['description'][:1500]}")
    print(f"Offre {row['id']} : {row['title']} | {row['company']} | contrat en base : {row['contract']}\n")
    result, stats = chat_json(SYSTEME_TEST, user, AnalyseOffre, use_format=not args.sans_format)
    print(result.model_dump_json(indent=2))
    print(f"\nStatistiques : {stats}")


if __name__ == "__main__":
    main()
