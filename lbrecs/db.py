"""Banco local (SQLite). Um único arquivo em data/cinema.db — pode apagar para recomeçar.

Tabelas:
  watched          seus filmes assistidos, já limpos e sem duplicatas
  movies           cache dos detalhes do TMDB (evita repetir chamadas)
  related_cache    cache das listas "recomendados a partir de X" do TMDB
  recommendations  o resultado final que a página web mostra
  meta             data da atualização, perfil de gosto, filmes não identificados...
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .cleaning import entry_key, normalize_title
from .models import Entry

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watched (
    key TEXT PRIMARY KEY, tmdb_id INTEGER, title TEXT NOT NULL, year INTEGER,
    rating REAL, liked INTEGER NOT NULL DEFAULT 0, last_watched TEXT,
    lb_url TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS movies (tmdb_id INTEGER PRIMARY KEY, data TEXT NOT NULL, fetched_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS related_cache (tmdb_id INTEGER PRIMARY KEY, data TEXT NOT NULL, fetched_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS recommendations (rank INTEGER PRIMARY KEY, tmdb_id INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

DAY = 86400


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------- assistidos
    def load_watched(self) -> list[Entry]:
        rows = self.conn.execute("SELECT * FROM watched").fetchall()
        return [
            Entry(
                title=r["title"], year=r["year"], rating=r["rating"], liked=bool(r["liked"]),
                last_watched=r["last_watched"], tmdb_id=r["tmdb_id"], lb_url=r["lb_url"],
                source=r["source"] or "rss",
            )
            for r in rows
        ]

    def replace_watched(self, entries: list[Entry]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM watched")
            self.conn.executemany(
                "INSERT OR REPLACE INTO watched VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (entry_key(e), e.tmdb_id, e.title, e.year, e.rating, int(e.liked),
                     e.last_watched, e.lb_url, e.source)
                    for e in entries
                ],
            )

    def watched_filters(self) -> tuple[set[int], set[tuple[str, int | None]], set[str]]:
        """(IDs do TMDB, pares (título normalizado, ano), títulos de filmes sem ano) já assistidos.
        Usado para garantir que nada já visto apareça como recomendação."""
        entries = self.load_watched()
        ids = {e.tmdb_id for e in entries if e.tmdb_id}
        keys = {(normalize_title(e.title), e.year) for e in entries}
        no_year = {normalize_title(e.title) for e in entries if not e.year}
        return ids, keys, no_year

    # ----------------------------------------------------------------- caches
    def _cache_get(self, table: str, tmdb_id: int, max_age_days: float):
        row = self.conn.execute(f"SELECT data, fetched_at FROM {table} WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        if row and time.time() - row["fetched_at"] <= max_age_days * DAY:
            return json.loads(row["data"])
        return None

    def _cache_put(self, table: str, tmdb_id: int, data) -> None:
        with self.conn:
            self.conn.execute(
                f"INSERT OR REPLACE INTO {table} VALUES (?,?,?)", (tmdb_id, json.dumps(data), time.time())
            )

    def get_movie(self, tmdb_id: int, max_age_days: float = 30):
        return self._cache_get("movies", tmdb_id, max_age_days)

    def put_movie(self, tmdb_id: int, data: dict) -> None:
        self._cache_put("movies", tmdb_id, data)

    def get_related(self, tmdb_id: int, max_age_days: float = 7):
        return self._cache_get("related_cache", tmdb_id, max_age_days)

    def put_related(self, tmdb_id: int, data: list) -> None:
        self._cache_put("related_cache", tmdb_id, data)

    # ---------------------------------------------------------- recomendações
    def save_recommendations(self, recs: list[dict]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM recommendations")
            self.conn.executemany(
                "INSERT INTO recommendations VALUES (?,?,?)",
                [(i, r["id"], json.dumps(r)) for i, r in enumerate(recs, start=1)],
            )

    def load_recommendations(self) -> list[dict]:
        rows = self.conn.execute("SELECT data FROM recommendations ORDER BY rank").fetchall()
        return [json.loads(r["data"]) for r in rows]

    # ------------------------------------------------------------------- meta
    def set_meta(self, key: str, value) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    def get_meta(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default
