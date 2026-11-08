"""Coleta de filmes assistidos no Letterboxd.

Duas fontes:

1. RSS público  (https://letterboxd.com/USUARIO/rss/)
   - não precisa de login nem de chave;
   - traz só as ~50 entradas mais recentes do DIÁRIO (filmes que você registrou
     com data; avaliar um filme sem registrá-lo no diário não aparece no feed);
   - já vem com o ID do TMDB, o que evita erros de correspondência de título.

2. Exportação oficial (opcional) — Letterboxd > Settings > Data > Export your data.
   Gera um .zip com o histórico COMPLETO (watched.csv, ratings.csv, likes/films.csv).
   Use `python main.py update --export caminho/do/arquivo.zip`.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator

import requests

from .models import Entry

RSS_URL = "https://letterboxd.com/{user}/rss/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; letterboxd-recs/1.0; uso pessoal)"}

# "Nome do Filme, 2019 - ★★★½"  (formato do <title> quando faltam as tags extras)
_TITLE_RE = re.compile(r"^(?P<title>.+?),\s*(?P<year>\d{4})(?:\s+-\s+(?P<stars>[\u2605\u00bd]+))?\s*$")


class LetterboxdError(Exception):
    """Falha ao ler o Letterboxd (mensagem pensada para o usuário final)."""


# ----------------------------------------------------------------------------- RSS
def fetch_rss(user: str, timeout: int = 20) -> list[Entry]:
    url = RSS_URL.format(user=user)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
    except requests.RequestException as exc:
        raise LetterboxdError(f"Não consegui acessar {url}: {exc}") from exc

    if resp.status_code == 404:
        raise LetterboxdError(
            f"Usuário '{user}' não encontrado no Letterboxd. Confira o LETTERBOXD_USER no .env "
            "(é o nome que aparece em letterboxd.com/SEU_NOME)."
        )
    if resp.status_code in (403, 429):
        raise LetterboxdError(
            f"O Letterboxd bloqueou a requisição temporariamente (HTTP {resp.status_code}). "
            "Espere alguns minutos e tente de novo, ou use a exportação (--export)."
        )
    if resp.status_code != 200:
        raise LetterboxdError(f"O Letterboxd respondeu com HTTP {resp.status_code} para {url}.")
    return parse_rss(resp.content)


def _local(tag: str) -> str:
    """'{https://letterboxd.com}filmTitle' -> 'filmTitle' (ignora o namespace)."""
    return tag.rsplit("}", 1)[-1]


def _stars_to_number(stars: str) -> float | None:
    value = stars.count("\u2605") + (0.5 if "\u00bd" in stars else 0)
    return value or None


def _to_int(text: str | None) -> int | None:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def parse_rss(xml_bytes: bytes) -> list[Entry]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise LetterboxdError("O feed do Letterboxd veio num formato inesperado (XML inválido).") from exc

    entries: list[Entry] = []
    for item in root.iter("item"):
        f: dict[str, str] = {}
        for child in item:
            f.setdefault(_local(child.tag), (child.text or "").strip())

        link = f.get("link", "")
        if "/list/" in link:  # listas aparecem no feed, mas não são filmes
            continue

        title, year, rating = f.get("filmTitle"), _to_int(f.get("filmYear")), None
        if f.get("memberRating"):
            try:
                rating = float(f["memberRating"])
            except ValueError:
                rating = None

        # Plano B: quando faltam as tags letterboxd:*, tenta ler o <title> do item.
        m = _TITLE_RE.match(f.get("title", ""))
        if m:
            title = title or m["title"]
            year = year or int(m["year"])
            if rating is None and m["stars"]:
                rating = _stars_to_number(m["stars"])
        if not title:
            continue  # item que não é um filme

        watched = f.get("watchedDate")
        if not watched and f.get("pubDate"):
            try:
                watched = parsedate_to_datetime(f["pubDate"]).date().isoformat()
            except (TypeError, ValueError):
                watched = None

        entries.append(
            Entry(
                title=title,
                year=year,
                rating=rating,
                liked=f.get("memberLike", "").lower() == "yes",
                last_watched=watched,
                tmdb_id=_to_int(f.get("movieId")),
                lb_url=link or None,
                source="rss",
            )
        )
    return entries


# -------------------------------------------------------------------- Exportação
def _iter_csv_files(path: Path) -> Iterator[tuple[str, str]]:
    """Devolve (caminho_relativo_em_minúsculas, conteúdo) de cada CSV do .zip/pasta/arquivo."""
    if path.is_dir():
        for p in sorted(path.rglob("*.csv")):
            yield p.relative_to(path).as_posix().lower(), p.read_text(encoding="utf-8-sig")
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".csv"):
                    yield name.lower(), zf.read(name).decode("utf-8-sig")
    elif path.suffix.lower() == ".csv":
        text = path.read_text(encoding="utf-8-sig")
        header = text.splitlines()[0].lower() if text else ""
        yield ("ratings.csv" if "rating" in header else "watched.csv"), text
    else:
        raise LetterboxdError("A exportação deve ser o .zip baixado do Letterboxd, a pasta extraída ou um .csv.")


def read_export(path: str | Path) -> list[Entry]:
    path = Path(path).expanduser()
    if not path.exists():
        raise LetterboxdError(f"Arquivo de exportação não encontrado: {path}")

    tables: dict[str, list[dict]] = {"watched": [], "ratings": [], "likes": []}
    try:
        for rel, text in _iter_csv_files(path):
            if rel.endswith("likes/films.csv"):
                kind = "likes"
            elif rel.endswith("watched.csv"):
                kind = "watched"
            elif rel.endswith("ratings.csv"):
                kind = "ratings"
            else:
                continue
            tables[kind].extend(csv.DictReader(io.StringIO(text)))
    except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
        raise LetterboxdError(f"Não consegui ler a exportação ({exc}).") from exc

    if not tables["watched"] and not tables["ratings"]:
        raise LetterboxdError("Não encontrei watched.csv nem ratings.csv nessa exportação.")

    liked_uris = {r.get("Letterboxd URI", "") for r in tables["likes"]}
    by_key: dict[str, Entry] = {}

    def upsert(row: dict, rating: float | None = None) -> None:
        name = (row.get("Name") or "").strip()
        if not name:
            return
        uri = (row.get("Letterboxd URI") or "").strip()
        key = uri or f"{name}:{row.get('Year', '')}"
        entry = by_key.get(key)
        if entry is None:
            entry = by_key[key] = Entry(
                title=name,
                year=_to_int(row.get("Year")),
                last_watched=(row.get("Date") or "")[:10] or None,
                lb_url=uri or None,
                liked=uri in liked_uris,
                source="export",
            )
        if rating is not None:
            entry.rating = rating

    for row in tables["watched"]:
        upsert(row)
    for row in tables["ratings"]:  # quem tem nota com certeza assistiu
        try:
            upsert(row, float(row.get("Rating") or 0) or None)
        except ValueError:
            upsert(row)
    return list(by_key.values())
