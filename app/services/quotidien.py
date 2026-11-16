"""Routine du matin : préchauffage du modèle, collecte des offres récentes, mise en file de la notation.

Lancée chaque jour à 7h30 par le timer systemd kazuo-collect.timer (et à la main pour tester).
Code de sortie 1 si la collecte est interrompue : systemd réessaie alors 30 minutes plus tard.
"""
import logging
import sys

import httpx

from app.config import settings
from app.services.collect import CollecteInterrompue, run
from app.services.scoring import enqueue_scoring

log = logging.getLogger("kazuo.quotidien")
JOURS = 2  # fenêtre de recouvrement : 2 jours, pour ne rien rater si une collecte a sauté


def prechauffer() -> None:
    """Charge le modèle dans Ollama (requête sans prompt) pour que la notation démarre aussitôt."""
    try:
        httpx.post(f"{settings.ollama_url}/api/generate",
                   json={"model": settings.ollama_model, "keep_alive": -1}, timeout=300)
        log.info("modèle %s chargé", settings.ollama_model)
    except httpx.HTTPError as e:
        # Non bloquant : les notations attendront dans la file que l'IA revienne
        log.warning("préchauffage impossible : %s", e)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s - %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # sinon la clé Adzuna apparaîtrait dans les URL
    prechauffer()
    try:
        bilan = run(jours=JOURS)
    except CollecteInterrompue as e:
        log.error("collecte interrompue : %s", e)
        return 1
    nouvelles = sum(b.get("nouvelles", 0) for b in bilan)
    rattachees = sum(b.get("rattachees", 0) for b in bilan)
    erreurs = sum(1 for b in bilan if "erreur" in b)
    log.info("collecte : %s recherche(s), %s offre(s) nouvelle(s), %s rattachée(s), %s erreur(s)",
             len(bilan), nouvelles, rattachees, erreurs)
    log.info("%s notation(s) mise(s) en file", enqueue_scoring())
    return 0


if __name__ == "__main__":
    sys.exit(main())
