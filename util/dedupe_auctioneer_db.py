#!/usr/bin/env python3
"""Utilities for deduplicating Auctioneer snapshot SQLite databases.

This module provides functions to detect repeated rows in the auctionListings
table, back up the original SQLite file, delete duplicate rows, and verify
compacted results.
"""

import argparse
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

Row = Tuple[int, ...]
DataRow = Tuple[Any, ...]
SELECT_SQL = "SELECT rowid, * FROM auctionListings ORDER BY rowid"


def load_rows(path: Path) -> List[Row]:
    """Load auctionListings rows from the SQLite database.

    Args:
        path: Path to the SQLite database file.

    Returns:
        A list of rows from auctionListings, each prefixed with its rowid.
    """
    with sqlite3.connect(path) as conn:
        return conn.execute(SELECT_SQL).fetchall()


def backup_database(source: Path) -> Path:
    """Create a backup copy of the SQLite database file.

    If a .bak file already exists, the backup name is incremented.

    Args:
        source: The original database file path.

    Returns:
        The path to the backup file created.
    """
    backup = source.with_name(source.name + ".bak")
    count = 1
    while backup.exists():
        backup = source.with_name(f"{source.name}.bak{count}")
        count += 1
    shutil.copy2(source, backup)
    return backup


def delete_rows(path: Path, rowids: Iterable[int]) -> int:
    """Delete rows from auctionListings by rowid.

    Args:
        path: Path to the SQLite database file.
        rowids: Iterable of rowid values to delete.

    Returns:
        Number of rows deleted.
    """
    rowids = tuple(rowids)
    if not rowids:
        return 0

    total_deleted = 0
    max_vars = 900
    with sqlite3.connect(path) as conn:
        for start in range(0, len(rowids), max_vars):
            chunk = rowids[start : start + max_vars]
            placeholders = ",".join("?" for _ in chunk)
            cursor = conn.execute(
                f"DELETE FROM auctionListings WHERE rowid IN ({placeholders})",
                chunk,
            )
            total_deleted += cursor.rowcount
        conn.commit()
    return total_deleted


def vacuum_database(path: Path) -> None:
    """Run SQLite VACUUM on the database to reclaim file space.

    Args:
        path: Path to the SQLite database file.
    """
    with sqlite3.connect(path) as conn:
        conn.execute("VACUUM")


def find_duplicate_rowids(rows: List[Row]) -> List[int]:
    """Identify duplicate rows based on auctionListings content.

    Args:
        rows: A list of rows loaded from auctionListings, including rowid.

    Returns:
        A list of rowids for rows whose content duplicates an earlier row.
    """
    seen: Dict[DataRow, int] = {}
    duplicates: List[int] = []
    for rowid, *values in rows:
        key = tuple(values)
        if key in seen:
            duplicates.append(rowid)
        else:
            seen[key] = rowid
    return duplicates


def repetition_factor(values: List[DataRow]) -> int:
    """Compute the repetition factor of a list of rows.

    The factor is the number of repeated segments in the sequence when it has
    an exact periodic structure.

    Args:
        values: Rows without rowid values.

    Returns:
        The repetition factor, or 1 if no exact repetition pattern exists.
    """
    n = len(values)
    if n <= 1:
        return 1
    pi = [0] * n
    for i in range(1, n):
        j = pi[i - 1]
        while j and values[i] != values[j]:
            j = pi[j - 1]
        if values[i] == values[j]:
            j += 1
        pi[i] = j
    period = n - pi[-1]
    return n // period if pi[-1] and n % period == 0 else 1


def exact_repeat_pattern(values: List[DataRow], factor: int) -> bool:
    """Check that values follow an exact repeated-factor pattern.

    Args:
        values: Rows without rowid values.
        factor: Exact repetition factor.

    Returns:
        True when every row i equals row(i + n/factor) for the repeated blocks.
    """
    n = len(values)
    if factor <= 1 or n % factor != 0:
        return False
    step = n // factor
    for i in range(step):
        expected = values[i]
        for j in range(1, factor):
            if values[i + j * step] != expected:
                return False
    return True


def compaction_rowids(rows: List[Row], factor: int) -> List[int]:
    """Return rowids to delete for exact repeated-sequence compaction.

    Args:
        rows: Full rows including rowid.
        factor: Exact repetition factor.

    Returns:
        Rowids from repeated segments beyond the first block.
    """
    if factor <= 1:
        return []
    step = len(rows) // factor
    return [rowid for rowid, *_ in rows[step:]]


