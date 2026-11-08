#!/usr/bin/env python3
"""Ponto de entrada. Uso:

    python main.py update                      baixa seu histórico, enriquece e calcula as recomendações
    python main.py update --export arq.zip     o mesmo, incluindo a exportação completa do Letterboxd
    python main.py serve                       abre a página web em http://127.0.0.1:8000
    python main.py run                         update + serve de uma vez
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from lbrecs.config import ConfigError, load_settings
from lbrecs.letterboxd import LetterboxdError
from lbrecs.pipeline import PipelineError, run_update
from lbrecs.tmdb import TMDBClient, TMDBError


def cmd_update(args) -> None:
    settings = load_settings()
    client = TMDBClient(settings.tmdb_api_key, settings.tmdb_language)
    run_update(settings, client, export_path=args.export)


def cmd_serve(args) -> None:
    from lbrecs.web import create_app

    settings = load_settings(require_user=False, require_tmdb=False)
    if not settings.db_path.exists():
        print("Ainda não há dados. Rode primeiro: python main.py update")
    url = f"http://{args.host}:{args.port}"
    print(f"Abrindo {url}  (Ctrl+C para parar)")
    if args.open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    create_app(settings.db_path).run(host=args.host, port=args.port, debug=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Recomendações de filmes a partir do seu Letterboxd.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_update = sub.add_parser("update", help="atualiza a base e recalcula as recomendações")
    p_update.add_argument("--export", metavar="ARQUIVO", help=".zip (ou pasta/.csv) da exportação do Letterboxd")
    p_update.set_defaults(func=cmd_update)

    for name, helptext in (("serve", "abre a página web"), ("run", "atualiza e abre a página web")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=8000)
        p.add_argument("--open", action="store_true", help="abre o navegador automaticamente")
        if name == "run":
            p.add_argument("--export", metavar="ARQUIVO", help="exportação do Letterboxd (opcional)")
        p.set_defaults(func=cmd_serve if name == "serve" else None)

    args = parser.parse_args()
    try:
        if args.command == "run":
            cmd_update(args)
            cmd_serve(args)
        else:
            args.func(args)
    except (ConfigError, LetterboxdError, TMDBError, PipelineError) as exc:
        print(f"\nErro: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nEncerrado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
