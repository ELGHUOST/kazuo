"""Sonde Adzuna : compare le volume de plusieurs recherches.

Un appel par recherche, un seul résultat demandé, rien d'enregistré.
Sert à régler les recherches d'un profil à partir de chiffres réels.
"""
import argparse
import time

import httpx

from app.config import settings
from app.sources.adzuna import API, PAUSE_S

AUTH = {"app_id": settings.adzuna_app_id, "app_key": settings.adzuna_app_key}
CATEGORIES = "https://api.adzuna.com/v1/api/jobs/fr/categories"


def compter(what: str, where: str, distance: int, jours: int | None, categorie: str | None) -> str:
    params = {**AUTH, "what": what, "where": where, "distance": distance, "results_per_page": 1}
    if jours:
        params["max_days_old"] = jours
    if categorie:
        params["category"] = categorie
    r = httpx.get(API.format(page=1), params=params, timeout=20)
    return str(r.json().get("count", 0)) if r.status_code == 200 else f"HTTP {r.status_code}"


def main() -> None:
    a = argparse.ArgumentParser(description="Compare le volume de plusieurs recherches Adzuna")
    a.add_argument("recherches", nargs="*")
    a.add_argument("--where", default="Bordeaux")
    a.add_argument("--distance", type=int, default=30)
    a.add_argument("--jours", type=int, default=30)
    a.add_argument("--categorie", help="ex. it-jobs (voir --categories)")
    a.add_argument("--categories", action="store_true", help="liste les catégories Adzuna France")
    args = a.parse_args()

    if args.categories:
        for c in httpx.get(CATEGORIES, params=AUTH, timeout=20).json().get("results", []):
            print(f"{c['tag']:<35} {c['label']}")
        return
    print(f"Offres sur {args.jours} jours, {args.distance} km autour de {args.where}"
          f"{', catégorie ' + args.categorie if args.categorie else ''} :")
    for i, q in enumerate(args.recherches):
        if i:
            time.sleep(PAUSE_S)
        print(f"{compter(q, args.where, args.distance, args.jours, args.categorie):>6}  {q}")


if __name__ == "__main__":
    main()
