"""Database helpers: connections, SQL files, bulk COPY."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from importlib import resources
from typing import Any

import pandas as pd
import psycopg

_STATEMENT_END = re.compile(r";\s*(?:\n|$)")


def connect(dsn: str) -> psycopg.Connection:
    # autocommit: every write block opens an explicit `with conn.transaction()`.
    return psycopg.connect(dsn, autocommit=True)


def read_sql(name: str) -> str:
    return resources.files("geostate.sql").joinpath(name).read_text(encoding="utf8")


def split_statements(sql: str) -> list[str]:
    """Split a SQL script on ``;`` at end of line and strip full-line ``--`` comments.

    Comments are removed so text such as ``%(city)s`` inside them is never parsed as a
    query parameter. Statements must not contain ``;`` at end of a line in string literals.
    """
    statements = []
    for chunk in _STATEMENT_END.split(sql):
        code = "\n".join(line for line in chunk.splitlines() if not line.strip().startswith("--"))
        if code.strip():
            statements.append(code.strip())
    return statements


def run_sql_file(
    conn: psycopg.Connection, name: str, params: Mapping[str, Any] | None = None
) -> None:
    """Run each statement of a packaged SQL file (caller controls the transaction)."""
    with conn.cursor() as cur:
        for statement in split_statements(read_sql(name)):
            cur.execute(statement, params)


def copy_rows(
    conn: psycopg.Connection, table: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]
) -> int:
    """Bulk load with COPY (much faster than INSERTs). Returns rows written."""
    cols = ", ".join(columns)
    n = 0
    with conn.cursor() as cur, cur.copy(f"COPY {table} ({cols}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
            n += 1
    return n


def frame_rows(df: pd.DataFrame, columns: Sequence[str]) -> Iterable[tuple[Any, ...]]:
    """DataFrame rows as plain Python tuples, with NaN/NA -> None."""
    clean = df[list(columns)].astype(object).where(df[list(columns)].notna(), None)
    return clean.itertuples(index=False, name=None)
