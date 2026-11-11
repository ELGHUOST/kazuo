"""Format commun des offres : chaque source convertit ses données vers ce format."""
from dataclasses import dataclass, field


@dataclass
class Offer:
    source: str                 # "adzuna", "francetravail", "linkedin"...
    source_id: str              # identifiant de l'offre chez la source
    title: str
    company: str | None
    location: str | None
    contract: str | None
    salary: str | None
    description: str
    url: str
    published_at: str | None    # ISO 8601, en UTC
    raw: dict = field(default_factory=dict, repr=False)  # réponse brute de la source
