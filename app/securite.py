"""Garde d'entrée de Kazuo : qui a le droit d'entrer, et d'où peut venir une modification.

Kazuo n'écoute que sur 127.0.0.1 : la seule porte d'entrée depuis l'extérieur est Tailscale Serve.
Serve ajoute à chaque requête l'en-tête X-Forwarded-For, et l'identité de la personne connectée
(Tailscale-User-Login). Il supprime ces en-têtes d'identité s'ils arrivent du client : impossible
de se faire passer pour quelqu'un d'autre.

1) Identité : une requête venue par Tailscale doit porter un compte autorisé (KAZUO_UTILISATEURS).
   Une requête locale (curl lancé sur le serveur lui-même) n'a pas ces en-têtes : elle est acceptée.
2) Requêtes intersites (CSRF) : un site malveillant ouvert sur le téléphone pourrait faire envoyer
   au navigateur un formulaire vers Kazuo ; le réseau Tailscale l'acheminerait avec ton identité.
   Toute requête qui modifie quelque chose (POST…) doit donc venir d'une page de Kazuo lui-même.
   On s'appuie sur l'en-tête Sec-Fetch-Site, que les navigateurs ajoutent et qu'une page ne peut
   pas falsifier ; à défaut, sur l'en-tête Origin. Aucun jeton à gérer dans les formulaires.
"""
import logging
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.config import settings

log = logging.getLogger("kazuo.securite")
METHODES_SANS_EFFET = {"GET", "HEAD", "OPTIONS"}


def _refus(request: Request, motif: str) -> PlainTextResponse:
    log.warning("refusé : %s %s : %s", request.method, request.url.path, motif)
    return PlainTextResponse(f"Accès refusé : {motif}.", status_code=403)


def verifier(request: Request) -> PlainTextResponse | None:
    """Renvoie une réponse 403 si la requête doit être refusée, None si elle peut passer."""
    h = request.headers

    # 1) Identité Tailscale
    if "x-forwarded-for" in h:  # la requête est passée par Tailscale Serve
        login = h.get("tailscale-user-login", "").strip().lower()
        if not login:
            return _refus(request, "appareil sans identité Tailscale")
        if settings.utilisateurs and login not in settings.utilisateurs:
            return _refus(request, f"le compte {login} n'est pas autorisé")

    # 2) Requêtes intersites
    if request.method not in METHODES_SANS_EFFET:
        site = h.get("sec-fetch-site")
        if site is not None:
            if site not in ("same-origin", "none"):
                return _refus(request, "requête envoyée depuis un autre site")
        else:
            origine = h.get("origin")
            if origine is not None and urlsplit(origine).netloc != h.get("host", ""):
                return _refus(request, "origine inattendue")
    return None
