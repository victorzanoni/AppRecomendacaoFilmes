"""Limpeza e organização da base de filmes assistidos.

Regras principais:
  * títulos são normalizados (espaços, acentos, maiúsculas) para comparação;
  * anos, notas e datas inválidos viram "faltando" (None) em vez de quebrar tudo;
  * o mesmo filme visto várias vezes (rewatch) vira UMA linha só.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from datetime import date

from .models import Entry

_INVISIBLE = re.compile("[\u200b\u200c\u200d\ufeff]")
_LEADING_ARTICLE = re.compile(r"^(the|a|an|o|os|as|la|le|les|el|il)\s+")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def clean_title(title: str | None) -> str:
    """Remove caracteres invisíveis e espaços repetidos, sem mexer em acentos."""
    text = unicodedata.normalize("NFKC", title or "")
    text = _INVISIBLE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(title: str | None) -> str:
    """Forma 'comparável' de um título: minúsculas, sem acentos, sem pontuação,
    sem artigo inicial. 'Amélie' e 'The Amelie!' viram a mesma coisa."""
    text = clean_title(title).lower().replace("&", " and ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("'", "").replace("\u2019", "")
    text = re.sub(r"[\W_]+", " ", text).strip()
    return _LEADING_ARTICLE.sub("", text)


def entry_key(entry: Entry) -> str:
    """Identificador de "mesmo filme": pelo ID do TMDB se houver, senão título+ano."""
    if entry.tmdb_id:
        return f"id:{entry.tmdb_id}"
    return f"t:{normalize_title(entry.title)}:{entry.year or ''}"


def clean_entries(entries: list[Entry]) -> list[Entry]:
    """Valida cada campo; descarta linhas sem título."""
    limit_year = date.today().year + 5
    cleaned: list[Entry] = []
    for e in entries:
        title = clean_title(e.title)
        if not title:
            continue
        year = e.year if e.year and 1870 <= e.year <= limit_year else None
        rating = e.rating if e.rating and 0 < e.rating <= 5 else None
        if rating is not None:
            rating = round(rating * 2) / 2  # Letterboxd só usa passos de meia estrela
        last = e.last_watched[:10] if e.last_watched and _DATE.match(e.last_watched) else None
        cleaned.append(replace(e, title=title, year=year, rating=rating, last_watched=last))
    return cleaned


def merge_entries(entries: list[Entry]) -> list[Entry]:
    """Junta duplicatas. Para o mesmo filme:
       nota = a mais recente que existir · curtido = se algum registro curtiu ·
       data = a mais recente · ID do TMDB = o primeiro que aparecer."""
    groups: dict[str, list[Entry]] = {}
    for e in entries:
        groups.setdefault(entry_key(e), []).append(e)

    merged: list[Entry] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda e: e.last_watched or "")  # estável
        rated = [e for e in ordered if e.rating is not None]
        newest = ordered[-1]
        merged.append(
            Entry(
                title=newest.title,
                year=next((e.year for e in reversed(ordered) if e.year), None),
                rating=rated[-1].rating if rated else None,
                liked=any(e.liked for e in ordered),
                last_watched=newest.last_watched,
                tmdb_id=next((e.tmdb_id for e in ordered if e.tmdb_id), None),
                lb_url=next((e.lb_url for e in reversed(ordered) if e.lb_url), None),
                source="+".join(sorted({s for e in ordered for s in e.source.split("+")})),
            )
        )
    return merged
