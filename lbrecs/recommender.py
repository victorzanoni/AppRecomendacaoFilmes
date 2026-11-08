"""Motor de recomendação.

Como funciona (em 4 passos):

1. PERFIL DE GOSTO. Cada filme que você viu recebe uma "afinidade" (nota alta e
   coração = positiva; nota baixa = negativa). Somamos essa afinidade nas
   características do filme (gêneros, diretor, elenco, palavras-chave, década,
   idioma) e o resultado é o seu perfil.

2. CANDIDATOS. Para os filmes de maior afinidade ("sementes"), pedimos ao TMDB
   os filmes que as pessoas costumam recomendar a partir deles. Filmes que
   aparecem para várias sementes ganham pontos (sinal "colaborativo").
   Se faltarem candidatos, completamos com os mais bem avaliados dos seus
   gêneros favoritos.

3. PONTUAÇÃO. Cada candidato recebe:
       50%  semelhança com o seu perfil (cosseno entre os vetores de características)
       35%  sinal colaborativo (quantas sementes o recomendaram, e quão forte)
       15%  qualidade (nota do TMDB, suavizada pelo nº de votos)

4. FILTROS. Nada que você já assistiu entra — conferido por ID do TMDB e também
   por título+ano (para filmes que não conseguimos identificar), além de
   descartar filmes adultos, não lançados ou com poucos votos.

Este módulo não faz chamadas de rede: recebe funções (`get_related`, `get_movie`,
`discover`) e pode ser testado offline.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Callable

from .cleaning import normalize_title
from .models import Entry

# Importância de cada tipo de característica. O peso é dividido entre as
# características do mesmo tipo (um filme com 4 gêneros não "vale" mais que um com 1).
KIND_WEIGHTS = {"genre": 1.0, "director": 1.0, "cast": 0.6, "kw": 0.8, "decade": 0.4, "lang": 0.3}
W_CONTENT, W_COLLAB, W_QUALITY = 0.50, 0.35, 0.15


@dataclass
class Feature:
    key: str     # ex.: "genre:878"
    kind: str    # genre, director, cast, kw, decade, lang
    label: str   # ex.: "Ficção científica"
    weight: float


def movie_features(movie: dict) -> list[Feature]:
    groups: dict[str, list[tuple[str, str]]] = {
        "genre": [(f"genre:{g['id']}", g["name"]) for g in movie.get("genres", []) if g.get("id")],
        "director": [(f"director:{d['id']}", d["name"]) for d in movie.get("directors", [])[:2]],
        "cast": [(f"cast:{c['id']}", c["name"]) for c in movie.get("cast", [])[:5]],
        "kw": [(f"kw:{k['id']}", k["name"]) for k in movie.get("keywords", [])[:12]],
    }
    if movie.get("year"):
        decade = movie["year"] // 10 * 10
        groups["decade"] = [(f"decade:{decade}", f"anos {decade}")]
    if movie.get("original_language"):
        groups["lang"] = [(f"lang:{movie['original_language']}", movie["original_language"])]

    features: list[Feature] = []
    for kind, items in groups.items():
        if items:
            weight = KIND_WEIGHTS[kind] / math.sqrt(len(items))
            features += [Feature(key, kind, label, weight) for key, label in items]
    return features


# ----------------------------------------------------------------- afinidade
def rating_center(entries: list[Entry]) -> float:
    """Nota 'típica' da pessoa (média), para entender o que é nota alta PARA ELA."""
    rated = [e.rating for e in entries if e.rating is not None]
    return sum(rated) / len(rated) if len(rated) >= 8 else 3.0


def affinity(entry: Entry, center: float) -> float:
    """Quanto este filme conta a favor (+) ou contra (-) de um tipo de filme."""
    if entry.rating is None:
        return 0.8 if entry.liked else 0.15   # sem nota: coração diz muito, só ter visto diz pouco
    absolute = (entry.rating - 3.0) / 2.0       # 3 estrelas = neutro
    relative = (entry.rating - center) / 2.0    # comparado com as notas habituais da pessoa
    value = 0.6 * absolute + 0.4 * relative + (0.3 if entry.liked else 0.0)
    return max(-1.25, min(1.3, value))


# -------------------------------------------------------------------- perfil
def build_profile(pairs: list[tuple[Entry, dict]], center: float) -> tuple[dict[str, float], dict[str, Feature]]:
    vector: dict[str, float] = {}
    labels: dict[str, Feature] = {}
    for entry, movie in pairs:
        aff = affinity(entry, center)
        for f in movie_features(movie):
            vector[f.key] = vector.get(f.key, 0.0) + aff * f.weight
            labels[f.key] = f
    return vector, labels


def _cosine(profile: dict[str, float], features: list[Feature]) -> float:
    if not features:
        return 0.0
    norm_p = math.sqrt(sum(v * v for v in profile.values()))
    norm_c = math.sqrt(sum(f.weight ** 2 for f in features))
    if not norm_p or not norm_c:
        return 0.0
    dot = sum(profile.get(f.key, 0.0) * f.weight for f in features)
    return max(0.0, dot / (norm_p * norm_c))


def summarize_profile(pairs, vector, labels, center) -> dict:
    """Resumo legível do gosto da pessoa, mostrado no topo da página."""
    genre_scores = sorted(
        ((v, labels[k].label) for k, v in vector.items() if k.startswith("genre:") and v > 0), reverse=True
    )[:6]
    top = genre_scores[0][0] if genre_scores else 1.0
    director_counts: dict[str, int] = {}
    for entry, movie in pairs:
        if affinity(entry, center) > 0:
            for d in movie.get("directors", [])[:2]:
                director_counts[d["name"]] = director_counts.get(d["name"], 0) + 1
    directors = sorted(((n, c) for n, c in director_counts.items() if c >= 2), key=lambda x: (-x[1], x[0]))[:5]
    rated = [e.rating for e, _ in pairs if e.rating is not None]
    return {
        "genres": [{"name": name, "share": round(score / top, 3)} for score, name in genre_scores],
        "directors": [{"name": n, "films": c} for n, c in directors],
        "avg_rating": round(sum(rated) / len(rated), 2) if rated else None,
        "rated_count": len(rated),
        "liked_count": sum(1 for e, _ in pairs if e.liked),
    }


# --------------------------------------------------------------- recomendação
def _bayesian_quality(movie: dict) -> float:
    """Nota do TMDB suavizada: poucos votos puxam a nota para a média geral (6.5)."""
    votes, avg, prior_votes, prior_avg = movie.get("vote_count", 0), movie.get("vote_average", 0.0), 500, 6.5
    score = (votes / (votes + prior_votes)) * avg + (prior_votes / (votes + prior_votes)) * prior_avg
    return max(0.0, min(1.0, (score - 5.0) / 4.0))


def _already_watched(movie: dict, ids: set[int], keys: set[tuple[str, int | None]], no_year: set[str]) -> bool:
    if movie["id"] in ids:
        return True
    for title in (movie.get("title"), movie.get("original_title")):
        norm = normalize_title(title)
        if not norm:
            continue
        if (norm, movie.get("year")) in keys or norm in no_year:
            return True
    return False


def _reasons(movie: dict, profile: dict[str, float], sources: list[tuple[float, str]], from_discover: bool) -> list[str]:
    reasons: list[str] = []
    titles: list[str] = []
    for _, title in sorted(sources, reverse=True):
        if title not in titles:
            titles.append(title)
        if len(titles) == 2:
            break
    if titles:
        reasons.append("Porque você gostou de " + " e ".join(titles))

    contribs = sorted(((profile.get(f.key, 0.0) * f.weight, f) for f in movie_features(movie)), key=lambda x: -x[0])
    positive = [f for c, f in contribs if c > 0]
    director = next((f for f in positive if f.kind == "director"), None)
    if director:
        reasons.append(f"Direção de {director.label}, que aparece nos seus filmes favoritos")
    genres = [f.label for f in positive if f.kind == "genre"][:2]
    if genres:
        reasons.append("Gêneros em comum com o seu gosto: " + ", ".join(genres))
    actor = next((f for f in positive if f.kind == "cast"), None)
    if actor and len(reasons) < 3:
        reasons.append(f"Com {actor.label}")
    if not reasons and from_discover:
        reasons.append("Muito bem avaliado nos seus gêneros preferidos")
    return reasons[:3]


def recommend(
    entries: list[Entry],
    get_movie: Callable[[int], dict | None],
    get_related: Callable[[int], list[dict]],
    discover: Callable[[list[int]], list[dict]],
    prefetch: Callable[[list[int]], None] = lambda ids: None,
    num_recs: int = 60,
    max_seeds: int = 40,
    max_candidates: int = 150,
    min_votes: int = 150,
    log: Callable[[str], None] = lambda msg: None,
) -> tuple[list[dict], dict]:
    """Devolve (lista de recomendações ordenada, resumo do perfil)."""
    watched_ids = {e.tmdb_id for e in entries if e.tmdb_id}
    watched_keys = {(normalize_title(e.title), e.year) for e in entries}
    watched_no_year = {normalize_title(e.title) for e in entries if not e.year}

    pairs = []
    for e in entries:
        movie = get_movie(e.tmdb_id) if e.tmdb_id else None
        if movie:
            pairs.append((e, movie))
    if not pairs:
        return [], {"genres": [], "directors": [], "avg_rating": None, "rated_count": 0, "liked_count": 0}

    center = rating_center([e for e, _ in pairs])
    vector, labels = build_profile(pairs, center)
    summary = summarize_profile(pairs, vector, labels, center)

    # --- 2. candidatos a partir das sementes
    seeds = sorted(
        ((affinity(e, center), e.last_watched or "", e, m) for e, m in pairs), key=lambda t: (t[0], t[1]), reverse=True
    )
    seeds = [s for s in seeds if s[0] > 0][:max_seeds]
    log(f"  {len(seeds)} filmes-semente; buscando recomendações do TMDB para cada um...")

    pool: dict[int, dict] = {}
    for aff, _, entry, movie in seeds:
        for rank, rel in enumerate(get_related(movie["id"])):
            cid = rel["id"]
            if cid in watched_ids or cid == movie["id"]:
                continue
            slot = pool.setdefault(cid, {"collab": 0.0, "sources": [], "discover": False})
            weight = aff / (1 + 0.1 * rank)  # as primeiras posições da lista do TMDB valem mais
            slot["collab"] += weight
            slot["sources"].append((weight, movie["title"]))

    chosen = sorted(pool, key=lambda cid: pool[cid]["collab"], reverse=True)[:max_candidates]

    # --- plano B: poucos candidatos (histórico pequeno) -> completa com os mais bem avaliados dos gêneros favoritos
    if len(chosen) < num_recs * 1.5:
        top_genres = [int(k.split(":")[1]) for k, v in sorted(vector.items(), key=lambda kv: -kv[1])
                      if k.startswith("genre:") and v > 0][:2]
        if top_genres:
            log("  Poucos candidatos pelas recomendações; completando com os mais bem avaliados dos seus gêneros...")
            for rel in discover(top_genres):
                if rel["id"] not in watched_ids and rel["id"] not in pool:
                    pool[rel["id"]] = {"collab": 0.0, "sources": [], "discover": True}
                    chosen.append(rel["id"])

    log(f"  Analisando {len(chosen)} candidatos...")
    prefetch(chosen)

    # --- 3. pontuação
    max_collab = max((pool[c]["collab"] for c in chosen), default=0.0)
    w_content, w_collab, w_quality = (W_CONTENT, W_COLLAB, W_QUALITY) if max_collab > 0 else (0.8, 0.0, 0.2)
    current_year = date.today().year

    scored: list[dict] = []
    for cid in chosen:
        movie = get_movie(cid)
        # --- 4. filtros
        if not movie or movie.get("adult") or not movie.get("year") or movie["year"] > current_year:
            continue
        if movie.get("vote_count", 0) < min_votes:
            continue
        if _already_watched(movie, watched_ids, watched_keys, watched_no_year):
            continue

        slot = pool[cid]
        content = _cosine(vector, movie_features(movie))
        collab = slot["collab"] / max_collab if max_collab > 0 else 0.0
        quality = _bayesian_quality(movie)
        final = w_content * content + w_collab * collab + w_quality * quality

        scored.append({
            "id": cid,
            "title": movie["title"],
            "original_title": movie["original_title"],
            "year": movie["year"],
            "overview": movie["overview"],
            "genres": [g["name"] for g in movie["genres"]],
            "runtime": movie["runtime"],
            "poster_path": movie["poster_path"],
            "vote_average": round(movie["vote_average"], 1),
            "vote_count": movie["vote_count"],
            "directors": [d["name"] for d in movie["directors"]],
            "score": round(final, 4),
            "match": max(1, min(99, round(final * 100))),
            "reasons": _reasons(movie, vector, slot["sources"], slot["discover"]),
        })

    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:num_recs], summary
