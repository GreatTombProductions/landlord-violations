"""
Generate search index and owner profile JSON for the static frontend.

Input: Clean DuckDB database from clean.py
Output:
  frontend/data/
    search/{prefix}.json      — Search chunks by first letter of owner name (A-Z, 0, _)
    search/manifest.json      — Chunk metadata
    owners/{prefix}.json      — Owner profiles chunked by first 2 chars of hash
    owners/manifest.json      — Chunk hash → prefix mapping
    stats.json                — City-wide summary statistics
    meta.json                 — Data vintage, disclaimers

Owner profiles are consolidated into ~256 chunk files (by first 2 hex chars of hash)
rather than 154K individual files. This reduces filesystem overhead from 640MB to ~170MB
and keeps the git repo under GitHub Pages limits.
"""

import json
import hashlib
from pathlib import Path
from collections import defaultdict

import duckdb

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "violations.duckdb"
OUTPUT_DIR = PROJECT_ROOT / "frontend" / "data"
SEARCH_DIR = OUTPUT_DIR / "search"
OWNERS_DIR = OUTPUT_DIR / "owners"

# Max violations to include per property in the profile JSON
MAX_VIOLATIONS_PER_PROPERTY = 50


def owner_hash(normalized_name):
    """Deterministic short hash for owner profile lookup."""
    return hashlib.md5(normalized_name.encode()).hexdigest()[:12]