def verify_compacted_rows(backup_path: Path, current_path: Path, factor: int) -> Tuple[bool, str]:
    """Verify that compacted current rows match the backup's first segment.

    Args:
        backup_path: Path to the backup database.
        current_path: Path to the modified database.
        factor: Expected repetition factor from the original rows.

    Returns:
        A tuple of (verified, reason). If verification succeeds, reason is empty.
    """
    if factor <= 1:
        return False, "factor <= 1"

    backup_rows = [row[1:] for row in load_rows(backup_path)]
    current_rows = [row[1:] for row in load_rows(current_path)]
    expected_count = len(backup_rows) // factor
    if len(current_rows) != expected_count:
        return (
            False,
            f"expected {expected_count} rows after compaction but found {len(current_rows)}",
        )

    for index, (current, expected) in enumerate(zip(current_rows, backup_rows[:expected_count])):
        if current != expected:
            return (
                False,
                (
                    f"row mismatch at index {index}:\n"
                    f"  current={current}\n"
                    f"  expected={expected}"
                ),
            )

    return True, ""


def max_run(rows: List[DataRow]) -> int:
    """Return the length of the longest run of identical consecutive rows.

    Args:
        rows: A list of data rows to inspect.

    Returns:
        Maximum number of repeated rows in a row.
    """
    m = k = 1
    for a, b in zip(rows, rows[1:]):
        k = k + 1 if a == b else 1
        if k > m:
            m = k
    return m


def print_summary(rows: List[Row]) -> None:
    """Print a basic summary of duplicate characteristics.

    Args:
        rows: A list of rows loaded from auctionListings, including rowid.
    """
    values = [tuple(row[1:]) for row in rows]
    total = len(values)
    distinct = len(set(values))
    adjacent_pairs = sum(1 for i in range(0, total - 1, 2) if values[i] == values[i + 1])
    print(
        f"rows={total} distinct={distinct} max_run={max_run(values)} "
        f"adjacent-pair={adjacent_pairs / (total // 2 or 1):.2%}"
    )
    if distinct / total < 0.75:
        print("SUSPECTED DUPLICATES DETECTED")
    factor = repetition_factor(values)
    if factor > 1:
        print(f"repeat-factor pattern detected: {factor}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Detect and optionally remove duplicate auctionListings rows"
    )
    parser.add_argument("db", help="Path to the SQLite database")
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Delete duplicate auctionListings rows from the database",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Compact the database after deletion with VACUUM",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Missing DB: {db_path}")

    rows = load_rows(db_path)
    if not rows:
        print("No auctionListings rows found.")
        raise SystemExit(0)

    if args.vacuum and not args.remove:
        raise SystemExit("--vacuum requires --remove")

    values = [row[1:] for row in rows]
    factor = repetition_factor(values)
    print_summary(rows)

    if factor > 1:
        delete_rowids = compaction_rowids(rows, factor)
        print(
            f"Exact repetition factor detected: {factor}; "
            f"{len(delete_rowids)} rows selected for compaction"
        )
    else:
        delete_rowids = find_duplicate_rowids(rows)
        print(f"duplicate rows={len(delete_rowids)}")

    if not args.remove:
        print("No changes made. Run with --remove to delete duplicates.")
        raise SystemExit(0)

    if not delete_rowids:
        print("No duplicate rows to remove.")
        raise SystemExit(0)

    backup_path = backup_database(db_path)
    print(f"Backup created at {backup_path}")

    removed = delete_rows(db_path, delete_rowids)
    print(f"Removed {removed} duplicate auctionListings row(s).")

    if args.vacuum and factor > 1 and exact_repeat_pattern(values, factor):
        vacuum_database(db_path)
        print("Database VACUUM completed; file size may now shrink.")
    elif args.vacuum:
        print(
            "Skipped VACUUM because exact repetition pattern verification failed. "
            "Backup retained at {backup_path}."
        )
    else:
        print(
            "Note: SQLite does not reclaim file space automatically after delete. "
            "Run with --vacuum to compact the file if verification passes."
        )

    if factor > 1 and exact_repeat_pattern(values, factor):
        verified, reason = verify_compacted_rows(backup_path, db_path, factor)
        if verified:
            print(f"Verified compacted rows against backup; backup retained at {backup_path}")
        else:
            print("Verification failed:")
            print(reason)
            shutil.copy2(backup_path, db_path)
            raise SystemExit(f"Verification failed; restored database from backup {backup_path}")
    else:
        print(
            "Skipped verification because no exact repeated pattern was detected; "
            f"backup retained at {backup_path}"
        )
