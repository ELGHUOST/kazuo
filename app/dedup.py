"""Empreinte d'une offre, pour repérer la même offre publiée plusieurs fois."""
import hashlib
import re
import unicodedata

_GENRE = re.compile(r"\b[hf]\s*[/-]\s*[hf]\b")   # h/f, f/h, h-f, (H/F)...
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text: str | None) -> str:
    """'Technicien Réseaux (H/F)' → 'technicien reseaux'."""
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    t = _GENRE.sub(" ", t)
    t = _NON_ALNUM.sub(" ", t)
    return " ".join(t.split())


def fingerprint(title: str, company: str | None) -> str:
    """Même titre + même entreprise (à la casse, aux accents et au « H/F » près) = même empreinte.

    La ville est volontairement ignorée : une même offre apparaît souvent avec
    « Bordeaux » chez une source et « Gironde » chez une autre.
    """
    key = f"{normalize(title)}|{normalize(company)}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]
