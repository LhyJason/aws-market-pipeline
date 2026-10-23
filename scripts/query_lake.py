"""Run the saved Athena queries against processed/ Parquet with DuckDB, for $0.

Glue and Athena are not in LocalStack's free edition, so this is how the
queries run locally:

    python scripts/query_lake.py --localstack    # pull processed/ from LocalStack, run all
    python scripts/query_lake.py --dir .build/lake/processed daily_vwap

The SQL files are the ones Terraform saves as Athena named queries
(analytics/athena/queries). They stick to syntax that Trino (Athena engine 3)
and DuckDB share; tests/unit/test_athena_queries.py runs them here and parses
them as Trino.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
QUERY_DIR = ROOT / "analytics" / "athena" / "queries"
SCHEMA_FILE = ROOT / "analytics" / "athena" / "schema" / "bars_1m.json"
DEFAULT_ENDPOINT = "http://localhost:4566"
DEFAULT_BUCKET = "market-pipeline-local-lake-000000000000"
DEFAULT_DEST = ROOT / ".build" / "lake"

# Glue (Hive) type names in the schema file -> DuckDB types.
DUCKDB_TYPES = {
    "string": "VARCHAR",
    "date": "DATE",
    "int": "INTEGER",
    "bigint": "BIGINT",
    "double": "DOUBLE",
    "timestamp": "TIMESTAMP",
}


def query_names() -> list[str]:
    return sorted(p.stem for p in QUERY_DIR.glob("*.sql"))


def load_query(name: str) -> str:
    return (QUERY_DIR / f"{name}.sql").read_text(encoding="utf-8")


def connect(processed_root: Path) -> duckdb.DuckDBPyConnection:
    """A DuckDB connection with a ``bars_1m`` view laid out like the Glue table.

    ``processed_root`` is the local copy of ``s3://<lake>/processed/``.
    """
    files = processed_root / "bars_1m"
    if not any(files.rglob("*.parquet")):
        raise SystemExit(f"no Parquet files under {files}")

    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    hive_types = ", ".join(
        f"'{key['name']}': '{DUCKDB_TYPES[key['type']]}'" for key in schema["partition_keys"]
    )
    pattern = (files / "**" / "*.parquet").as_posix().replace("'", "''")

    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")  # Athena's current_date is UTC
    con.execute(
        "CREATE VIEW bars_1m AS SELECT * FROM read_parquet("
        f"'{pattern}', hive_partitioning = true, hive_types = {{{hive_types}}})"
    )
    return con


def pull_from_localstack(bucket: str, endpoint: str, dest: Path) -> int:
    """Replace ``dest`` with a copy of the bucket's processed/ prefix; return the file count."""
    import boto3  # only needed for this path; tests use local files

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        # LocalStack's documented dummy credentials.
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    if dest.exists():
        shutil.rmtree(dest)
    count = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="processed/"):
        for obj in page.get("Contents", []):
            target = dest / obj["Key"]
            target.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(target))
            count += 1
    return count


def format_rows(relation: duckdb.DuckDBPyRelation) -> str:
    """Plain tab-separated output; DuckDB's box drawing breaks on some Windows consoles."""
    lines = ["\t".join(relation.columns)]
    lines += ["\t".join("" if v is None else str(v) for v in row) for row in relation.fetchall()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("queries", nargs="*", help=f"default: all of {', '.join(query_names())}")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--localstack", action="store_true", help="pull processed/ first")
    source.add_argument("--dir", type=Path, help="local copy of processed/")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    args = parser.parse_args(argv)

    if args.localstack:
        count = pull_from_localstack(args.bucket, args.endpoint, DEFAULT_DEST)
        print(f"pulled {count} objects from s3://{args.bucket}/processed/")
    processed_root = args.dir or DEFAULT_DEST / "processed"

    unknown = set(args.queries) - set(query_names())
    if unknown:
        parser.error(f"unknown queries: {', '.join(sorted(unknown))}")

    con = connect(processed_root)
    for name in args.queries or query_names():
        print(f"\n-- {name}")
        print(format_rows(con.sql(load_query(name))))


if __name__ == "__main__":
    main()
