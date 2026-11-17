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

from app.dedup import normalize

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


MOTS_CONTRAT = {"alternance", "apprentissage", "apprenti", "apprentie", "cdi", "cdd", "interim", "stage", "freelance"}
MOTS_VIDES = {"de", "du", "des", "d", "et", "en", "a", "au", "aux", "la", "le", "les", "l", "pour"}


class FicheProfil(BaseModel):
    metiers: list[str] = Field(min_length=1, max_length=6, description="intitulés de métiers, sans type de contrat")
    recherches: list[str] = Field(
        min_length=3, max_length=8,
        description="requêtes à taper dans un moteur d'offres : intitulés de poste courts, de 1 à 3 mots, "
                    "sans type de contrat, lieu, enseigne ni nom de technologie")
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

    @field_validator("recherches")
    @classmethod
    def recherches_courtes(cls, valeurs: list[str]) -> list[str]:
        """Adzuna exige TOUS les mots d'une recherche dans l'annonce : au-delà de 3 mots, presque rien ne sort.
        Le code garantit donc la règle, même si l'IA l'oublie : on retire les types de contrat, on garde
        les 3 premiers mots porteurs de sens (l'intitulé vient en premier) et pas de petit mot en fin."""
        vus, propres = set(), []
        for v in valeurs:
            garde, pleins = [], 0
            for mot in v.split():
                n = normalize(mot)
                if n in MOTS_CONTRAT:
                    continue
                if n not in MOTS_VIDES:
                    if pleins == 3:
                        break
                    pleins += 1
                garde.append(mot)
            while garde and normalize(garde[-1]) in MOTS_VIDES:
                garde.pop()
            courte = " ".join(garde)
            if courte and courte.lower() not in vus:
                vus.add(courte.lower())
                propres.append(courte)
        return propres


