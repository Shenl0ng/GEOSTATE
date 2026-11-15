import pytest

from geostate.cli import build_parser
from geostate.clickhouse import partition_filter
from geostate.db import read_sql, split_statements
from geostate.pipeline import STEPS


def test_partition_filter_matches_clickhouse_tuple_text():
    assert partition_filter("Madrid") == "('Madrid',%"


@pytest.mark.parametrize("bad", ["Madrid'); DROP TABLE x; --", "a%b", ""])
def test_partition_filter_rejects_unsafe_city_names(bad):
    with pytest.raises(ValueError):
        partition_filter(bad)


def test_clickhouse_schema_has_table_aggregate_and_view():
    statements = split_statements(read_sql("clickhouse_schema.sql"))
    assert [s.split("(")[0].split()[-1] for s in statements[:2]] == [
        "listings",
        "zone_quarter_stats",
    ]
    assert "MATERIALIZED VIEW" in statements[2]


def test_every_pipeline_step_is_a_cli_command():
    parser = build_parser()
    for step in STEPS:
        assert parser.parse_args([step, "--city", "Madrid"]).command == step


def test_steps_run_in_dependency_order():
    assert list(STEPS) == ["stage", "core", "marts", "clickhouse"]
