from geostate.db import read_sql, split_statements


def test_split_statements_drops_comments_and_empty_chunks():
    sql = "-- header %(city)s\nSELECT 1;\n\n-- only a comment;\nSELECT 2\nFROM t;\n"
    assert split_statements(sql) == ["SELECT 1", "SELECT 2\nFROM t"]


def test_packaged_sql_files_split_into_statements():
    # data-modifying CTE (geo QA) must stay one statement
    core = split_statements(read_sql("core.sql"))
    assert any(s.startswith("WITH pts AS") and "INSERT INTO core.listings" in s for s in core)
    assert all("--" not in s.splitlines()[0] for s in core)
    assert len(split_statements(read_sql("marts.sql"))) == 6
