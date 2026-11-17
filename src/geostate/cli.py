"""Command line entry point.

    geostate init-db                      create schemas and tables (idempotent)
    geostate run --city Madrid            full pipeline, all steps in order
    geostate <step> --city Madrid         one step: stage | core | marts | clickhouse
    geostate trends --city Madrid         price change per neighbourhood (ClickHouse)
    geostate model --city Madrid          price model vs. honeycomb baseline (offline)
    geostate segments --city Madrid       k-means market segments of H3 cells (offline)

Exit codes: 0 ok, 2 quality gate failed (data problem: do not retry), 1 other errors.
Each step is the contract an orchestrator runs: Airflow calls these same commands.
"""

from __future__ import annotations

import argparse
import json
import logging

from geostate import clickhouse, extract, pipeline, price_model, segments
from geostate.config import get_settings
from geostate.transform import DataQualityError

CITIES = ("Madrid", "Barcelona", "Valencia")  # available in idealista18
EXIT_QUALITY_GATE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geostate")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create schemas and tables (idempotent)")
    for name, help_text in [
        ("run", "run every step in order"),
        *((step, f"run only the '{step}' step") for step in pipeline.STEPS),
        ("trends", "price change per neighbourhood between first and last quarter"),
        ("model", "train the price model and compare it with the honeycomb baseline"),
        ("segments", "group H3 cells into market segments with k-means"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--city", choices=CITIES, default="Madrid")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    log = logging.getLogger("geostate")

    if args.command == "init-db":
        pipeline.init_db(settings)
        return 0
    if args.command == "trends":
        print(clickhouse.trends(args.city, settings).to_string(index=False))
        return 0
    if args.command in ("model", "segments"):
        source = extract.download_source(
            args.city, "Sale", settings.source_base_url, settings.data_dir
        )
        listings = price_model.build_dataset(
            extract.read_rda(source), settings.h3_fine_res, settings.h3_coarse_res
        )
        if args.command == "model":
            result = price_model.evaluate(listings)
        else:
            result = segments.segment(listings)
            result.pop("cell_segments")  # one row per cell: too long for the terminal
        print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
        return 0

    try:
        if args.command == "run":
            stats = pipeline.run(args.city, settings)
        else:
            pipeline.init_db(settings)
            stats = pipeline.STEPS[args.command](args.city, settings)
    except DataQualityError as exc:
        log.error("quality gate failed, nothing published: %s", exc)
        return EXIT_QUALITY_GATE
    print(json.dumps(stats, indent=2, default=str, ensure_ascii=False))
    return 0
