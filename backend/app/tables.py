import re
from pathlib import Path

import duckdb

from app.db import DATA_DIR

DUCK_PATH = DATA_DIR / "tables.duckdb"


def make_table_name(filename: str) -> str:
    """orders.csv -> orders, 'Order Items (2).csv' -> order_items_2"""
    stem = Path(filename).stem.lower()
    name = re.sub(r"[^a-z0-9]+", "_", stem).strip("_") or "table"
    if name[0].isdigit():
        name = "t_" + name
    return name


def load_csv(path, filename: str) -> dict:
    """Load a CSV into DuckDB. Replaces any table with the same name."""
    DATA_DIR.mkdir(exist_ok=True)
    name = make_table_name(filename)
    safe_path = str(path).replace("'", "''")
    con = duckdb.connect(str(DUCK_PATH))
    try:
        con.execute(
            f'CREATE OR REPLACE TABLE "{name}" AS '
            f"SELECT * FROM read_csv_auto('{safe_path}', header=true, sample_size=-1)"
        )
        rows = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        cols = con.execute(f'DESCRIBE "{name}"').fetchall()
        columns = [{"name": c[0], "type": c[1]} for c in cols]
    finally:
        con.close()
    return {"table_name": name, "rows": rows, "columns": columns}


def drop_table(name: str) -> None:
    if not DUCK_PATH.exists():
        return
    con = duckdb.connect(str(DUCK_PATH))
    try:
        con.execute(f'DROP TABLE IF EXISTS "{name}"')
    finally:
        con.close()


def describe_tables(sample_rows: int = 3) -> list[dict]:
    """Name, columns, row count and a few sample rows for every loaded table."""
    if not DUCK_PATH.exists():
        return []
    con = duckdb.connect(str(DUCK_PATH), read_only=True)
    try:
        out = []
        for (name,) in con.execute("SHOW TABLES").fetchall():
            cols = con.execute(f'DESCRIBE "{name}"').fetchall()
            count = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            cur = con.execute(f'SELECT * FROM "{name}" LIMIT {sample_rows}')
            names = [d[0] for d in cur.description]
            sample = [dict(zip(names, r)) for r in cur.fetchall()]
            out.append(
                {
                    "table_name": name,
                    "row_count": count,
                    "columns": [{"name": c[0], "type": c[1]} for c in cols],
                    "sample_rows": sample,
                }
            )
        return out
    finally:
        con.close()


def run_query(sql: str, max_rows: int = 200) -> dict:
    """Run a read-only SELECT. Returns columns, rows and the SQL that ran."""
    s = sql.strip().rstrip(";")
    if not re.match(r"^(select|with)\b", s, re.IGNORECASE):
        raise ValueError("Only SELECT queries are allowed.")
    if not DUCK_PATH.exists():
        raise ValueError("No tables loaded yet. Upload a CSV first.")
    con = duckdb.connect(str(DUCK_PATH), read_only=True)
    try:
        cur = con.execute(s)
        cols = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchmany(max_rows)]
        return {"columns": cols, "rows": rows, "sql": s}
    finally:
        con.close()