# AuctionAnalysis

This directory contains tools for importing, analyzing, and diffing Auctioneer auction house data from World of Warcraft.

## Purpose

`AuctionAnalysis` includes scripts to:

- Convert Auctioneer SavedVariables / rope data into SQLite (`auctioneer_rope_to_sqlite.py`)
- Analyze Auctioneer snapshot databases and sales inference outputs (`analyze_auctioneer_db.py`, `analyze_sales_db.py`)
- Compare sequential auction snapshots and infer sales/expirations/reposts (`AuctionScanDiff.py`)
- Extract item market statistics from Auctioneer and Informant data (`extract_item_market_data.py`)
- Inspect auction seller extraction data (`inspect_sellers.py`)
- Trace a given item through scan snapshots and inferred sales results (`trace_item_sales.py`)
- Provide shared helpers for SQLite, Lua parsing, and CLI utilities (`script_helpers.py`)

## Files

- `analyze_auctioneer_db.py`
  - Summarizes SQLite database contents, including table row counts, distinct values, duplicates, and JSON/rope analysis.

- `analyze_sales_db.py`
  - Reads a `sales.db` produced by `AuctionScanDiff.py` and prints sold/repost/expired item summaries and daily sales metrics.

- `auctioneer_rope_to_sqlite.py`
  - Converts Auctioneer rope or SavedVariables Lua data into an SQLite database, extracting raw ropes and decoded JSON.

- `AuctionScanDiff.py`
  - Diffs two or more sequential Auctioneer snapshot databases to infer which listings disappeared, and whether they were likely sold, expired, or reposted.

- `extract_item_market_data.py`
  - Combines item name lookup with Auctioneer market-price statistics, using Informant item names and Auctioneer histogram data.

- `inspect_sellers.py`
  - Inspects `auctionListings` data for seller name extraction coverage and prints sample rows containing seller information.

- `script_helpers.py`
  - Contains reusable helper code for SQLite schema introspection, Lua parsing, CLI argument parsing, debug logging, and serialization.

## MockAuctionHouseTesting

This subdirectory contains a synthetic data generation and validation framework for testing AuctionScanDiff and related logic.

- `generate.py`
  - Generates synthetic auction snapshots and ground-truth event logs.

- `validate.py`
  - Validates `AuctionScanDiff.py` predictions against synthetic ground truth and computes accuracy metrics.

- `debugging.py`
  - Runs a diagnostic pipeline across generated mock data and validation outputs.

- `test_mock_framework.py`
  - Contains lightweight tests for mock data generation and validation components.

- `data/`
  - Storage for generated synthetic snapshot databases and ground-truth JSON.

## Usage

Most scripts are intended to be run directly with Python 3. Example:

```bash
python AuctionAnalysis/auctioneer_rope_to_sqlite.py --input rope.lua --output auctioneer.db
python AuctionAnalysis/AuctionScanDiff.py --scan-dir scans/ --csv diff.csv
python AuctionAnalysis/trace_item_sales.py --item-name "Northern Spices" --scan-dir ./ --sales-db sales.db
```

## Notes

- Scripts in `AuctionAnalysis` assume Python 3.9+ type hints and standard library dependencies.
- `extract_item_market_data.py` and `auctioneer_rope_to_sqlite.py` may require the `slpp` package for Lua parsing.
- `AuctionScanDiff.py` writes a persistent `sales.db` output database by default, which downstream scripts can consume.
