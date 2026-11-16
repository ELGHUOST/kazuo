"""Notation des offres : une tâche « score_offer » par couple offre × profil à noter."""
import argparse

from app.db import session
from app.llm import queue


def enqueue_scoring(profile_id: int | None = None, limite: int | None = None) -> int:
    """Met en file la notation des offres retenues (non exclues), pas encore notées,
    sans tâche déjà en attente, pour les profils actifs. Les plus récentes d'abord :
    si la file est interrompue, ce sont les offres fraîches qui auront été notées."""
    sql = """SELECT m.offer_id, m.profile_id
             FROM matches AS m
             JOIN offers AS o ON o.id = m.offer_id
             JOIN profiles AS p ON p.id = m.profile_id
             WHERE m.excluded_by IS NULL AND m.score IS NULL AND p.status = 'active'
               AND NOT EXISTS (
                   SELECT 1 FROM llm_jobs AS j
                   WHERE j.kind = 'score_offer' AND j.status IN ('pending', 'running')
                     AND json_extract(j.payload, '$.offer_id') = m.offer_id
                     AND json_extract(j.payload, '$.profile_id') = m.profile_id)"""
    args: list = []
    if profile_id is not None:
        sql += " AND m.profile_id = ?"
        args.append(profile_id)
    sql += " ORDER BY o.published_at DESC"
    if limite:
        sql += " LIMIT ?"
        args.append(limite)
    with session() as conn:
        couples = conn.execute(sql, args).fetchall()
    for c in couples:
        queue.enqueue("score_offer", {"offer_id": c["offer_id"], "profile_id": c["profile_id"]})
    return len(couples)


def main() -> None:
    a = argparse.ArgumentParser(description="Met en file la notation des offres non notées")
    a.add_argument("--profil", type=int, help="un seul profil (id)")
    a.add_argument("--limite", type=int, help="au plus N offres (pour tester)")
    args = a.parse_args()
    print(f"{enqueue_scoring(args.profil, args.limite)} notation(s) mise(s) en file")


if __name__ == "__main__":
    main()
