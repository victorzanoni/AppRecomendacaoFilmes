"""Descobre o filme do TMDB que corresponde a um título do Letterboxd.

Quando o RSS já traz o ID do TMDB isso nem é necessário. Mas na exportação (ou se
o RSS vier sem ID) só temos título + ano, e títulos nem sempre batem exatamente:
acentos, subtítulos, "&" vs "and", título original vs título em inglês...
Por isso comparamos de forma aproximada e conferimos o ano.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from .cleaning import normalize_title

MIN_SCORE = 0.72  # abaixo disso preferimos "não identificado" a um palpite errado


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    direct = SequenceMatcher(None, a, b).ratio()
    # Ignora a ordem das palavras ("Godfather, The" vs "The Godfather").
    reordered = SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
    score = max(direct, reordered)
    # "Star Wars" vs "Star Wars Episode IV A New Hope": um começa com o outro.
    short, long_ = sorted((a, b), key=len)
    if len(short.split()) >= 2 and long_.startswith(short + " "):
        score = max(score, 0.8)
    return score


def _year_adjustment(wanted: int | None, found: int | None) -> float:
    if not wanted or not found:
        return 0.0
    diff = abs(wanted - found)
    return {0: 0.0, 1: -0.05, 2: -0.15}.get(diff, -0.35)


def pick_best(title: str, year: int | None, results: list[dict]) -> tuple[dict | None, float]:
    """Escolhe o melhor resultado de uma busca do TMDB e devolve (resultado, nota 0-1)."""
    wanted = normalize_title(title)
    best, best_score = None, 0.0
    for r in results:
        found_year = int(r["release_date"][:4]) if (r.get("release_date") or "")[:4].isdigit() else None
        sim = max(
            _similarity(wanted, normalize_title(r.get("title"))),
            _similarity(wanted, normalize_title(r.get("original_title"))),
        )
        score = sim + _year_adjustment(year, found_year) + min(r.get("popularity") or 0, 100) / 100 * 0.01
        if score > best_score:
            best, best_score = r, score
    return best, best_score


def _query_variants(title: str, year: int | None) -> list[tuple[str, int | None]]:
    """Ordem de tentativas: título+ano, título sem ano, título sem subtítulo."""
    variants: list[tuple[str, int | None]] = []
    if year:
        variants.append((title, year))
    variants.append((title, None))
    for sep in (":", " - "):
        if sep in title:
            variants.append((title.split(sep)[0].strip(), year))
            break
    return variants


def resolve(client, title: str, year: int | None, min_score: float = MIN_SCORE) -> tuple[int | None, float]:
    """Devolve (tmdb_id, nota). tmdb_id é None se nada parecido o bastante for encontrado."""
    best_score = 0.0
    for query, query_year in _query_variants(title, year):
        results = client.search_movie(query, year=query_year)
        match, score = pick_best(title, year, results)
        best_score = max(best_score, score)
        if match and score >= min_score:
            return int(match["id"]), score
    return None, best_score
