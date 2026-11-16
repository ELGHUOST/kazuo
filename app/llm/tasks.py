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


class Experience(BaseModel):
    poste: str
    employeur: str
    realisation: str = Field(description="ce qui a été fait, en une ligne")


class CvCondense(BaseModel):
    """Extrait structuré du CV. Aucun champ pour un nom ou des coordonnées : impossible de les y mettre."""
    formation: list[str] = Field(max_length=3, description="diplôme, établissement, statut exact (obtenu / en cours)")
    experiences: list[Experience] = Field(max_length=4)
    competences: list[str] = Field(max_length=15, description="compétences techniques, 1 à 3 mots chacune")
    certifications: list[str] = Field(max_length=6)
    langues: list[str] = Field(max_length=6, description="langue et niveau, ex. « anglais C1 »")

    def en_texte(self) -> str:
        """Version compacte, celle qui sera envoyée dans les prompts de notation."""
        lignes = [
            "Formation : " + " ; ".join(self.formation),
            "Expériences : " + " ; ".join(f"{e.poste} ({e.employeur}) : {e.realisation}" for e in self.experiences),
            "Compétences : " + ", ".join(self.competences),
            "Certifications : " + ", ".join(self.certifications),
            "Langues : " + ", ".join(self.langues),
        ]
        return "\n".join(l for l in lignes if not l.endswith(": "))


class FicheProfil(BaseModel):
    metiers: list[str] = Field(min_length=1, max_length=6, description="intitulés de métiers, sans type de contrat")
    recherches: list[str] = Field(
        min_length=3, max_length=8,
        description="requêtes à taper dans un moteur d'offres : intitulés de poste de 2 à 5 mots, "
                    "jamais un nom de technologie")
    mots_exclus: list[str] = Field(
        max_length=8,
        description="termes désignant un AUTRE métier que celui visé ; jamais un niveau, "
                    "un contrat ni un mode de travail")
    codes_rome: list[CodeRome] = Field(max_length=5, description="codes ROME, seulement si certain")
    contrats: list[Literal["CDI", "CDD", "intérim", "alternance", "stage", "freelance"]] = Field(min_length=1)
    cv_condense: CvCondense

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
    "2) « metiers » : des intitulés de métier, sans mention de contrat. "
    "3) « recherches » : des intitulés de poste de 2 à 5 mots, tels qu'on les tape dans un moteur "
    "d'offres (ex. « technicien systèmes et réseaux », « administrateur réseau »), jamais un mot seul "
    "ni un nom de technologie. Si l'objectif mentionne l'alternance, ajoute une ou deux recherches "
    "contenant le mot « alternance ». "
    "4) « mots_exclus » : peu nombreux, uniquement des termes qui désignent un AUTRE métier que celui "
    "visé (ex. pour un profil réseaux informatiques : « eau potable », « électricien », « plomberie »). "
    "Jamais un niveau, un type de contrat ni un mode de travail. En cas de doute, n'exclus pas. "
    "5) « contrats » : conformes à l'objectif ; l'alternance seulement si l'objectif la mentionne. "
    "6) Codes ROME : seulement si tu en es certain, sinon une liste vide. "
    "7) « cv_condense » : extrait du CV SEUL, formulations courtes, sans phrase. Pour la formation, "
    "reprends le statut exact écrit dans le CV (obtenu, en cours). "
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
               WHERE id = ? AND status = 'draft'""",
            (dumps(fiche.metiers), dumps(fiche.recherches), dumps(fiche.mots_exclus),
             dumps(fiche.codes_rome), dumps(fiche.contrats), fiche.cv_condense.en_texte(),
             datetime.now(timezone.utc).isoformat(timespec="seconds"), payload["profile_id"]),
        )
    return {"fiche": fiche.model_dump(), "stats": stats}


# ---------------------------------------------------------------------------
# Notation d'une offre pour un profil
# ---------------------------------------------------------------------------
class Notation(BaseModel):
    note: int = Field(ge=0, le=10, description="adéquation de l'offre au candidat, de 0 à 10")
    explication: str = Field(description="une phrase courte qui cite le point décisif")


SYSTEME_NOTATION = (
    "Tu évalues l'adéquation entre une offre d'emploi et un candidat, pour l'aider à trier ses offres. "
    "Barème : "
    "9-10 = métier visé, niveau accessible au candidat, compétences de son CV directement utiles, contrat accepté ; "
    "7-8 = bonne correspondance avec un écart mineur ; "
    "5-6 = domaine proche mais écart notable (expérience demandée supérieure, autre spécialité) ; "
    "3-4 = même secteur mais autre métier (ex. développement logiciel pour un profil systèmes et réseaux) ; "
    "0-2 = hors sujet, ou niveau ou contrat incompatible (ex. poste senior exigeant plusieurs années "
    "d'expérience, formation d'un niveau inférieur à celui du candidat). "
    "Règles : n'invente rien sur l'offre ni sur le candidat ; si la description est trop courte pour juger, "
    "note prudemment et dis-le ; l'explication tient en une phrase et cite le point décisif. "
    "Réponds uniquement avec le JSON demandé."
)


@handler("score_offer")
def score_offer(payload: dict) -> dict:
    oid, pid = payload["offer_id"], payload["profile_id"]
    with session() as conn:  # lecture, puis connexion refermée AVANT l'appel à l'IA
        o = conn.execute("SELECT title, company, location, contract, salary, description "
                         "FROM offers WHERE id = ?", (oid,)).fetchone()
        p = conn.execute("SELECT name, jobs, contracts, city, radius_km, cv_summary "
                         "FROM profiles WHERE id = ?", (pid,)).fetchone()
    if o is None or p is None:
        raise ValueError(f"offre {oid} ou profil {pid} introuvable")
    user = (
        f"CANDIDAT (profil « {p['name']} »)\n"
        f"Métiers visés : {', '.join(json.loads(p['jobs'])) or 'non précisés'}\n"
        f"Contrats acceptés : {', '.join(json.loads(p['contracts']))}\n"
        f"Zone : {p['city']} ({p['radius_km']} km)\n"
        f"CV :\n{p['cv_summary'] or 'non renseigné'}\n\n"
        f"OFFRE\nTitre : {o['title']}\nEntreprise : {o['company'] or 'non précisée'}\n"
        f"Lieu : {o['location'] or 'non précisé'}\nContrat : {o['contract'] or 'non précisé'}\n"
        f"Salaire : {o['salary'] or 'non précisé'}\n"
        f"Description : {(o['description'] or '')[:2000]}"
    )
    notation, stats = chat_json(SYSTEME_NOTATION, user, Notation, max_tokens=400)
    with session() as conn:
        conn.execute("UPDATE matches SET score = ?, explanation = ? WHERE offer_id = ? AND profile_id = ?",
                     (notation.note, notation.explication, oid, pid))
    return {"notation": notation.model_dump(), "stats": stats}
