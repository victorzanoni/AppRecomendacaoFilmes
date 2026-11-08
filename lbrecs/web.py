"""Página web (Flask) que mostra as recomendações já calculadas.

Ela só LÊ o banco local: não precisa de chave do TMDB nem de internet para
funcionar (apenas os pôsteres são carregados do servidor de imagens do TMDB).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template

from .cleaning import normalize_title
from .config import default_db_path
from .db import Database

POSTER_BASE = "https://image.tmdb.org/t/p/w342"


def _load_view_data(db_path: Path) -> dict:
    db = Database(db_path)
    try:
        recs = db.load_recommendations()
        ids, keys, no_year = db.watched_filters()
        # Segunda barreira: nunca mostrar algo que está na lista de assistidos,
        # mesmo que a base tenha mudado desde o último cálculo.
        recs = [
            r for r in recs
            if r["id"] not in ids
            and (normalize_title(r["title"]), r["year"]) not in keys
            and normalize_title(r["title"]) not in no_year
        ]
        updated = db.get_meta("updated_at")
        return {
            "recs": recs,
            "user": db.get_meta("user", ""),
            "updated_at": datetime.fromisoformat(updated).strftime("%d/%m/%Y %H:%M") if updated else None,
            "watched_total": db.get_meta("watched_total", 0),
            "summary": db.get_meta("summary", {}) or {},
            "unresolved": db.get_meta("unresolved", []) or [],
            "genres": sorted({g for r in recs for g in r["genres"]}),
        }
    finally:
        db.close()


def create_app(db_path: str | Path | None = None) -> Flask:
    app = Flask(__name__)
    path = Path(db_path) if db_path else default_db_path()

    @app.get("/")
    def index():
        return render_template("index.html", poster_base=POSTER_BASE, **_load_view_data(path))

    @app.get("/api/recommendations")
    def api():
        data = _load_view_data(path)
        return jsonify(user=data["user"], updated_at=data["updated_at"], recommendations=data["recs"])

    return app
