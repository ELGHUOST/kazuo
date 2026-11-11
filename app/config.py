"""Configuration de Kazuo.

Toutes les valeurs viennent de /etc/kazuo/kazuo.env (ou du fichier indiqué
par la variable KAZUO_ENV_FILE). Le reste du code importe uniquement `settings`.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = os.environ.get("KAZUO_ENV_FILE", "/etc/kazuo/kazuo.env")
if not Path(ENV_FILE).is_file():
    raise SystemExit(f"Fichier de configuration introuvable : {ENV_FILE}")
load_dotenv(ENV_FILE)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    tz: str
    ollama_url: str
    ollama_model: str
    # repr=False : les secrets n'apparaissent jamais si `settings` est affiché dans un log
    ft_client_id: str = field(repr=False)
    ft_client_secret: str = field(repr=False)
    adzuna_app_id: str = field(repr=False)
    adzuna_app_key: str = field(repr=False)
    gmail_address: str = field(repr=False)
    gmail_app_password: str = field(repr=False)


settings = Settings(
    data_dir=Path(_env("KAZUO_DATA_DIR", "/var/lib/kazuo")),
    tz=_env("KAZUO_TZ", "Europe/Paris"),
    ollama_url=_env("OLLAMA_URL", "http://192.168.8.210:11434").rstrip("/"),
    ollama_model=_env("OLLAMA_MODEL", "gpt-oss:20b"),
    ft_client_id=_env("FT_CLIENT_ID"),
    ft_client_secret=_env("FT_CLIENT_SECRET"),
    adzuna_app_id=_env("ADZUNA_APP_ID"),
    adzuna_app_key=_env("ADZUNA_APP_KEY"),
    gmail_address=_env("GMAIL_ADDRESS"),
    gmail_app_password=_env("GMAIL_APP_PASSWORD"),
)
