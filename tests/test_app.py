"""Testes que rodam SEM internet: usam um TMDB falso e um RSS de exemplo.

    python -m unittest discover -s tests -v
"""
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lbrecs.cleaning import clean_entries, merge_entries, normalize_title
from lbrecs.config import Settings, clean_username
from lbrecs.db import Database
from lbrecs.letterboxd import parse_rss, read_export
from lbrecs.matching import pick_best, resolve
from lbrecs.models import Entry
from lbrecs.pipeline import run_update
from lbrecs.web import create_app

SAMPLE_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:letterboxd="https://letterboxd.com" xmlns:tmdb="https://themoviedb.org"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
 <channel><title>Letterboxd - fulano</title>
  <item>
   <title>Blade Runner, 1982 - ★★★★½</title>
   <link>https://letterboxd.com/fulano/film/blade-runner/</link>
   <pubDate>Sat, 03 Aug 2024 10:00:00 +1200</pubDate>
   <letterboxd:watchedDate>2024-08-03</letterboxd:watchedDate>
   <letterboxd:rewatch>No</letterboxd:rewatch>
   <letterboxd:filmTitle>Blade Runner</letterboxd:filmTitle>
   <letterboxd:filmYear>1982</letterboxd:filmYear>
   <letterboxd:memberRating>4.5</letterboxd:memberRating>
   <letterboxd:memberLike>Yes</letterboxd:memberLike>
   <tmdb:movieId>78</tmdb:movieId>
  </item>
  <item>
   <title>Alien, 1979 - ★★★½</title>
   <link>https://letterboxd.com/fulano/film/alien/</link>
   <pubDate>Fri, 02 Aug 2024 10:00:00 +1200</pubDate>
  </item>
  <item>
   <title>Minha lista de ficção</title>
   <link>https://letterboxd.com/fulano/list/minha-lista/</link>
   <pubDate>Fri, 02 Aug 2024 10:00:00 +1200</pubDate>
  </item>
  <item>
   <title>Sem formato de filme</title>
   <link>https://letterboxd.com/fulano/</link>
  </item>
 </channel></rss>""".encode("utf-8")


# ------------------------------------------------------------------ TMDB falso
def raw(tmdb_id, title, year, genres, director, cast=(), votes=2000, avg=7.5, lang="en", original=None):
    return {
        "id": tmdb_id, "title": title, "original_title": original or title, "release_date": f"{year}-06-01",
        "overview": f"Sinopse de {title}.", "genres": [{"id": g, "name": n} for g, n in genres],
        "runtime": 110, "poster_path": f"/p{tmdb_id}.jpg", "vote_average": avg, "vote_count": votes,
        "original_language": lang, "adult": False, "popularity": 50,
        "credits": {"crew": [{"id": 1000 + director, "name": f"Diretor {director}", "job": "Director"}],
                    "cast": [{"id": 5000 + c, "name": f"Ator {c}"} for c in cast]},
        "keywords": {"keywords": []},
    }


SCIFI, DRAMA, COMEDY = (878, "Ficção científica"), (18, "Drama"), (35, "Comédia")


class FakeTMDB:
    """Imita o TMDBClient com um catálogo pequeno: 8 ficções, 6 dramas, 6 comédias."""

    def __init__(self):
        self.movies = {}
        for i in range(1, 9):
            self.movies[i] = raw(i, f"Sci {i}", 1980 + i, [SCIFI], director=1 if i < 5 else 2, cast=(1, 2))
        for i in range(11, 17):
            self.movies[i] = raw(i, f"Drama {i}", 1990 + i, [DRAMA], director=3, cast=(3,))
        for i in range(21, 27):
            self.movies[i] = raw(i, f"Comedy {i}", 2000 + i - 20, [COMEDY], director=4, cast=(4,))
        self.movies[99] = raw(99, "Dune", 2021, [SCIFI], director=5)
        self.movies[98] = raw(98, "Dune", 1984, [SCIFI], director=6)
        self.movies[97] = raw(97, "Amélie", 2001, [COMEDY], director=7, original="Le Fabuleux Destin d'Amélie Poulain")
        self.movies[96] = raw(96, "Obscuro", 2010, [SCIFI], director=1, votes=3)   # poucos votos
        self.calls = 0

    def check_key(self):
        pass

    def search_movie(self, query, year=None):
        self.calls += 1
        q = normalize_title(query)
        out = []
        for m in self.movies.values():
            if q in normalize_title(m["title"]) or q in normalize_title(m["original_title"]):
                if not year or abs(int(m["release_date"][:4]) - year) <= 1:
                    out.append(m)
        return out

    def movie_details(self, tmdb_id, language=None):
        self.calls += 1
        return self.movies.get(tmdb_id)

    def recommendations(self, tmdb_id):
        self.calls += 1
        genre = self.movies[tmdb_id]["genres"][0]["id"]
        same = [m for m in self.movies.values() if m["genres"][0]["id"] == genre and m["id"] != tmdb_id]
        return sorted(same, key=lambda m: m["id"])[:20]

    def discover(self, genre_ids, pages=2, min_votes=1000):
        return [m for m in self.movies.values() if m["genres"][0]["id"] in genre_ids]


def make_settings(tmp):
    return Settings(letterboxd_user="fulano", tmdb_api_key="x", db_path=Path(tmp) / "t.db",
                    manual_matches_path=Path(tmp) / "manual.csv", enriched_csv_path=Path(tmp) / "out.csv",
                    num_recommendations=20, min_votes=150)


# ------------------------------------------------------------------------ testes
class RssTests(unittest.TestCase):
    def test_parse_rss(self):
        entries = parse_rss(SAMPLE_RSS)
        self.assertEqual(len(entries), 2)  # lista e item sem formato de filme foram ignorados
        br, alien = entries
        self.assertEqual((br.title, br.year, br.rating, br.liked, br.tmdb_id), ("Blade Runner", 1982, 4.5, True, 78))
        self.assertEqual(br.last_watched, "2024-08-03")
        # Sem tags extras: título/ano/nota vêm do <title>, data vem do pubDate
        self.assertEqual((alien.title, alien.year, alien.rating, alien.tmdb_id), ("Alien", 1979, 3.5, None))
        self.assertEqual(alien.last_watched, "2024-08-02")

    def test_invalid_xml(self):
        from lbrecs.letterboxd import LetterboxdError
        with self.assertRaises(LetterboxdError):
            parse_rss(b"<html>not xml")

    def test_read_export_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("watched.csv", "Date,Name,Year,Letterboxd URI\n2020-01-01,Heat,1995,https://boxd.it/a\n"
                                       "2020-01-02,Alien,1979,https://boxd.it/b\n")
            zf.writestr("ratings.csv", "Date,Name,Year,Letterboxd URI,Rating\n2020-01-02,Alien,1979,https://boxd.it/b,4.5\n"
                                       "2020-01-03,Ran,1985,https://boxd.it/c,5\n")
            zf.writestr("likes/films.csv", "Date,Name,Year,Letterboxd URI\n2020-01-02,Alien,1979,https://boxd.it/b\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            path.write_bytes(buf.getvalue())
            entries = {e.title: e for e in read_export(path)}
        self.assertEqual(set(entries), {"Heat", "Alien", "Ran"})
        self.assertIsNone(entries["Heat"].rating)
        self.assertEqual(entries["Alien"].rating, 4.5)
        self.assertTrue(entries["Alien"].liked)
        self.assertFalse(entries["Heat"].liked)


class CleaningTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_title("  Amélie "), normalize_title("The Amelie!"))
        self.assertEqual(normalize_title("Fast & Furious"), normalize_title("fast and furious"))

    def test_clean_invalid_values(self):
        out = clean_entries([Entry("  X\u200b  Y ", year=3000, rating=7, last_watched="ontem"), Entry("   ")])
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].title, out[0].year, out[0].rating, out[0].last_watched), ("X Y", None, None, None))

    def test_merge_rewatches(self):
        merged = merge_entries([
            Entry("Heat", 1995, rating=3.0, last_watched="2020-01-01"),
            Entry("heat", 1995, rating=4.5, last_watched="2023-01-01", liked=True),
            Entry("Heat", 1995, rating=None, last_watched="2024-01-01"),
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0].rating, merged[0].liked, merged[0].last_watched), (4.5, True, "2024-01-01"))

    def test_username(self):
        self.assertEqual(clean_username("https://letterboxd.com/fulano/"), "fulano")
        self.assertEqual(clean_username("@fulano"), "fulano")


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeTMDB()

    def test_accents_and_original_title(self):
        self.assertEqual(resolve(self.client, "Amelie", 2001)[0], 97)

    def test_year_disambiguates_remakes(self):
        self.assertEqual(resolve(self.client, "Dune", 1984)[0], 98)
        self.assertEqual(resolve(self.client, "Dune", 2021)[0], 99)

    def test_missing_year_still_works(self):
        self.assertIsNotNone(resolve(self.client, "Amelie", None)[0])

    def test_unknown_title_is_rejected(self):
        self.assertIsNone(resolve(self.client, "Filme Que Não Existe", 2001)[0])

    def test_wrong_year_far_away_is_rejected(self):
        match, score = pick_best("Dune", 1950, [self.client.movies[99]])
        self.assertLess(score, 0.72)


class PipelineTests(unittest.TestCase):
    def run_pipeline(self, tmp, client, rss):
        logs = []
        stats = run_update(make_settings(tmp), client, fetch_rss_fn=lambda user: rss, log=logs.append)
        return stats, logs

    def sample_history(self):
        return [
            # com ID do TMDB (como no RSS)
            Entry("Sci 1", 1981, 5.0, True, "2024-01-01", tmdb_id=1),
            Entry("Sci 2", 1982, 4.5, False, "2024-01-02", tmdb_id=2),
            Entry("Sci 3", 1983, 4.0, False, "2024-01-03", tmdb_id=3),
            Entry("Drama 11", 2001, 2.0, False, "2024-01-04", tmdb_id=11),
            Entry("Comedy 21", 2001, 1.5, False, "2024-01-05", tmdb_id=21),
            # sem ID: precisa casar por título/ano (com acento diferente)
            Entry("Amelie", 2001, 3.0, False, "2024-01-06"),
            # não existe no TMDB
            Entry("Filme Inexistente", 1999, 4.0, False, "2024-01-07"),
            # sem nota e sem ano
            Entry("Dune", None, None, False, "2024-01-08"),
        ]

    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeTMDB()
            stats, logs = self.run_pipeline(tmp, client, self.sample_history())
            self.assertGreater(stats["recommendations"], 0)

            db = Database(Path(tmp) / "t.db")
            recs = db.load_recommendations()
            ids = [r["id"] for r in recs]
            watched_ids = {e.tmdb_id for e in db.load_watched() if e.tmdb_id}

            self.assertFalse(set(ids) & watched_ids, "recomendou filme já assistido")
            self.assertEqual(len(ids), len(set(ids)), "recomendação duplicada")
            self.assertNotIn(96, ids, "filme com poucos votos não deveria aparecer")
            self.assertTrue({4, 5, 6}.issubset(set(ids)), "ficção científica (gosto do usuário) deveria aparecer")
            # gosto por ficção científica: a melhor sugestão deve ser do gênero
            self.assertIn("Ficção científica", recs[0]["genres"])
            self.assertTrue(recs[0]["reasons"])
            # Dune sem ano foi assistido: nenhum Dune pode ser sugerido, seja qual for o ano
            self.assertFalse({98, 99} & set(ids), "Dune já assistido (sem ano) foi sugerido")

            # não identificados ficam registrados
            unresolved = [u["title"] for u in db.get_meta("unresolved")]
            self.assertIn("Filme Inexistente", unresolved)
            self.assertNotIn("Amelie", unresolved)  # casou com "Amélie"
            self.assertTrue((Path(tmp) / "out.csv").exists())
            db.close()

    def test_second_run_uses_cache_and_keeps_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeTMDB()
            self.run_pipeline(tmp, client, self.sample_history())
            first_calls = client.calls
            # segunda rodada: RSS só com 1 filme novo; o histórico antigo deve ser preservado
            self.run_pipeline(tmp, client, [Entry("Sci 4", 1984, 4.0, False, "2024-02-01", tmdb_id=4)])
            db = Database(Path(tmp) / "t.db")
            titles = {e.title for e in db.load_watched()}
            self.assertTrue({"Sci 1", "Sci 4", "Filme Inexistente"}.issubset(titles))
            self.assertNotIn(4, [r["id"] for r in db.load_recommendations()])  # agora assistido
            self.assertLess(client.calls - first_calls, first_calls, "o cache deveria reduzir as chamadas")
            db.close()

    def test_changing_user_starts_a_fresh_base(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeTMDB()
            self.run_pipeline(tmp, client, self.sample_history())
            other = replace(make_settings(tmp), letterboxd_user="outra_pessoa")
            run_update(other, client, fetch_rss_fn=lambda u: [Entry("Comedy 22", 2002, 4.0, tmdb_id=22)], log=lambda m: None)
            db = Database(Path(tmp) / "t.db")
            self.assertEqual({e.title for e in db.load_watched()}, {"Comedy 22"})
            self.assertEqual(db.get_meta("user"), "outra_pessoa")
            db.close()

    def test_manual_match_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "manual.csv").write_text("title,year,tmdb_id\nFilme Inexistente,1999,5\n", encoding="utf-8")
            self.run_pipeline(tmp, FakeTMDB(), self.sample_history())
            db = Database(Path(tmp) / "t.db")
            by_title = {e.title: e for e in db.load_watched()}
            self.assertEqual(by_title["Filme Inexistente"].tmdb_id, 5)
            db.close()

    def test_tiny_history_falls_back_to_discover(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats, _ = self.run_pipeline(tmp, FakeTMDB(), [Entry("Sci 1", 1981, 5.0, True, tmdb_id=1)])
            self.assertGreater(stats["recommendations"], 0)

    def test_no_data_at_all_raises(self):
        from lbrecs.pipeline import PipelineError
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PipelineError):
                self.run_pipeline(tmp, FakeTMDB(), [])


class WebTests(unittest.TestCase):
    def test_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = create_app(Path(tmp) / "t.db").test_client()
            self.assertIn("Ainda não há recomendações", empty.get("/").get_data(as_text=True))

            PipelineTests().run_pipeline(tmp, FakeTMDB(), PipelineTests().sample_history())
            client = create_app(Path(tmp) / "t.db").test_client()
            html = client.get("/").get_data(as_text=True)
            self.assertEqual(client.get("/").status_code, 200)
            self.assertIn("Filmes para fulano", html)
            self.assertIn("Sci 4", html)
            self.assertIn("Filme Inexistente", html)  # aparece na lista de não identificados
            data = client.get("/api/recommendations").get_json()
            self.assertGreater(len(data["recommendations"]), 0)

    def test_web_drops_films_watched_after_last_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            PipelineTests().run_pipeline(tmp, FakeTMDB(), PipelineTests().sample_history())
            db = Database(Path(tmp) / "t.db")
            db.replace_watched(db.load_watched() + [Entry("Sci 4", 1984, 5.0, tmdb_id=4)])
            db.close()
            data = create_app(Path(tmp) / "t.db").test_client().get("/api/recommendations").get_json()
            self.assertNotIn(4, [r["id"] for r in data["recommendations"]])


if __name__ == "__main__":
    unittest.main()
