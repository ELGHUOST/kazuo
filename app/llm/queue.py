"""File d'attente des tâches IA : une table SQLite + un seul thread qui les traite une par une.

- persistante : une tâche en attente survit à un redémarrage de Kazuo ;
- prioritaire : la lettre que tu attends passe devant la notation de masse ;
- sérialisée : Ollama ne reçoit jamais deux requêtes à la fois.
"""
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

from app.db import session
from app.llm.client import LLMError

log = logging.getLogger("kazuo.llm")

PRIORITES = {"cover_letter": 100, "profile_from_cv": 90, "classify_reply": 50, "score_offer": 10}
MAX_TENTATIVES = 3      # erreurs passagères (Ollama indisponible, JSON invalide) : 3 essais
PAUSE_ERREUR_S = 10     # attente après un échec, pour laisser Ollama se rétablir
ATTENTE_VIDE_S = 2      # file vide : on regarde de nouveau toutes les 2 s

HANDLERS: dict[str, Callable[[dict], dict]] = {}
_stop = threading.Event()
_thread: threading.Thread | None = None


def handler(kind: str):
    """Décorateur : @handler("score_offer") associe une fonction à un type de tâche."""
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def enqueue(kind: str, payload: dict, priority: int | None = None) -> int:
    """Ajoute une tâche à la file et renvoie son id."""
    prio = priority if priority is not None else PRIORITES.get(kind, 10)
    with session() as conn:
        cur = conn.execute(
            "INSERT INTO llm_jobs (kind, payload, priority, created_at) VALUES (?, ?, ?, ?)",
            (kind, json.dumps(payload, ensure_ascii=False), prio, _now()),
        )
        return cur.lastrowid


def _claim():
    """Prend la tâche en attente la plus prioritaire et la passe en 'running', en une seule requête."""
    with session() as conn:
        rows = conn.execute(
            """UPDATE llm_jobs
               SET status = 'running', started_at = ?, attempts = attempts + 1
               WHERE id = (SELECT id FROM llm_jobs WHERE status = 'pending'
                           ORDER BY priority DESC, id LIMIT 1)
               RETURNING id, kind, payload, attempts""",
            (_now(),),
        ).fetchall()
    return rows[0] if rows else None


def _finish(job_id: int, *, result: dict | None = None, error: str | None = None,
            retry: bool = False, duree: float | None = None) -> None:
    status = "pending" if retry else ("error" if error else "done")
    with session() as conn:
        conn.execute(
            """UPDATE llm_jobs SET status = ?, result = ?, error = ?, finished_at = ?, duration_s = ?
               WHERE id = ?""",
            (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
             error, None if retry else _now(), duree, job_id),
        )


def _process_one() -> bool:
    """Traite une tâche. Renvoie False si la file était vide."""
    job = _claim()
    if job is None:
        return False
    jid, kind, attempts = job["id"], job["kind"], job["attempts"]
    fn = HANDLERS.get(kind)
    if fn is None:
        _finish(jid, error=f"type de tâche inconnu : {kind}")
        return True
    t0 = time.monotonic()
    try:
        result = fn(json.loads(job["payload"]))
    except LLMError as e:
        # Erreur passagère probable : remise en attente, dans la limite de MAX_TENTATIVES
        retry = attempts < MAX_TENTATIVES
        log.warning("tâche %s (%s), tentative %s/%s : %s", jid, kind, attempts, MAX_TENTATIVES, e)
        _finish(jid, error=str(e), retry=retry)
        _stop.wait(PAUSE_ERREUR_S)
        return True
    except Exception as e:
        # Bug ou donnée invalide : réessayer ne changerait rien
        log.exception("tâche %s (%s) en échec définitif", jid, kind)
        _finish(jid, error=f"{type(e).__name__}: {e}")
        return True
    duree = round(time.monotonic() - t0, 2)
    _finish(jid, result=result, duree=duree)
    log.info("tâche %s (%s) terminée en %.1f s", jid, kind, duree)
    return True


def _recover() -> None:
    """Au démarrage, une tâche encore 'running' vient d'un arrêt brutal : on la remet en attente."""
    with session() as conn:
        n = conn.execute("UPDATE llm_jobs SET status = 'pending' WHERE status = 'running'").rowcount
    if n:
        log.warning("%s tâche(s) interrompue(s) remise(s) en attente", n)


def _loop() -> None:
    _recover()
    log.info("worker IA démarré")
    while not _stop.is_set():
        try:
            if not _process_one():
                _stop.wait(ATTENTE_VIDE_S)
        except Exception:
            log.exception("erreur inattendue du worker IA")
            _stop.wait(PAUSE_ERREUR_S)
    log.info("worker IA arrêté")


def start() -> None:
    global _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="llm-worker", daemon=True)
    _thread.start()


def stop(timeout: float = 5) -> None:
    _stop.set()
    if _thread is not None:
        _thread.join(timeout)
