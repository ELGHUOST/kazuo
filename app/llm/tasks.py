"""Types de tâches IA. Chaque fonction reçoit le payload (dict) et renvoie un résultat (dict)."""
from app.db import session
from app.llm.client import SYSTEME_TEST, AnalyseOffre, chat_json
from app.llm.queue import handler


@handler("analyse_offre")
def analyse_offre(payload: dict) -> dict:
    """Tâche de test : analyse une offre en base (contrat, niveau, compétences)."""
    with session() as conn:  # connexion refermée AVANT l'appel à l'IA
        row = conn.execute(
            "SELECT title, company, description FROM offers WHERE id = ?", (payload["offer_id"],)
        ).fetchone()
    if row is None:
        raise ValueError(f"offre {payload['offer_id']} introuvable")
    user = (f"Titre : {row['title']}\nEntreprise : {row['company']}\n"
            f"Description : {row['description'][:1500]}")
    result, stats = chat_json(SYSTEME_TEST, user, AnalyseOffre, max_tokens=500)
    return {"analyse": result.model_dump(), "stats": stats}


# ---------------------------------------------------------------------------
# Fiche de profil depuis le CV
# ---------------------------------------------------------------------------
import json
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

CodeRome = Annotated[str, StringConstraints(pattern=r"^[A-N][0-9]{4}$")]


class FicheProfil(BaseModel):
    metiers: list[str] = Field(min_length=1, max_length=6, description="intitulés de métiers visés")
    recherches: list[str] = Field(
        min_length=3, max_length=8,
        description="requêtes à taper dans un moteur d'offres : intitulés de poste de 1 à 4 mots, "
                    "jamais un nom de technologie")
    mots_exclus: list[str] = Field(
        max_length=8,
        description="termes désignant un AUTRE métier que celui visé ; jamais un niveau, "
                    "un contrat ni un mode de travail")
    codes_rome: list[CodeRome] = Field(max_length=5, description="codes ROME, seulement si certain")
    contrats: list[Literal["CDI", "CDD", "intérim", "alternance", "stage", "freelance"]] = Field(min_length=1)
    cv_condense: str = Field(description="résumé factuel du CV seul, 120 mots maximum")

    @field_validator("metiers", "recherches", "mots_exclus", "codes_rome", "contrats")
    @classmethod
    def sans_doublons(cls, valeurs: list[str]) -> list[str]:
        """La grammaire n'empêche pas les doublons : on les retire ici, sans tenir compte de la casse."""
        vus, propres = set(), []
        for v in valeurs:
            cle = v.strip().lower()
            if cle and cle not in vus:
                vus.add(cle)
                propres.append(v.strip())
        return propres


SYSTEME_PROFIL = (
    "Tu aides un candidat à paramétrer une recherche d'emploi automatique en France. "
    "À partir de son CV et de l'objectif du profil, propose une fiche. Règles : "
    "1) N'invente rien : uniquement ce que disent le CV et l'objectif. "
    "2) « recherches » : des intitulés de poste tels qu'on les tape dans un moteur d'offres "
    "(ex. « technicien systèmes et réseaux », « administrateur réseau »), jamais un nom de logiciel "
    "ou de technologie. Si l'objectif mentionne l'alternance, ajoute une ou deux recherches "
    "contenant le mot « alternance ». "
    "3) « mots_exclus » : peu nombreux, uniquement des termes qui désignent un AUTRE métier que celui "
    "visé (ex. pour un profil réseaux informatiques : « eau potable », « électricien », « plomberie »). "
    "Jamais un niveau (senior, junior), un type de contrat ni un mode de travail (télétravail). "
    "En cas de doute, n'exclus pas. "
    "4) « contrats » : conformes à l'objectif ; l'alternance seulement si l'objectif la mentionne. "
    "5) Codes ROME : seulement si tu en es certain, sinon une liste vide. "
    "6) « cv_condense » : résumé factuel du CV SEUL (formation, expériences, compétences, "
    "certifications, langues), 120 mots maximum, sans coordonnées personnelles, sans adjectif "
    "valorisant, sans reprendre l'objectif. "
    "Réponds uniquement avec le JSON demandé."
)


@handler("profile_from_cv")
def profile_from_cv(payload: dict) -> dict:
    with session() as conn:
        p = conn.execute("SELECT name, city, cv_text FROM profiles WHERE id = ?",
                         (payload["profile_id"],)).fetchone()
    if p is None:
        raise ValueError(f"profil {payload['profile_id']} introuvable")
    user = (f"Nom du profil : {p['name']}\n"
            f"Objectif : {payload.get('objectif') or 'non précisé'}\n"
            f"Ville : {p['city']}\n\nCV :\n{p['cv_text'][:12000]}")
    fiche, stats = chat_json(SYSTEME_PROFIL, user, FicheProfil, max_tokens=1500)

    dumps = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731
    with session() as conn:
        conn.execute(
            """UPDATE profiles SET jobs = ?, keywords = ?, excluded = ?, rome_codes = ?,
                                   contracts = ?, cv_summary = ?, updated_at = ?
               WHERE id = ?""",
            (dumps(fiche.metiers), dumps(fiche.recherches), dumps(fiche.mots_exclus),
             dumps(fiche.codes_rome), dumps(fiche.contrats), fiche.cv_condense,
             datetime.now(timezone.utc).isoformat(timespec="seconds"), payload["profile_id"]),
        )
    return {"fiche": fiche.model_dump(), "stats": stats}
