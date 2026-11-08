"""Cliente mínimo da API v3 do TMDB (https://developer.themoviedb.org/docs).

Aceita os dois tipos de credencial que o TMDB oferece:
  * "API Key" (v3, string curta de 32 caracteres)  -> enviada como ?api_key=
  * "API Read Access Token" (começa com 'eyJ')     -> enviada como Bearer
"""
from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://api.themoviedb.org/3"


class TMDBError(Exception):
    """Falha ao falar com o TMDB (mensagem pensada para o usuário final)."""


class TMDBClient:
    def __init__(self, api_key: str, language: str = "pt-BR", timeout: int = 20):
        self.api_key = api_key.strip()
        self.language = language
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        if self.api_key.startswith("eyJ"):
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        # Tenta de novo sozinho em caso de limite de requisições (429) ou erro do servidor.
        retry = Retry(
            total=4,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))

    # ------------------------------------------------------------------ interno
    def _get(self, path: str, params: dict | None = None, language: str | None = None):
        """GET na API. Devolve o JSON, ou None se o recurso não existir (404)."""
        query = dict(params or {})
        query["language"] = language or self.language
        if "Authorization" not in self.session.headers:
            query["api_key"] = self.api_key
        try:
            resp = self.session.get(f"{BASE_URL}{path}", params=query, timeout=self.timeout)
        except requests.RequestException as exc:
            # Não inclui o texto original do erro: ele traz a URL, e a URL traz a sua chave.
            raise TMDBError(
                f"Não consegui acessar o TMDB ({type(exc).__name__}). Verifique sua conexão com a internet "
                "e tente de novo."
            ) from None

        if resp.status_code == 401:
            raise TMDBError(
                "O TMDB recusou a chave (HTTP 401). Confira o TMDB_API_KEY no .env: "
                "use a 'Chave da API' ou o 'Token de Leitura da API' de themoviedb.org/settings/api."
            )
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise TMDBError(f"O TMDB respondeu HTTP {resp.status_code} em {path}.")
        return resp.json()

    # ------------------------------------------------------------------ público
    def check_key(self) -> None:
        """Falha cedo (e com mensagem clara) se a chave não funcionar."""
        self._get("/configuration")

    def search_movie(self, query: str, year: int | None = None) -> list[dict]:
        """Busca por título. Usa inglês porque o Letterboxd usa títulos em inglês/originais."""
        params: dict = {"query": query, "include_adult": "false"}
        if year:
            params["year"] = year
        data = self._get("/search/movie", params, language="en-US")
        return (data or {}).get("results", [])

    def movie_details(self, tmdb_id: int, language: str | None = None) -> dict | None:
        """Detalhes + elenco/equipe + palavras-chave numa única chamada."""
        return self._get(f"/movie/{tmdb_id}", {"append_to_response": "credits,keywords"}, language)

    def recommendations(self, tmdb_id: int) -> list[dict]:
        """Filmes que o TMDB recomenda a partir deste (baseado no comportamento dos usuários)."""
        data = self._get(f"/movie/{tmdb_id}/recommendations", {"page": 1})
        return [r for r in (data or {}).get("results", []) if not r.get("adult")]

    def discover(self, genre_ids: list[int], pages: int = 2, min_votes: int = 1000) -> list[dict]:
        """Filmes bem avaliados dos gêneros dados (plano B quando faltam recomendações)."""
        results: list[dict] = []
        for page in range(1, pages + 1):
            data = self._get(
                "/discover/movie",
                {
                    "with_genres": ",".join(str(g) for g in genre_ids),  # vírgula = "todos os gêneros"
                    "sort_by": "vote_average.desc",
                    "vote_count.gte": min_votes,
                    "include_adult": "false",
                    "page": page,
                },
            )
            results.extend((data or {}).get("results", []))
        return results


def slim_movie(raw: dict) -> dict:
    """Reduz a resposta gigante do TMDB só ao que o app usa (e que cabe bem no cache)."""
    credits = raw.get("credits") or {}
    directors = [
        {"id": p["id"], "name": p["name"]}
        for p in credits.get("crew", [])
        if p.get("job") == "Director" and p.get("id") and p.get("name")
    ][:3]
    cast = [{"id": p["id"], "name": p["name"]} for p in credits.get("cast", [])[:8] if p.get("id") and p.get("name")]
    keywords = [
        {"id": k["id"], "name": k["name"]}
        for k in (raw.get("keywords") or {}).get("keywords", [])[:15]
        if k.get("id") and k.get("name")
    ]
    release = raw.get("release_date") or ""
    return {
        "id": raw["id"],
        "title": raw.get("title") or raw.get("original_title") or "",
        "original_title": raw.get("original_title") or "",
        "year": int(release[:4]) if release[:4].isdigit() else None,
        "overview": (raw.get("overview") or "").strip(),
        "genres": [{"id": g["id"], "name": g["name"]} for g in raw.get("genres", []) if g.get("id")],
        "runtime": raw.get("runtime") or None,
        "poster_path": raw.get("poster_path"),
        "vote_average": raw.get("vote_average") or 0.0,
        "vote_count": raw.get("vote_count") or 0,
        "original_language": raw.get("original_language") or "",
        "adult": bool(raw.get("adult")),
        "directors": directors,
        "cast": cast,
        "keywords": keywords,
    }
