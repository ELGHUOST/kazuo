"""Retraitement : recalcule les champs des offres à partir de raw_json, sans rappeler les API.

À lancer après chaque amélioration d'un adaptateur ou de l'empreinte.
"""
import json

from app.db import session
from app.dedup import fingerprint
from app.sources import adzuna

ADAPTATEURS = {"adzuna": adzuna._to_offer}
CHAMPS = ("title", "company", "location", "contract", "salary", "description", "url", "published_at")


def main() -> None:
    vus = modifies = 0
    with session() as conn:
        for r in conn.execute("SELECT * FROM offers WHERE raw_json IS NOT NULL").fetchall():
            to_offer = ADAPTATEURS.get(r["source"])
            if to_offer is None:
                continue
            o = to_offer(json.loads(r["raw_json"]))
            nouveau = {c: getattr(o, c) for c in CHAMPS}
            nouveau["fingerprint"] = fingerprint(o.title, o.company, o.location)
            vus += 1
            if any(r[c] != v for c, v in nouveau.items()):
                modifies += 1
                # Les noms de colonnes viennent de CHAMPS (une constante du code), jamais
                # d'une donnée externe : seules les valeurs passent par des « ? ».
                conn.execute(
                    f"UPDATE offers SET {', '.join(f'{c} = ?' for c in nouveau)} WHERE id = ?",
                    (*nouveau.values(), r["id"]),
                )
    print(f"{vus} offres retraitées, {modifies} modifiées")


if __name__ == "__main__":
    main()