def build_index():
    """Generate all frontend data files."""
    SEARCH_DIR.mkdir(parents=True, exist_ok=True)
    OWNERS_DIR.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(DB_PATH), read_only=True)

    # -------------------------------------------------------
    # BULK EXPORT — 3 queries
    # -------------------------------------------------------
    print("Loading owner stats...")
    owner_rows = con.execute("""
        SELECT
            owner_normalized, owner_display, property_count, total_violations,
            open_violations, hazardous_count, imminent_danger_count,
            non_hazardous_count,
            first_violation::VARCHAR, last_violation::VARCHAR,
            is_government
        FROM owner_stats
        ORDER BY total_violations DESC
    """).fetchall()
    print(f"  {len(owner_rows):,} owners")

    print("Loading owner-property records...")
    prop_rows = con.execute("""
        SELECT
            owner_normalized, address, opa_account_num,
            violation_count, open_violations, hazardous, imminent_danger, non_hazardous,
            first_violation::VARCHAR, last_violation::VARCHAR,
            zip_code, market_value, property_type, livable_area,
            bedrooms, lat, lng
        FROM owner_properties
        ORDER BY owner_normalized, violation_count DESC
    """).fetchall()
    print(f"  {len(prop_rows):,} owner-property records")

    owner_props = defaultdict(list)
    for row in prop_rows:
        owner_props[row[0]].append(row)
    del prop_rows

    print("Loading all violations...")
    viol_rows = con.execute("""
        SELECT
            owner_normalized, address,
            violation_date::VARCHAR, violation_code,
            violation_description, violation_status,
            case_status, priority, resolution_date::VARCHAR
        FROM violations
        ORDER BY owner_normalized, address, violation_date DESC NULLS LAST
    """).fetchall()
    print(f"  {len(viol_rows):,} violations")

    owner_addr_viols = defaultdict(list)
    for row in viol_rows:
        key = (row[0], row[1])
        owner_addr_viols[key].append(row)
    del viol_rows

    # -------------------------------------------------------
    # 1. Search index (chunked by first letter)
    # -------------------------------------------------------
    print("\nBuilding search index...")

    chunks = defaultdict(list)
    owner_hash_map = {}

    for row in owner_rows:
        (norm, display, props, total_v, open_v, haz, imm, nh,
         first_d, last_d, is_govt) = row

        if not norm:
            continue

        h = owner_hash(norm)
        owner_hash_map[norm] = h

        first_char = norm[0].upper()
        if first_char.isalpha():
            key = first_char
        elif first_char.isdigit():
            key = "0"
        else:
            key = "_"

        entry = [
            (display or norm)[:80],
            h,
            props,
            total_v,
        ]

        flags = []
        if open_v:
            flags.append(f"o:{open_v}")
        if imm:
            flags.append(f"i:{imm}")
        if haz:
            flags.append(f"h:{haz}")
        if is_govt:
            flags.append("g")
        if flags:
            entry.append("|".join(flags))

        chunks[key].append(entry)

    manifest = {}
    total_size = 0

    for key in sorted(chunks.keys()):
        entries = chunks[key]
        chunk_path = SEARCH_DIR / f"{key}.json"
        with open(chunk_path, "w") as f:
            json.dump(entries, f, separators=(",", ":"))
        raw_size = chunk_path.stat().st_size
        manifest[key] = {"count": len(entries), "size": raw_size}
        total_size += raw_size

    with open(SEARCH_DIR / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"  {len(chunks)} chunks, {len(owner_hash_map):,} owners, {total_size / (1024*1024):.1f} MB")

    # -------------------------------------------------------
    # 2. Owner profiles — chunked by hash prefix
    # -------------------------------------------------------
    print("\nGenerating owner profiles (chunked by hash prefix)...")

    # Group profiles by first 2 chars of hash → one JSON file per prefix
    owner_chunks = defaultdict(dict)  # prefix -> {hash: profile}
    profiles_written = 0

    for row in owner_rows:
        (norm, display, prop_count, total_v, open_v, haz, imm, nh,
         first_d, last_d, is_govt) = row

        if not norm:
            continue

        h = owner_hash(norm)
        prefix = h[:2]

        # Build property list
        properties = []
        for prop in owner_props.get(norm, []):
            (_, addr, opa, v_cnt, v_open, v_haz, v_imm, v_nh,
             p_first, p_last, zipcode, mkt_val, prop_type,
             area, beds, lat, lng) = prop

            p = {"a": addr, "v": v_cnt}
            if v_open:
                p["o"] = v_open
            if v_imm:
                p["i"] = v_imm
            if v_haz:
                p["h"] = v_haz
            if p_first:
                p["fd"] = p_first[:10]
            if p_last:
                p["ld"] = p_last[:10]
            if zipcode:
                p["z"] = zipcode
            if mkt_val:
                p["mv"] = mkt_val
            if prop_type:
                p["pt"] = prop_type
            if area:
                p["sf"] = int(area)
            if beds:
                p["bd"] = beds
            if lat and lng:
                p["ll"] = [round(lat, 5), round(lng, 5)]

            viols = owner_addr_viols.get((norm, addr), [])
            vlist = []
            for viol in viols[:MAX_VIOLATIONS_PER_PROPERTY]:
                (_, _, vd, vc, vdesc, vstatus, cstatus, vpri, vres) = viol
                vr = {}
                if vd:
                    vr["d"] = vd[:10]
                if vc:
                    vr["c"] = vc
                if vdesc:
                    vr["t"] = vdesc[:80]
                if vpri and vpri != "NON HAZARDOUS":
                    vr["p"] = vpri
                if cstatus and cstatus != "CLOSED":
                    vr["s"] = cstatus
                if vres:
                    vr["r"] = vres[:10]
                vlist.append(vr)

            if vlist:
                p["vl"] = vlist

            properties.append(p)

        profile = {
            "n": display or norm,
            "pc": prop_count,
            "tv": total_v,
        }
        if open_v:
            profile["ov"] = open_v
        if haz:
            profile["hc"] = haz
        if imm:
            profile["ic"] = imm
        if first_d:
            profile["fd"] = first_d[:10]
        if last_d:
            profile["ld"] = last_d[:10]
        if is_govt:
            profile["g"] = True
        profile["props"] = properties

        owner_chunks[prefix][h] = profile
        profiles_written += 1

        if profiles_written % 25000 == 0:
            print(f"    {profiles_written:,} profiles assembled...")

    # Write chunk files
    total_owner_size = 0
    for prefix in sorted(owner_chunks.keys()):
        chunk_path = OWNERS_DIR / f"{prefix}.json"
        with open(chunk_path, "w") as f:
            json.dump(owner_chunks[prefix], f, separators=(",", ":"))
        total_owner_size += chunk_path.stat().st_size

    owner_chunk_count = len(owner_chunks)
    print(f"  {profiles_written:,} profiles in {owner_chunk_count} chunk files, {total_owner_size / (1024*1024):.1f} MB total")

    # -------------------------------------------------------
    # 3. Summary statistics
    # -------------------------------------------------------
    print("\nGenerating summary statistics...")

    total_violations = con.execute("SELECT COUNT(*) FROM violations").fetchone()[0]
    total_owners = con.execute("SELECT COUNT(*) FROM owner_stats").fetchone()[0]
    private_owners = con.execute("SELECT COUNT(*) FROM owner_stats WHERE NOT is_government").fetchone()[0]
    total_properties = con.execute("SELECT COUNT(DISTINCT address) FROM violations").fetchone()[0]

    top_violators = []
    for row in con.execute("""
        SELECT owner_normalized, owner_display, total_violations, property_count,
               open_violations, imminent_danger_count, hazardous_count
        FROM owner_stats
        WHERE NOT is_government
        ORDER BY total_violations DESC
        LIMIT 20
    """).fetchall():
        top_violators.append({
            "n": row[1], "v": row[2], "p": row[3],
            "o": row[4], "i": row[5], "h": row[6],
            "hash": owner_hash_map.get(row[0], ""),
        })

    violation_types = []
    for row in con.execute("""
        SELECT violation_description, COUNT(*) as cnt
        FROM violations GROUP BY violation_description
        ORDER BY cnt DESC LIMIT 20
    """).fetchall():
        violation_types.append({"t": row[0], "c": row[1]})

    priority_dist = {}
    for row in con.execute("""
        SELECT priority, COUNT(*) FROM violations
        WHERE priority IS NOT NULL
        GROUP BY priority ORDER BY COUNT(*) DESC
    """).fetchall():
        priority_dist[row[0]] = row[1]

    by_year = {}
    for row in con.execute("""
        SELECT EXTRACT(YEAR FROM violation_date)::INT as yr, COUNT(*)
        FROM violations WHERE violation_date IS NOT NULL
        GROUP BY yr ORDER BY yr
    """).fetchall():
        by_year[str(row[0])] = row[1]

    stats = {
        "total_violations": total_violations,
        "total_owners": total_owners,
        "private_owners": private_owners,
        "total_properties": total_properties,
        "top_violators": top_violators,
        "violation_types": violation_types,
        "priority": priority_dist,
        "by_year": by_year,
    }

    stats_path = OUTPUT_DIR / "stats.json"
    with open(stats_path, "w") as f:
        json.dump(stats, f, separators=(",", ":"))
    print(f"  Stats: {stats_path.stat().st_size / 1024:.0f} KB")

    # -------------------------------------------------------
    # 4. Metadata
    # -------------------------------------------------------
    date_range = con.execute("""
        SELECT MIN(violation_date)::VARCHAR, MAX(violation_date)::VARCHAR
        FROM violations
    """).fetchone()

    meta = {
        "source": "City of Philadelphia, Licenses & Inspections",
        "source_url": "https://www.opendataphilly.org/datasets/violations/",
        "description": "Housing code violations with owner names, cross-referenced with OPA property records",
        "date_range": {
            "start": date_range[0][:10] if date_range[0] else None,
            "end": date_range[1][:10] if date_range[1] else None,
        },
        "record_count": total_violations,
        "update_note": "Data sourced from phl.carto.com. Dataset covers 2007 through early 2020.",
        "disclaimer": (
            "This tool presents publicly available Philadelphia code enforcement data "
            "in a more accessible format. It does not include state-level enforcement "
            "or conditions not reported to L&I. Absence of a record does not mean no "
            "violations exist. Not legal advice."
        ),
    }

    meta_path = OUTPUT_DIR / "meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    # -------------------------------------------------------
    # Summary
    # -------------------------------------------------------
    print(f"\n=== OUTPUT SUMMARY ===")
    print(f"  Search chunks: {len(chunks)} files, {total_size / (1024*1024):.1f} MB")
    print(f"  Owner profiles: {profiles_written:,} in {owner_chunk_count} files, {total_owner_size / (1024*1024):.1f} MB")
    print(f"  Stats: {stats_path.stat().st_size / 1024:.0f} KB")
    print(f"  Initial page load: stats.json only")
    print(f"  First search: one chunk (~200-600KB)")
    print(f"  Click result: one owner chunk (~500KB-2MB, then client extracts by hash)")

    con.close()


if __name__ == "__main__":
    build_index()
