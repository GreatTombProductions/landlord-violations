"""
Full pipeline orchestrator for Landlord Violations Tracker (Philadelphia).

Usage:
    python3 pipeline/run.py              # Run full pipeline
    python3 pipeline/run.py --clean      # Skip acquisition, re-clean and re-index
    python3 pipeline/run.py --index      # Re-index only (using existing DuckDB)
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "pipeline"))

from acquire import acquire
from clean import clean_data
from index import build_index


def main():
    args = set(sys.argv[1:])

    if "--index" in args:
        print("=" * 60)
        print("INDEXING ONLY (using existing DuckDB)")
        print("=" * 60)
        build_index()
        return

    if "--clean" not in args:
        print("=" * 60)
        print("STEP 1: DATA ACQUISITION (Carto SQL API)")
        print("=" * 60)
        acquire()
        print()

    print("=" * 60)
    print("STEP 2: CLEANING & NORMALIZATION")
    print("=" * 60)
    clean_data()
    print()

    print("=" * 60)
    print("STEP 3: INDEX GENERATION")
    print("=" * 60)
    build_index()
    print()

    print("=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print("To serve locally: python3 -m http.server 8080 -d frontend/")


if __name__ == "__main__":
    main()
