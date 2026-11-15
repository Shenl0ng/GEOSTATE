"""Command line entry point: ``geostate init-db`` / ``geostate run --city Madrid``."""

from __future__ import annotations

import argparse
import json
import logging

from geostate import pipeline
from geostate.config import get_settings
from geostate.transform import DataQualityError

CITIES = ("Madrid", "Barcelona", "Valencia")  # available in idealista18


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="geostate")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create schemas and tables (idempotent)")
    run_p = sub.add_parser("run", help="run the full pipeline for one city")
    run_p.add_argument("--city", choices=CITIES, default="Madrid")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()

    if args.command == "init-db":
        pipeline.init_db(settings)
        return 0

    try:
        stats = pipeline.run(args.city, settings)
    except DataQualityError as exc:
        logging.getLogger("geostate").error("quality gate failed, nothing published: %s", exc)
        return 2
    print(json.dumps(stats, indent=2, default=str, ensure_ascii=False))
    return 0
