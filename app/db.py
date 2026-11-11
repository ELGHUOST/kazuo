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
