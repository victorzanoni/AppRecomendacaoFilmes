"""Configurações do app.

Tudo que você precisa trocar fica no arquivo `.env` (copie de `.env.example`).
Este módulo só lê esse arquivo e valida os valores.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# Valores de exemplo do .env.example: se sobrarem no arquivo, avisamos.
_PLACEHOLDERS = {"", "seu_usuario_aqui", "sua_chave_aqui"}


class ConfigError(Exception):
    """Configuração ausente ou inválida (mensagem pensada para o usuário final)."""


@dataclass(frozen=True)
class Settings:
    letterboxd_user: str = ""
    tmdb_api_key: str = ""
    tmdb_language: str = "pt-BR"
    num_recommendations: int = 60   # quantos filmes aparecem na página
    max_seed_films: int = 60        # quantos filmes que você gostou viram "sementes"
    max_candidates: int = 150       # quantos candidatos são analisados a fundo
    min_votes: int = 150            # ignora filmes com poucos votos no TMDB
    db_path: Path = DATA_DIR / "cinema.db"
    manual_matches_path: Path = DATA_DIR / "manual_matches.csv"
    enriched_csv_path: Path = DATA_DIR / "watched_enriched.csv"


def default_db_path() -> Path:
    load_dotenv(BASE_DIR / ".env")
    return Path(os.getenv("LB_DB_PATH") or DATA_DIR / "cinema.db")


def clean_username(raw: str) -> str:
    """Aceita 'fulano', '@fulano' ou a URL do perfil e devolve só o nome de usuário."""
    raw = (raw or "").strip().strip("/")
    if "letterboxd.com" in raw:
        raw = raw.split("letterboxd.com/", 1)[-1].split("/", 1)[0]
    return raw.lstrip("@").strip()


def _int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} no .env precisa ser um número inteiro (valor atual: {raw!r}).")
    if value < minimum:
        raise ConfigError(f"{name} no .env precisa ser pelo menos {minimum}.")
    return value


def load_settings(require_user: bool = True, require_tmdb: bool = True) -> Settings:
    load_dotenv(BASE_DIR / ".env",encoding="utf-8-sig")

    user = clean_username(os.getenv("LETTERBOXD_USER", ""))
    key = os.getenv("TMDB_API_KEY", "").strip()

    if require_user and (user in _PLACEHOLDERS or not re.fullmatch(r"[A-Za-z0-9_\-]+", user)):
        raise ConfigError(
            "Defina LETTERBOXD_USER no arquivo .env com o seu nome de usuário do Letterboxd "
            "(a parte depois de letterboxd.com/). Se o arquivo .env ainda não existe, "
            "copie o .env.example e renomeie para .env."
        )
    if require_user:
        if user in _PLACEHOLDERS:
            raise ConfigError(
                "LETTERBOXD_USER está vazio ou ainda com o valor de exemplo. "
                "Confira se você editou o arquivo .env (e não o .env.example) e se salvou."
            )
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", user):
            raise ConfigError(
                f"LETTERBOXD_USER ({user!r}) tem caracteres inválidos. "
                "Use só o nome que aparece em letterboxd.com/SEU_NOME."
            )

    return Settings(
        letterboxd_user=user,
        tmdb_api_key=key,
        tmdb_language=os.getenv("TMDB_LANGUAGE", "pt-BR").strip() or "pt-BR",
        num_recommendations=_int("NUM_RECOMMENDATIONS", 60),
        max_seed_films=_int("MAX_SEED_FILMS", 40),
        max_candidates=_int("MAX_CANDIDATES", 150),
        min_votes=_int("MIN_VOTES", 150, minimum=0),
        db_path=default_db_path(),
    )
