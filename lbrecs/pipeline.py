"""Pipeline de atualização: é o que roda no comando `python main.py update`.

    1. coleta        RSS do Letterboxd (+ exportação opcional) + o que já estava salvo
    2. identificação descobre o ID do TMDB dos filmes que vieram sem ID
    3. enriquecimento baixa detalhes do TMDB (gêneros, elenco, diretor, sinopse...)
    4. limpeza       remove duplicatas, valida campos, salva a base (SQLite + CSV)
    5. recomendação  calcula as sugestões e grava para a página web
"""
from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from .cleaning import clean_entries, merge_entries, normalize_title
from .config import Settings
from .db import Database
from .letterboxd import LetterboxdError, fetch_rss, read_export
from .matching import resolve
from .models import Entry
from .recommender import recommend
from .tmdb import TMDBError, slim_movie

WORKERS = 8  # chamadas simultâneas ao TMDB (o limite dele é folgado, ~40/s)


class PipelineError(Exception):
    """Problema que impede a atualização (mensagem pensada para o usuário final)."""


def _parallel(fn: Callable, items: list) -> list:
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(fn, items))


def load_manual_matches(path: Path) -> dict[tuple[str, int | None], int]:
    """Lê data/manual_matches.csv (colunas: title,year,tmdb_id) — correções feitas à mão."""
    matches: dict[tuple[str, int | None], int] = {}
    if not path.exists():
        return matches
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                tmdb_id = int(str(row.get("tmdb_id", "")).strip())
            except ValueError:
                continue
            year = int(row["year"]) if str(row.get("year", "")).strip().isdigit() else None
            matches[(normalize_title(row.get("title")), year)] = tmdb_id
    return matches


class MovieStore:
    """Detalhes de filmes do TMDB com cache em disco (SQLite)."""

    def __init__(self, client, db: Database):
        self.client, self.db = client, db

    def get(self, tmdb_id: int) -> dict | None:
        cached = self.db.get_movie(tmdb_id)
        if cached:
            return cached
        raw = self.client.movie_details(tmdb_id)
        if raw is None:
            return None
        movie = slim_movie(raw)
        self.db.put_movie(tmdb_id, movie)
        return movie

    def prefetch(self, ids: list[int]) -> None:
        """Baixa em paralelo o que ainda não está no cache (gravação fica na thread principal)."""
        missing = [i for i in dict.fromkeys(ids) if self.db.get_movie(i) is None]
        for tmdb_id, raw in zip(missing, _parallel(self.client.movie_details, missing)):
            if raw is not None:
                self.db.put_movie(tmdb_id, slim_movie(raw))

    def related(self, tmdb_id: int) -> list[dict]:
        cached = self.db.get_related(tmdb_id)
        if cached is not None:
            return cached
        items = [{"id": r["id"]} for r in self.client.recommendations(tmdb_id)]
        self.db.put_related(tmdb_id, items)
        return items


