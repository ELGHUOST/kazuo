"""Base de données SQLite de Kazuo : un seul fichier, /var/lib/kazuo/kazuo.db."""
import sqlite3
from contextlib import contextmanager

from app.config import settings

DB_PATH = settings.data_dir / "kazuo.db"

# Une entrée = une migration. On ne modifie JAMAIS une migration déjà appliquée :
# on en ajoute une nouvelle à la fin. PRAGMA user_version retient la dernière appliquée.
MIGRATIONS = [
    # v1 : les offres
    """
    CREATE TABLE offers (
        id           INTEGER PRIMARY KEY,
        source       TEXT NOT NULL,
        source_id    TEXT NOT NULL,
        fingerprint  TEXT NOT NULL,
        title        TEXT NOT NULL,
        company      TEXT,
        location     TEXT,
        contract     TEXT,
        salary       TEXT,
        description  TEXT NOT NULL DEFAULT '',
        url          TEXT NOT NULL,
        published_at TEXT,
        fetched_at   TEXT NOT NULL,
        raw_json     TEXT,
        UNIQUE (source, source_id)
    );
    CREATE INDEX idx_offers_fingerprint ON offers (fingerprint);
    CREATE INDEX idx_offers_published   ON offers (published_at);
    """,
    # v2 : profils de recherche + file d'attente de l'IA
    """
    CREATE TABLE profiles (
        id          INTEGER PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,          -- « Réseau / Cloud », « Job alimentaire »
        first_name  TEXT NOT NULL,
        cv_path     TEXT NOT NULL,                 -- /var/lib/kazuo/cv/<identifiant>.pdf
        cv_text     TEXT NOT NULL,                 -- texte extrait du PDF
        cv_summary  TEXT,                          -- CV condensé, pour des prompts courts
        jobs        TEXT NOT NULL DEFAULT '[]',    -- JSON : métiers visés
        keywords    TEXT NOT NULL DEFAULT '[]',    -- JSON : mots-clés de recherche
        excluded    TEXT NOT NULL DEFAULT '[]',    -- JSON : mots à exclure
        rome_codes  TEXT NOT NULL DEFAULT '[]',    -- JSON : codes ROME (à vérifier)
        contracts   TEXT NOT NULL DEFAULT '[]',    -- JSON : CDI, CDD, intérim, alternance...
        city        TEXT NOT NULL DEFAULT 'Bordeaux',
        radius_km   INTEGER NOT NULL DEFAULT 30,
        status      TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'active', 'paused')),
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );

    CREATE TABLE llm_jobs (
        id          INTEGER PRIMARY KEY,
        kind        TEXT NOT NULL,                 -- 'profile_from_cv', 'score_offer'...
        payload     TEXT NOT NULL,                 -- JSON : paramètres de la tâche
        priority    INTEGER NOT NULL DEFAULT 10,   -- plus grand = plus urgent
        status      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'done', 'error')),
        attempts    INTEGER NOT NULL DEFAULT 0,
        result      TEXT,                          -- JSON : réponse validée de l'IA
        error       TEXT,
        created_at  TEXT NOT NULL,
        started_at  TEXT,
        finished_at TEXT,
        duration_s  REAL                           -- mesure réelle du temps de calcul
    );
    CREATE INDEX idx_llm_jobs_queue ON llm_jobs (status, priority DESC, id);
    """,
    # v3 : rattachement offre × profil (sert aussi à la notation et au swipe)
    """
    CREATE TABLE matches (
        offer_id    INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
        profile_id  INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        query       TEXT NOT NULL,                 -- la recherche qui a trouvé l'offre
        found_at    TEXT NOT NULL,
        excluded_by TEXT,                          -- mot exclu trouvé dans le titre ; NULL = retenue
        score       INTEGER CHECK (score BETWEEN 0 AND 10),   -- phase 3
        explanation TEXT,                                     -- phase 3
        decision    TEXT CHECK (decision IN ('like', 'nope')),-- phase 4 (swipe)
        decided_at  TEXT,
        PRIMARY KEY (offer_id, profile_id)
    );
    CREATE INDEX idx_matches_profile ON matches (profile_id, excluded_by);
    """,
    # v4 : catégorie Adzuna par profil (ex. it-jobs) : limite les recherches à un secteur
    """
    ALTER TABLE profiles ADD COLUMN adzuna_category TEXT;
    """,
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row             # colonnes accessibles par nom : row["title"]
    conn.execute("PRAGMA journal_mode = WAL")  # lectures possibles pendant une écriture
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session():
    """Connexion courte : validée si tout va bien, annulée en cas d'erreur, toujours fermée."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate() -> int:
    """Applique les migrations manquantes et renvoie la version du schéma."""
    conn = connect()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for num, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {num};\nCOMMIT;")
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
