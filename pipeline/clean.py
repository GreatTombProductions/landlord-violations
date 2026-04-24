"""
Clean and normalize Philadelphia L&I violation and property data.

Input: Raw CSV chunks from Carto SQL API
Output: DuckDB database with:
  - violations: cleaned violation records
  - properties: cleaned property records (for enrichment)
  - owner_stats: per-owner aggregates (the core product)

Cleaning steps:
1. Combine CSV chunks into single tables
2. Normalize owner names (uppercase, strip LLC/Inc/Corp suffixes, collapse whitespace)
3. Join violations to properties via opa_account_num = parcel_number
4. Compute per-owner aggregates (violation count, property count, status breakdown)
5. Compute per-property summaries under each owner
"""

import duckdb
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
DB_PATH = PROJECT_ROOT / "data" / "violations.duckdb"


# Government/institutional owners to flag (not private landlords)
GOVT_PATTERNS = [
    "CITY OF PHIL",        # Covers "CITY OF PHILA", "CITY OF PHILADELPHIA"
    "PHILADELPHIA HOUSING AUTH",
    "PHILA HOUSING AUTH",
    "PHA ",                # Philadelphia Housing Authority abbreviation
    "REDEVELOPMENT AUTH",
    "PHILADELPHIA LAND BANK",
    "HOUSING AND URBAN DEV",
    "DEPT OF HOUSING",
    "DEPT OF PU",          # Department of Public
    "UNITED STATES OF AMERICA",
    "COMMONWEALTH OF PA",
    "STATE OF PENNSYLVANIA",
    "SCHOOL DISTRICT",
    "SOUTHEASTERN PA TRANS",
    "SEPTA",
    "US POSTAL",
    "TEMPLE UNIVERSITY",
    "UNIVERSITY OF PENN",
    "DREXEL UNIVERSITY",
]