def run_update(
    settings: Settings,
    client,
    db: Database | None = None,
    export_path: str | None = None,
    fetch_rss_fn: Callable[[str], list[Entry]] = fetch_rss,
    log: Callable[[str], None] = print,
) -> dict:
    client.check_key()  # falha cedo (antes de criar qualquer arquivo) se a chave do TMDB estiver errada
    db = db or Database(settings.db_path)
    store = MovieStore(client, db)

    # ---------------------------------------------------------------- 1. coleta
    log("[1/5] Coletando seus filmes do Letterboxd...")
    fresh: list[Entry] = []
    try:
        rss = fetch_rss_fn(settings.letterboxd_user)
        log(f"      RSS: {len(rss)} registros do diário")
        fresh += rss
    except LetterboxdError as exc:
        log(f"      Aviso: {exc}")
    if export_path:
        exported = read_export(export_path)
        log(f"      Exportação: {len(exported)} filmes")
        fresh += exported

    previous_user = db.get_meta("user")
    if previous_user and previous_user.lower() != settings.letterboxd_user.lower():
        # Trocou o usuário no .env: não pode misturar o histórico de duas pessoas.
        log(f"      Usuário mudou de '{previous_user}' para '{settings.letterboxd_user}': começando uma base nova.")
        previous = []
    else:
        previous = db.load_watched()
    if previous:
        log(f"      Base anterior: {len(previous)} filmes (serão mantidos)")
    if not fresh and not previous:
        raise PipelineError(
            "Não consegui nenhum filme: o RSS falhou e não há base anterior nem exportação. "
            "Veja a mensagem acima."
        )
    entries = merge_entries(clean_entries(previous + fresh))

    # --------------------------------------------------------- 2. identificação
    to_resolve = [e for e in entries if not e.tmdb_id]
    if to_resolve:
        log(f"[2/5] Identificando {len(to_resolve)} filmes sem ID do TMDB (por título e ano)...")
        overrides = load_manual_matches(settings.manual_matches_path)

        def find(e: Entry):
            manual = overrides.get((normalize_title(e.title), e.year)) or overrides.get((normalize_title(e.title), None))
            return (manual, 1.0) if manual else resolve(client, e.title, e.year)

        for e, (tmdb_id, _score) in zip(to_resolve, _parallel(find, to_resolve)):
            e.tmdb_id = tmdb_id
    else:
        log("[2/5] Todos os filmes já vieram com ID do TMDB.")

    # ------------------------------------------------------- 3. enriquecimento
    ids = [e.tmdb_id for e in entries if e.tmdb_id]
    log(f"[3/5] Baixando detalhes de {len(ids)} filmes no TMDB (o que já está em cache é pulado)...")
    store.prefetch(ids)
    movies: dict[int, dict] = {}
    for e in entries:
        if e.tmdb_id:
            movie = store.get(e.tmdb_id)
            if movie:
                movies[e.tmdb_id] = movie
            else:
                e.tmdb_id = None  # ID que não existe mais no TMDB

    # ------------------------------------------------------------- 4. limpeza
    entries = merge_entries(entries)  # IDs novos podem revelar duplicatas
    unresolved = [e for e in entries if not e.tmdb_id]
    db.replace_watched(entries)
    db.set_meta("user", settings.letterboxd_user)
    _write_enriched_csv(settings.enriched_csv_path, entries, movies)
    identified = len(entries) - len(unresolved)
    log(f"[4/5] Base salva: {len(entries)} filmes ({identified} identificados, {len(unresolved)} sem correspondência).")
    for e in unresolved[:10]:
        log(f"      não identificado: {e.title} ({e.year or 'ano ?'})")
    if len(unresolved) > 10:
        log(f"      ...e mais {len(unresolved) - 10} (lista completa na página web).")

    # --------------------------------------------------------- 5. recomendação
    log("[5/5] Calculando recomendações...")
    recs, summary = recommend(
        entries,
        get_movie=store.get,
        get_related=store.related,
        discover=lambda genre_ids: client.discover(genre_ids, min_votes=max(settings.min_votes, 1000)),
        prefetch=store.prefetch,
        num_recs=settings.num_recommendations,
        max_seeds=settings.max_seed_films,
        max_candidates=settings.max_candidates,
        min_votes=settings.min_votes,
        log=log,
    )
    _fill_missing_overviews(client, recs)

    db.save_recommendations(recs)
    db.set_meta("updated_at", datetime.now().isoformat(timespec="seconds"))
    db.set_meta("watched_total", len(entries))
    db.set_meta("summary", summary)
    db.set_meta("unresolved", [{"title": e.title, "year": e.year} for e in unresolved])
    log(f"Pronto: {len(recs)} recomendações salvas. Rode `python main.py serve` para ver no navegador.")
    return {"watched": len(entries), "unresolved": len(unresolved), "recommendations": len(recs)}


def _fill_missing_overviews(client, recs: list[dict]) -> None:
    """Se a sinopse em português não existir, usa a versão em inglês."""
    missing = [r for r in recs if not r["overview"]]

    def fetch(r: dict) -> str:
        try:
            raw = client.movie_details(r["id"], language="en-US") or {}
        except TMDBError:
            return ""
        return (raw.get("overview") or "").strip()

    for r, overview in zip(missing, _parallel(fetch, missing)):
        r["overview"] = overview


def _write_enriched_csv(path: Path, entries: list[Entry], movies: dict[int, dict]) -> None:
    """Base legível em planilha: seus dados + gêneros, diretores e duração do TMDB."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["tmdb_id", "title", "year", "rating", "liked", "last_watched", "source",
               "tmdb_title", "genres", "directors", "runtime", "language"]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for e in sorted(entries, key=lambda e: (e.last_watched or "", e.title), reverse=True):
            m = movies.get(e.tmdb_id or 0, {})
            writer.writerow([
                e.tmdb_id or "", e.title, e.year or "", e.rating if e.rating is not None else "",
                "sim" if e.liked else "", e.last_watched or "", e.source,
                m.get("title", ""), ", ".join(g["name"] for g in m.get("genres", [])),
                ", ".join(d["name"] for d in m.get("directors", [])), m.get("runtime") or "",
                m.get("original_language", ""),
            ])
