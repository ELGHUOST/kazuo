"""Source Adzuna : API Jobs (https://developer.adzuna.com).

Quota gratuit : 25 appels/min, 250/jour, 2 500/mois. On reste très en dessous :
au plus MAX_PAGES appels par recherche, espacés de PAUSE_S secondes.
"""
import argparse
import html
import re
import time

import httpx

from app.config import settings
from app.sources.base import Offer

API = "https://api.adzuna.com/v1/api/jobs/fr/search/{page}"
PER_PAGE = 50      # maximum autorisé par Adzuna
MAX_PAGES = 3      # garde-fou : au plus 150 offres et 3 appels par recherche
PAUSE_S = 3        # 3 s entre deux appels → jamais plus de 20 appels/min
CONTRATS = {"permanent": "CDI", "contract": "CDD / contrat"}
_TAG = re.compile(r"<[^>]+>")


class AdzunaError(RuntimeError):
    pass


def _clean(text: str | None) -> str:
    """Retire les balises HTML et décode les entités (&amp; → &)."""
    return html.unescape(_TAG.sub("", text or "")).strip()


def _salary(r: dict) -> str | None:
    lo, hi = r.get("salary_min"), r.get("salary_max")
    if not lo and not hi:
        return None
    lo, hi = int(lo or hi), int(hi or lo)
    # Adzuna dit « annuel », mais certaines offres donnent un salaire mensuel
    # (ex. 1 570 – 2 230) : sous 5 000, on considère que c'est par mois.
    unite = "€/mois" if hi < 5000 else "€/an"
    txt = f"{lo:,} {unite}" if lo == hi else f"{lo:,} – {hi:,} {unite}"
    txt = txt.replace(",", " ")
    if str(r.get("salary_is_predicted")) == "1":
        txt += " (estimation Adzuna)"
    return txt


def _to_offer(r: dict) -> Offer:
    """Traduit une offre Adzuna vers le format commun."""
    contrat = CONTRATS.get(r.get("contract_type"))
    if r.get("contract_time") == "part_time":
        contrat = f"{contrat or 'Contrat'} · temps partiel"
    return Offer(
        source="adzuna",
        source_id=str(r["id"]),
        title=_clean(r.get("title")),
        company=_clean((r.get("company") or {}).get("display_name")) or None,
        location=_clean((r.get("location") or {}).get("display_name")) or None,
        contract=contrat,
        salary=_salary(r),
        description=_clean(r.get("description")),
        url=r.get("redirect_url", ""),
        published_at=r.get("created"),
        raw=r,
    )


def search(what: str, where: str = "Bordeaux", distance_km: int = 30,
           max_days_old: int | None = None) -> list[Offer]:
    """Cherche des offres, page par page, dans la limite des garde-fous."""
    if not (settings.adzuna_app_id and settings.adzuna_app_key):
        raise AdzunaError("ADZUNA_APP_ID / ADZUNA_APP_KEY absents de kazuo.env")
    offers: list[Offer] = []
    headers = {"User-Agent": "Kazuo/0.1 (usage personnel)"}
    with httpx.Client(timeout=20, headers=headers) as client:
        for page in range(1, MAX_PAGES + 1):
            params = {
                "app_id": settings.adzuna_app_id,
                "app_key": settings.adzuna_app_key,
                "what": what,
                "where": where,
                "distance": distance_km,
                "results_per_page": PER_PAGE,
                "sort_by": "date",
            }
            if max_days_old:
                params["max_days_old"] = max_days_old
            r = client.get(API.format(page=page), params=params)
            # On n'affiche jamais r.url : elle contient app_key en clair.
            if r.status_code in (401, 403):
                raise AdzunaError("Identifiants Adzuna refusés")
            if r.status_code == 429:
                raise AdzunaError("Quota Adzuna atteint (HTTP 429)")
            if r.status_code >= 400:
                raise AdzunaError(f"Erreur Adzuna HTTP {r.status_code}")
            data = r.json()
            results = data.get("results", [])
            offers.extend(_to_offer(x) for x in results)
            if len(results) < PER_PAGE or len(offers) >= data.get("count", 0):
                break  # dernière page atteinte : inutile de consommer un appel de plus
            time.sleep(PAUSE_S)
    return offers


def main() -> None:
    p = argparse.ArgumentParser(description="Test en ligne de commande de la source Adzuna")
    p.add_argument("what", help="mots-clés, ex. 'technicien réseau'")
    p.add_argument("--where", default="Bordeaux")
    p.add_argument("--distance", type=int, default=30, help="rayon en km")
    p.add_argument("--jours", type=int, default=None, help="seulement les offres des N derniers jours")
    args = p.parse_args()

    offers = search(args.what, args.where, args.distance, args.jours)
    print(f"{len(offers)} offre(s) pour « {args.what} » autour de {args.where} ({args.distance} km)\n")
    for o in offers:
        print(f"- {o.title} | {o.company or '?'} | {o.location or '?'} | {o.contract or '-'} | {o.salary or '-'}")
        print(f"  publiée {o.published_at} · {o.url}")


if __name__ == "__main__":
    main()
