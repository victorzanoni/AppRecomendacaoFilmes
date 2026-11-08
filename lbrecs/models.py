"""Estruturas de dados compartilhadas."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Entry:
    """Um filme assistido por você (uma linha da sua base)."""

    title: str
    year: int | None = None
    rating: float | None = None        # 0.5 a 5.0, ou None se você não deu nota
    liked: bool = False                # coração no Letterboxd
    last_watched: str | None = None    # AAAA-MM-DD
    tmdb_id: int | None = None         # None = ainda não identificado no TMDB
    lb_url: str | None = None
    source: str = "rss"                # de onde veio: rss, export
