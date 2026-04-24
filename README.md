# Philadelphia Landlord Violations

Search housing code violations by landlord name or LLC. See every property and violation across an owner's entire portfolio.

**1.45 million violations. 154,000+ owners. Search in seconds.**

## What This Does

Philadelphia publishes housing code violations and property ownership records as open data. This tool joins them, so you can:

1. **Search by owner name** — Type a landlord's name or LLC and see every property they manage
2. **See the full portfolio** — Total violations across all properties, not just your address
3. **Check severity** — Open violations, hazardous conditions, imminent danger flagged
4. **Review individual violations** — Date, type, status, priority for each property

## Why This Exists

Tenants can look up violations at their specific address via the city's website. What they can't do is see their landlord's track record across *all* their properties.

The data to surface serial violators is public. Nobody had joined it for tenants.

## Data

- **Source:** [City of Philadelphia L&I Violations](https://www.opendataphilly.org/datasets/violations/) + [OPA Property Assessments](https://www.opendataphilly.org/datasets/opa-property-assessments/)
- **Coverage:** 2007 through early 2020
- **Records:** 1,447,919 violations across 206,668 properties
- **Update frequency:** Static snapshot (pipeline can be re-run to refresh)

## How to Reproduce

Requires Python 3.10+ and [DuckDB](https://duckdb.org/).

```bash
pip install duckdb

# Run the full pipeline (downloads ~400MB from Philadelphia open data API)
python3 pipeline/run.py

# Serve locally
python3 -m http.server 8080 -d frontend/
```

The pipeline takes about 10 minutes (most of it is downloading from the Carto SQL API).

### Pipeline steps

1. **Acquire** — Downloads violations and property records via paginated Carto SQL API
2. **Clean** — Normalizes owner names, joins properties, computes per-owner aggregates
3. **Index** — Generates search chunks and 154K owner profile JSON files

## Technical Notes

- **No server required.** All search happens client-side. Data is pre-indexed into JSON chunks loaded on demand.
- **Initial page load** is ~3KB (stats.json only). First search loads one chunk (~200-600KB). Clicking a result loads one owner chunk (~500KB-2MB, shared across ~600 owners per chunk).
- **No API keys, no accounts, no tracking.** Static HTML + JSON served from GitHub Pages.
- **Owner name normalization** strips common LLC/Inc/Corp suffixes and DBA variants. Some related entities (e.g., "GRAY ROCK EQUITIES LLC" vs "GRAY ROCK MANAGEMENT LLC") appear separately because they are distinct legal entities in the source data.
- **Government entities** (City of Philadelphia, PHA, etc.) are flagged but included in the data for transparency.

## Disclaimer

This tool presents publicly available city code enforcement data in a more accessible format. It does not include state-level enforcement or conditions not reported to L&I. Absence of a record does not mean no violations exist. Not legal advice.
