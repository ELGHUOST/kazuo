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


def fingerprint(title: str, company: str | None, location: str | None = None) -> str:
    """Même titre + même entreprise + même ville (à la casse, aux accents et au « H/F » près).

    La ville est le premier élément du lieu (« Cenon, Bordeaux » → « cenon ») : deux
    restaurants d'une même enseigne restent deux offres distinctes. Contrepartie : une
    même offre localisée « Bordeaux » chez une source et « Gironde » chez une autre
    apparaîtra deux fois. Mieux vaut un doublon visible qu'une offre cachée.
    """
    ville = normalize((location or "").split(",")[0])
    key = f"{normalize(title)}|{normalize(company)}|{ville}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]
