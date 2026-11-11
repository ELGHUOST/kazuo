"""Types de tâches IA. Chaque fonction reçoit le payload (dict) et renvoie un résultat (dict)."""
from app.db import session
from app.llm.client import SYSTEME_TEST, AnalyseOffre, chat_json
from app.llm.queue import handler


@handler("analyse_offre")
def analyse_offre(payload: dict) -> dict:
    """Tâche de test : analyse une offre en base (contrat, niveau, compétences)."""
    with session() as conn:  # connexion refermée AVANT l'appel à l'IA
        row = conn.execute(
            "SELECT title, company, description FROM offers WHERE id = ?", (payload["offer_id"],)
        ).fetchone()
    if row is None:
        raise ValueError(f"offre {payload['offer_id']} introuvable")
    user = (f"Titre : {row['title']}\nEntreprise : {row['company']}\n"
            f"Description : {row['description'][:1500]}")
    result, stats = chat_json(SYSTEME_TEST, user, AnalyseOffre)
    return {"analyse": result.model_dump(), "stats": stats}
