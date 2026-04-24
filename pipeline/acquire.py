"""
Acquire Philadelphia L&I violation and property data via Carto SQL API.

Data source: phl.carto.com (Philadelphia open data Carto instance)
Tables:
  - li_violations: 1.45M housing code violations with owner names, addresses, lat/lng
  - opa_properties_public: 584K property records with parcel numbers, owner names, values

The Carto SQL API supports pagination via LIMIT/OFFSET. We download in chunks
of 50,000 rows to avoid timeouts, writing each chunk to CSV for later DuckDB ingestion.
"""

import json
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"

CARTO_SQL_URL = "https://phl.carto.com/api/v2/sql"
CHUNK_SIZE = 50000


def fetch_carto_csv(table_name, columns_sql, order_by, output_prefix):
    """Download a Carto table in paginated chunks to CSV files."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # Check for existing data
    existing = sorted(RAW_DIR.glob(f"{output_prefix}_*.csv"))
    if existing:
        total_rows = 0
        for f in existing:
            with open(f) as fh:
                total_rows += sum(1 for _ in fh) - 1  # minus header
        print(f"  {output_prefix}: {len(existing)} chunks already downloaded ({total_rows:,} rows)")
        print("  Delete data/raw/{output_prefix}_*.csv to re-download.")
        return existing

    # Get total count
    count_sql = f"SELECT COUNT(*) as cnt FROM {table_name}"
    count_url = f"{CARTO_SQL_URL}?q={urllib.parse.quote(count_sql)}"
    with urllib.request.urlopen(count_url) as resp:
        total = json.loads(resp.read())["rows"][0]["cnt"]
    print(f"  {output_prefix}: {total:,} total rows to download")

    offset = 0
    chunk_num = 0
    files = []
    retries_left = 3

    while offset < total:
        sql = f"SELECT {columns_sql} FROM {table_name} ORDER BY {order_by} LIMIT {CHUNK_SIZE} OFFSET {offset}"
        url = f"{CARTO_SQL_URL}?q={urllib.parse.quote(sql)}&format=csv"

        chunk_path = RAW_DIR / f"{output_prefix}_{chunk_num:03d}.csv"
        try:
            urllib.request.urlretrieve(url, str(chunk_path))

            # Validate — count rows
            with open(chunk_path) as f:
                rows = sum(1 for _ in f) - 1  # minus header
            if rows == 0 and offset < total:
                print(f"    WARNING: Empty chunk at offset {offset}, retrying...")
                if retries_left > 0:
                    retries_left -= 1
                    time.sleep(5)
                    continue
                else:
                    print("    Too many empty chunks, stopping.")
                    break

            size_kb = chunk_path.stat().st_size / 1024
            print(f"    Chunk {chunk_num}: {rows:,} rows ({size_kb:.0f} KB) [offset {offset:,}]")

            files.append(chunk_path)
            offset += CHUNK_SIZE
            chunk_num += 1
            retries_left = 3  # reset on success

            # Be nice to the API
            if offset < total:
                time.sleep(1)

        except Exception as e:
            print(f"    Error at offset {offset}: {e}", file=sys.stderr)
            if retries_left > 0:
                retries_left -= 1
                print(f"    Retrying in 10s ({retries_left} retries left)...")
                time.sleep(10)
            else:
                print("    Failed after retries, stopping.", file=sys.stderr)
                break

    total_downloaded = sum(1 for f in files for line in open(f)) - len(files)
    print(f"  Done: {len(files)} chunks, {total_downloaded:,} rows")
    return files


def acquire():
    """Download all Philadelphia data."""
    # Violations — the core dataset
    # Actual columns from phl.carto.com li_violations table
    violations_cols = ", ".join([
        "objectid", "casenumber", "aptype",
        "violationdate", "violationtype", "violationdescription",
        "status", "casestatus", "casepriority", "prioritydesc",
        "caseresolutiondate", "caseresolutioncode",
        "opa_account_num", "address", "unit", "zip",
        "ownername",
        "geocode_x", "geocode_y",
        "council_district", "censustract",
    ])

    print("=== Acquiring Philadelphia L&I Violations ===")
    violations = fetch_carto_csv(
        "li_violations", violations_cols, "objectid", "violations"
    )

    # Properties — for enrichment (market value, building details, mailing address)
    properties_cols = ", ".join([
        "objectid", "parcel_number", "location",
        "owner_1", "owner_2",
        "market_value", "category_code", "category_code_description",
        "building_code", "building_code_description",
        "total_livable_area", "number_of_bedrooms", "number_of_bathrooms",
        "year_built", "zip_code", "zoning",
        "ST_Y(the_geom) as lat", "ST_X(the_geom) as lng",
    ])

    print()
    print("=== Acquiring OPA Property Records ===")
    properties = fetch_carto_csv(
        "opa_properties_public", properties_cols, "objectid", "properties"
    )

    print()
    print(f"Data ready: {len(violations)} violation chunks, {len(properties)} property chunks")
    return violations, properties


if __name__ == "__main__":
    acquire()