SYSTEME_PROFIL = (
    "Tu aides un candidat à paramétrer une recherche d'emploi automatique en France. "
    "À partir de son CV et de l'objectif du profil, propose une fiche. Règles : "
    "1) N'invente rien : uniquement ce que disent le CV et l'objectif. "
    "2) « metiers » : des intitulés de métier, sans mention de contrat. "
    "3) « recherches » : des intitulés de poste COURTS, de 1 à 3 mots, tels qu'on les tape dans un moteur "
    "d'offres (ex. « technicien réseau », « préparateur de commandes », « hôte de caisse ») : plus une "
    "recherche est longue, moins elle trouve d'offres. Jamais de type de contrat (alternance, CDI…), "
    "de lieu, d'enseigne ni de nom de technologie. "
    "4) « mots_exclus » : peu nombreux, uniquement des termes qui désignent un AUTRE métier que celui "
    "visé (ex. pour un profil réseaux informatiques : « eau potable », « électricien », « plomberie »). "
    "Jamais un niveau, un type de contrat ni un mode de travail. En cas de doute, n'exclus pas. "
    "5) « contrats » : conformes à l'objectif ; l'alternance, le stage et le freelance seulement si "
    "l'objectif les mentionne. "
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
# Les valeurs sont des mots qui se décrivent eux-mêmes ; le code les traduit en nombres.
METIER = {"metier_vise": 3, "metier_voisin": 2, "autre_metier_informatique": 1, "sans_rapport": 0}
NIVEAU = {"accessible": 2, "un_peu_au_dessus": 1, "inaccessible": 0}
COMPETENCES = {"presque_toutes": 3, "la_plupart": 2, "peu": 1, "aucune": 0}
CONTRAT = {"compatible": 1, "incompatible": 0}


class Evaluation(BaseModel):
    metier: Literal["metier_vise", "metier_voisin", "autre_metier_informatique", "sans_rapport"]
    niveau: Literal["accessible", "un_peu_au_dessus", "inaccessible"]
    competences: Literal["presque_toutes", "la_plupart", "peu", "aucune"]
    contrat: Literal["compatible", "incompatible"]
    explication: str = Field(description="une phrase de 25 mots maximum")


SYSTEME_NOTATION = (
    "Tu évalues une offre d'emploi pour un candidat, critère par critère, sans produire de note globale.\n"
    "metier :\n"
    "- metier_vise : l'offre correspond à l'un des métiers visés par le candidat ;\n"
    "- metier_voisin : métier proche (mêmes outils, missions qui se recoupent largement) ;\n"
    "- autre_metier_informatique : informatique, mais un autre métier : développement, architecture "
    "logicielle, ERP/SAP, data, gestion de projet, product owner, commercial, formateur ;\n"
    "- sans_rapport : hors informatique.\n"
    "niveau (accessibilité pour CE candidat) :\n"
    "- accessible : ouvert à un débutant ayant le diplôme du candidat ;\n"
    "- un_peu_au_dessus : courte expérience souhaitée (1 à 2 ans), intitulé « ingénieur » sans autre "
    "indice de séniorité, ou niveau incertain ;\n"
    "- inaccessible : senior, confirmé, expert, lead, référent, architecte, responsable ou manager ; 3 ans "
    "d'expérience ou plus ; salaire supérieur à 45 000 €/an ; ou formation d'un niveau inférieur au "
    "diplôme du candidat.\n"
    "Équivalences : BUT et licence = Bac+3 ; DUT et BTS = Bac+2 ; master et diplôme d'ingénieur = Bac+5.\n"
    "Offre d'une école ou d'un organisme de formation qui recrute des alternants : regarde le diplôme "
    "préparé ; s'il est d'un niveau inférieur ou égal à celui du candidat, niveau = inaccessible ; "
    "s'il est supérieur (master, ingénieur), le niveau peut être accessible.\n"
    "competences : part des compétences demandées présentes dans le CV.\n"
    "contrat : compatible si le contrat fait partie des contrats acceptés ou n'est pas précisé.\n"
    "Règles : n'invente rien ; un stage n'est pas une expérience professionnelle ; si la description est "
    "tronquée, utilise l'intitulé et le salaire. L'explication tient en une phrase de 25 mots maximum. "
    "Réponds uniquement avec le JSON demandé."
)


def evaluer(oid: int, pid: int) -> tuple[dict, str, dict]:
    """Interroge l'IA et renvoie (critères en nombres, explication, statistiques). N'écrit rien en base."""
    with session() as conn:  # lecture, puis connexion refermée AVANT l'appel à l'IA
        o = conn.execute("SELECT title, company, location, contract, salary, description "
                         "FROM offers WHERE id = ?", (oid,)).fetchone()
        p = conn.execute("SELECT name, jobs, contracts, city, radius_km, cv_summary "
                         "FROM profiles WHERE id = ?", (pid,)).fetchone()
    if o is None or p is None:
        raise ValueError(f"offre {oid} ou profil {pid} introuvable")
    # Le candidat d'abord (identique pour toutes les offres du profil : profite du cache d'Ollama)
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
    e, stats = chat_json(SYSTEME_NOTATION, user, Evaluation, max_tokens=400)
    facteurs = {"metier": METIER[e.metier], "niveau": NIVEAU[e.niveau],
                "competences": COMPETENCES[e.competences], "contrat": CONTRAT[e.contrat]}
    return facteurs, e.explication, stats


@handler("score_offer")
def score_offer(payload: dict) -> dict:
    from app.services.scoring import note_finale  # import local : évite un import circulaire au démarrage

    oid, pid = payload["offer_id"], payload["profile_id"]
    # Même annonce déjà évaluée pour ce profil : on recopie, sans rappeler l'IA. « Même annonce » =
    # même empreinte (titre normalisé, entreprise, ville : un doublon publié deux fois), ou même
    # intitulé exact et même entreprise (une enseigne qui publie la même offre dans plusieurs villes).
    with session() as conn:
        o = conn.execute("SELECT title, company, fingerprint FROM offers WHERE id = ?", (oid,)).fetchone()
        if o is None:
            raise ValueError(f"offre {oid} introuvable")
        deja = conn.execute(
            """SELECT m.score, m.explanation, m.factors, m.offer_id FROM matches AS m
               JOIN offers AS x ON x.id = m.offer_id
               WHERE m.profile_id = ? AND m.factors IS NOT NULL AND m.offer_id != ?
                 AND (x.fingerprint = ?
                      OR (lower(x.title) = lower(?) AND lower(coalesce(x.company, '')) = lower(coalesce(?, ''))))
               ORDER BY x.fingerprint = ? DESC
               LIMIT 1""", (pid, oid, o["fingerprint"], o["title"], o["company"], o["fingerprint"])).fetchone()
        if deja is not None:
            conn.execute("UPDATE matches SET score = ?, explanation = ?, factors = ? "
                         "WHERE offer_id = ? AND profile_id = ?",
                         (deja["score"], deja["explanation"], deja["factors"], oid, pid))
            return {"copie_de": deja["offer_id"], "note": deja["score"]}

    facteurs, explication, stats = evaluer(oid, pid)
    note = note_finale(facteurs)
    with session() as conn:
        conn.execute("UPDATE matches SET score = ?, explanation = ?, factors = ? "
                     "WHERE offer_id = ? AND profile_id = ?",
                     (note, explication, json.dumps(facteurs), oid, pid))
    return {"note": note, "facteurs": facteurs, "explication": explication, "stats": stats}
