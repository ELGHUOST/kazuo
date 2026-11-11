"""API de la file d'attente IA : mise en file de tests et suivi des tâches."""
import json

from fastapi import APIRouter, HTTPException

from app.db import session
from app.llm import queue

router = APIRouter(prefix="/api/llm", tags=["IA"])


@router.post("/test/{offer_id}")
def mettre_en_file_test(offer_id: int, priority: int = 10) -> dict:
    """Met en file une analyse de l'offre (tâche de test)."""
    return {"job_id": queue.enqueue("analyse_offre", {"offer_id": offer_id}, priority=priority)}


@router.get("/jobs")
def lister_taches(limit: int = 20) -> list[dict]:
    """Les dernières tâches, sans leur contenu."""
    with session() as conn:
        rows = conn.execute(
            """SELECT id, kind, priority, status, attempts, created_at, started_at,
                      finished_at, duration_s, error
               FROM llm_jobs ORDER BY id DESC LIMIT ?""", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/jobs/{job_id}")
def detail_tache(job_id: int) -> dict:
    """Une tâche complète, avec sa charge utile et son résultat."""
    with session() as conn:
        row = conn.execute("SELECT * FROM llm_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="tâche introuvable")
    d = dict(row)
    for k in ("payload", "result"):
        if d[k]:
            d[k] = json.loads(d[k])
    return d