def clean_data():
    """Process raw CSVs into clean DuckDB database."""
    if DB_PATH.exists():
        DB_PATH.unlink()

    con = duckdb.connect(str(DB_PATH))

    # -------------------------------------------------------
    # 1. Load violations
    # -------------------------------------------------------
    violations_pattern = str(RAW_DIR / "violations_*.csv")
    print("Loading violation CSVs...")
    con.execute(f"""
        CREATE TABLE violations_raw AS
        SELECT * FROM read_csv_auto('{violations_pattern}',
            header=true,
            union_by_name=true,
            ignore_errors=true,
            all_varchar=true
        )
    """)
    total_v = con.execute("SELECT COUNT(*) FROM violations_raw").fetchone()[0]
    print(f"  Loaded {total_v:,} raw violation records")

    # -------------------------------------------------------
    # 2. Load properties
    # -------------------------------------------------------
    properties_pattern = str(RAW_DIR / "properties_*.csv")
    print("Loading property CSVs...")
    con.execute(f"""
        CREATE TABLE properties_raw AS
        SELECT * FROM read_csv_auto('{properties_pattern}',
            header=true,
            union_by_name=true,
            ignore_errors=true,
            all_varchar=true
        )
    """)
    total_p = con.execute("SELECT COUNT(*) FROM properties_raw").fetchone()[0]
    print(f"  Loaded {total_p:,} raw property records")

    # -------------------------------------------------------
    # 3. Clean violations
    # -------------------------------------------------------
    print("\nCleaning violations...")
    con.execute("""
        CREATE TABLE violations AS
        SELECT
            TRY_CAST(objectid AS INTEGER) as violation_id,
            TRIM(casenumber) as case_number,
            TRIM(aptype) as violation_category,

            -- Dates
            TRY_CAST(violationdate AS TIMESTAMP) as violation_date,
            TRY_CAST(caseresolutiondate AS TIMESTAMP) as resolution_date,

            -- Violation detail
            TRIM(violationtype) as violation_code,
            TRIM(violationdescription) as violation_description,
            TRIM(status) as violation_status,
            TRIM(casestatus) as case_status,
            TRIM(prioritydesc) as priority,

            -- Location
            TRIM(address) as address,
            TRIM(unit) as unit,
            LEFT(TRIM(zip), 5) as zip_code,
            TRIM(opa_account_num) as opa_account_num,
            TRIM(council_district) as council_district,
            TRIM(censustract) as census_tract,

            -- Coordinates (Philadelphia State Plane → we'll use as-is for now)
            TRY_CAST(geocode_x AS DOUBLE) as geo_x,
            TRY_CAST(geocode_y AS DOUBLE) as geo_y,

            -- Owner name (raw)
            TRIM(ownername) as owner_raw,

            -- Normalized owner name for matching
            UPPER(TRIM(
                REGEXP_REPLACE(
                    REGEXP_REPLACE(
                        REGEXP_REPLACE(
                            REGEXP_REPLACE(
                                TRIM(ownername),
                                ',?\\s*(INC\\.?|LLC\\.?|LTD\\.?|CORP\\.?|CO\\.?|LP\\.?|L\\.?L\\.?C\\.?|L\\.?P\\.?)\\s*$', '', 'i'
                            ),
                            ',?\\s*(D/?B/?A|DBA|T/?A|AKA)\\s+.*$', '', 'i'
                        ),
                        '\\s+', ' '
                    ),
                    '^\\s+|\\s+$', ''
                )
            )) as owner_normalized

        FROM violations_raw
        WHERE ownername IS NOT NULL AND TRIM(ownername) != ''
    """)

    cleaned_v = con.execute("SELECT COUNT(*) FROM violations").fetchone()[0]
    print(f"  {cleaned_v:,} violations with owner names ({100*cleaned_v/total_v:.1f}%)")

    # -------------------------------------------------------
    # 4. Clean properties
    # -------------------------------------------------------
    print("Cleaning properties...")
    con.execute("""
        CREATE TABLE properties AS
        SELECT
            TRIM(parcel_number) as parcel_number,
            TRIM(location) as location,
            TRIM(owner_1) as owner_1,
            TRIM(owner_2) as owner_2,
            TRY_CAST(market_value AS INTEGER) as market_value,
            TRIM(category_code) as category_code,
            TRIM(category_code_description) as category_description,
            TRIM(building_code) as building_code,
            TRIM(building_code_description) as building_description,
            TRY_CAST(total_livable_area AS DOUBLE) as livable_area_sqft,
            TRY_CAST(number_of_bedrooms AS INTEGER) as bedrooms,
            TRY_CAST(number_of_bathrooms AS INTEGER) as bathrooms,
            TRY_CAST(year_built AS INTEGER) as year_built,
            TRIM(zip_code) as zip_code,
            TRIM(zoning) as zoning,
            TRY_CAST(lat AS DOUBLE) as lat,
            TRY_CAST(lng AS DOUBLE) as lng
        FROM properties_raw
        WHERE parcel_number IS NOT NULL AND TRIM(parcel_number) != ''
    """)

    cleaned_p = con.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    print(f"  {cleaned_p:,} properties")

    # Drop raw tables
    con.execute("DROP TABLE violations_raw")
    con.execute("DROP TABLE properties_raw")

    # -------------------------------------------------------
    # 5. Build owner aggregates
    # -------------------------------------------------------
    print("\nBuilding owner aggregates...")

    # Per-owner, per-property stats
    con.execute("""
        CREATE TABLE owner_properties AS
        SELECT
            v.owner_normalized,
            MIN(v.owner_raw) as owner_display,
            v.address,
            v.opa_account_num,
            COUNT(*) as violation_count,
            COUNT(*) FILTER (WHERE v.case_status != 'CLOSED' OR v.case_status IS NULL) as open_violations,
            COUNT(*) FILTER (WHERE v.priority = 'IMMINENTLY DANGEROUS') as imminent_danger,
            COUNT(*) FILTER (WHERE v.priority = 'HAZARDOUS') as hazardous,
            COUNT(*) FILTER (WHERE v.priority = 'NON HAZARDOUS') as non_hazardous,
            MIN(v.violation_date) as first_violation,
            MAX(v.violation_date) as last_violation,
            FIRST(v.zip_code) as zip_code,
            -- Take a representative coordinate pair
            FIRST(v.geo_x) as geo_x,
            FIRST(v.geo_y) as geo_y,
            -- Property enrichment via join
            FIRST(p.market_value) as market_value,
            FIRST(p.category_description) as property_type,
            FIRST(p.livable_area_sqft) as livable_area,
            FIRST(p.bedrooms) as bedrooms,
            FIRST(p.lat) as lat,
            FIRST(p.lng) as lng
        FROM violations v
        LEFT JOIN properties p ON v.opa_account_num = p.parcel_number
        WHERE v.owner_normalized IS NOT NULL AND v.owner_normalized != ''
        GROUP BY v.owner_normalized, v.address, v.opa_account_num
    """)

    prop_count = con.execute("SELECT COUNT(*) FROM owner_properties").fetchone()[0]
    print(f"  {prop_count:,} owner-property combinations")

    # Per-owner summary
    con.execute("""
        CREATE TABLE owner_stats AS
        SELECT
            owner_normalized,
            MIN(owner_display) as owner_display,
            COUNT(DISTINCT address) as property_count,
            SUM(violation_count) as total_violations,
            SUM(open_violations) as open_violations,
            SUM(imminent_danger) as imminent_danger_count,
            SUM(hazardous) as hazardous_count,
            SUM(non_hazardous) as non_hazardous_count,
            MIN(first_violation) as first_violation,
            MAX(last_violation) as last_violation,
            -- Flag if this looks like a government entity
            CASE WHEN """ + " OR ".join(
                f"owner_normalized LIKE '%{p}%'" for p in GOVT_PATTERNS
            ) + """ THEN true ELSE false END as is_government
        FROM owner_properties
        GROUP BY owner_normalized
    """)

    owner_count = con.execute("SELECT COUNT(*) FROM owner_stats").fetchone()[0]
    private_count = con.execute("SELECT COUNT(*) FROM owner_stats WHERE NOT is_government").fetchone()[0]
    print(f"  {owner_count:,} unique owners ({private_count:,} private, {owner_count - private_count:,} government/institutional)")

    # -------------------------------------------------------
    # 6. Violation type distribution
    # -------------------------------------------------------
    print("\nTop violation categories:")
    for row in con.execute("""
        SELECT violation_description, COUNT(*) as cnt
        FROM violations
        GROUP BY violation_description
        ORDER BY cnt DESC
        LIMIT 15
    """).fetchall():
        print(f"  {row[0][:50]:50s} {row[1]:>10,}")

    # -------------------------------------------------------
    # 7. Top private violators
    # -------------------------------------------------------
    print("\nTop 10 private violators:")
    for row in con.execute("""
        SELECT owner_display, total_violations, property_count,
               open_violations, hazardous_count, imminent_danger_count
        FROM owner_stats
        WHERE NOT is_government
        ORDER BY total_violations DESC
        LIMIT 10
    """).fetchall():
        print(f"  {row[0][:35]:35s} {row[1]:>6,} violations across {row[2]:>4,} properties"
              f" ({row[3]:,} open, {row[4]:,} hazardous, {row[5]:,} imminent)")

    # -------------------------------------------------------
    # Summary
    # -------------------------------------------------------
    stats = con.execute("""
        SELECT
            (SELECT COUNT(*) FROM violations) as total_violations,
            (SELECT COUNT(*) FROM owner_stats) as total_owners,
            (SELECT COUNT(*) FROM owner_stats WHERE NOT is_government) as private_owners,
            (SELECT SUM(total_violations) FROM owner_stats WHERE NOT is_government) as private_violations,
            (SELECT COUNT(*) FROM owner_properties) as property_combos,
            (SELECT MIN(violation_date) FROM violations)::VARCHAR as earliest,
            (SELECT MAX(violation_date) FROM violations)::VARCHAR as latest
    """).fetchone()

    db_size = DB_PATH.stat().st_size / (1024 * 1024)

    print(f"\n=== CLEAN DATABASE ===")
    print(f"  Total violations: {stats[0]:,}")
    print(f"  Total owners: {stats[1]:,} ({stats[2]:,} private)")
    print(f"  Private owner violations: {stats[3]:,}")
    print(f"  Owner-property pairs: {stats[4]:,}")
    print(f"  Date range: {stats[5]} to {stats[6]}")
    print(f"  Database: {DB_PATH} ({db_size:.1f} MB)")

    con.close()
    return DB_PATH


if __name__ == "__main__":
    clean_data()
