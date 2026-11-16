"""Notation des offres : une tâche « score_offer » par couple offre × profil à noter."""
import argparse

from app.db import session
import json

from app.llm import queue

# Poids de chaque critère dans la note, et valeur maximale de chaque critère
POIDS = {"metier": 0.45, "niveau": 0.25, "competences": 0.20, "contrat": 0.10}
MAXI = {"metier": 3, "niveau": 2, "competences": 3, "contrat": 1}


def note_finale(f: dict) -> int:
    """Note sur 10, calculée par le CODE à partir des critères évalués par l'IA.

    Les plafonds sont des règles éliminatoires : un métier sans rapport ou un niveau
    inaccessible ne peut pas être compensé par de bonnes compétences.
    """
    base = 10 * sum(POIDS[k] * f[k] / MAXI[k] for k in POIDS)
    plafond = {0: 1, 1: 4, 2: 10, 3: 10}[f["metier"]]
    plafond = min(plafond, {0: 3, 1: 6, 2: 10}[f["niveau"]])
    if f["contrat"] == 0:
        plafond = min(plafond, 3)
    return int(min(base, plafond) + 0.5)


def recalculer_notes(profile_id: int | None = None) -> int:
    """Recalcule toutes les notes à partir des critères stockés, SANS l'IA (après un changement de poids)."""
    sql = "SELECT offer_id, profile_id, score, factors FROM matches WHERE factors IS NOT NULL"
    args: tuple = ()
    if profile_id is not None:
        sql += " AND profile_id = ?"
        args = (profile_id,)
    changements = 0
    with session() as conn:
        for r in conn.execute(sql, args).fetchall():
            note = note_finale(json.loads(r["factors"]))
            if note != r["score"]:
                conn.execute("UPDATE matches SET score = ? WHERE offer_id = ? AND profile_id = ?",
                             (note, r["offer_id"], r["profile_id"]))
                changements += 1
    return changements


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
    a.add_argument("--recalculer", action="store_true", help="recalcule les notes depuis les critères, sans l'IA")
    args = a.parse_args()
    if args.recalculer:
        print(f"{recalculer_notes(args.profil)} note(s) modifiée(s)")
        return
    print(f"{enqueue_scoring(args.profil, args.limite)} notation(s) mise(s) en file")


if __name__ == "__main__":
    main()
