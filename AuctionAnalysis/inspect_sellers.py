#!/usr/bin/env python3
from __future__ import annotations
"""Inspect sellerName extraction results in Auctioneer scan SQLite databases.

This script opens an Auctioneer snapshot database, validates the auctionListings
schema, reports sellerName coverage, and prints a sample of rows where seller
information was extracted for diagnostic purposes.
"""

import argparse
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional

from AuctionAnalysis.script_helpers import SCAN_FIELD_POSITIONS


def inspect_database(path: Path, limit: Optional[int] = None) -> None:
    print(f"Inspecting database: {path}")
    print(f"sellerName field position in scan row mapping: {SCAN_FIELD_POSITIONS['sellerName']}")

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) AS total FROM auctionListings")
        total = cursor.fetchone()[0]
        print(f"Total auctionListings rows: {total}")

        cursor.execute(
            "SELECT COUNT(*) AS has_seller FROM auctionListings WHERE sellerName IS NOT NULL AND sellerName != ''"
        )
        seller_count = cursor.fetchone()[0]
        print(f"Rows with sellerName: {seller_count}")

        cursor.execute(
            "SELECT sellerName, COUNT(*) AS cnt FROM auctionListings GROUP BY sellerName ORDER BY cnt DESC LIMIT 20"
        )
        rows = cursor.fetchall()
        print("Top sellerName values:")
        for row in rows:
            print(f"  {row['sellerName']!r}: {row['cnt']}")

        print("\nSample rows with sellerName:")
        query = "SELECT itemName, sellerName, stackSize, buyoutPrice, minBid, curBid FROM auctionListings WHERE sellerName IS NOT NULL AND sellerName != '' LIMIT ?"
        cursor.execute(query, (limit or 20,))
        for row in cursor.fetchall():
            print(
                f"{row['itemName']!r} - sellerName={row['sellerName']!r} - qty={row['stackSize']} - buyout={row['buyoutPrice']} - minBid={row['minBid']} - curBid={row['curBid']}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect sellerName extraction in auctioneer database")
    parser.add_argument("db", type=Path, help="path to auctioneer SQLite database")
    parser.add_argument("--sample", type=int, default=20, help="number of sample rows to show")
    args = parser.parse_args()
    inspect_database(args.db, args.sample)
