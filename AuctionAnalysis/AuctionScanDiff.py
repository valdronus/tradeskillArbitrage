#!/usr/bin/env python3
"""Diff sequential Auctioneer SQLite snapshots and classify disappeared listings.

The input databases are produced by auctioneer_rope_to_sqlite.py.  A listing
has no stable Auctioneer id, so rows are matched by their observed attributes
while ignoring the fields that naturally change between scans: ``id``,
``ropeId``, ``seenTime``, and ``timeLeft``.

For a row missing from a later snapshot:

* it is ``likely_sold`` when the second scan happened before the first scan's
  time-left bucket could have expired;
* it is ``likely_expired`` when the second scan happened after the bucket's
  maximum duration;
* otherwise it is ``missing`` and receives a tunable sold-likelihood score.

Auctioneer's non-Classic time-left codes are 1: 0-0.5h, 2: 0.5-2h,
3: 2-12h, and 4: 12-48h.  Use ``--bucket-hours`` to override these bounds
when the saved data comes from a different client or has been normalized.

Example:
  python AuctionScanDiff.py --scan-dir scans/ --csv diff.csv

The script also writes a persistent sales database by default to `sales.db`.
The output schema is:

- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `before_snapshot` TEXT
- `after_snapshot` TEXT
- `first_seen_snapshot` TEXT
- `first_disappeared_snapshot` TEXT
- `status` TEXT
- `sold_likelihood` REAL
- `sold_likelihood_percent` REAL
- `elapsed_hours` REAL
- `bucket_min_hours` REAL
- `bucket_max_hours` REAL
- `peer_count` INTEGER
- `cheaper_peer_count` INTEGER
- `repost_average_unit_price` REAL
- `reason` TEXT
- `listing_json` TEXT

Other scripts can use `sales.db` as a clean, replayable result store
for inferred sold/expired/repost outcomes and metadata.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import math
import statistics
import time
from pathlib import Path
import sqlite3
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from script_helpers import (
    debug_log,
    format_money,
    get_table_columns as helper_get_table_columns,
    make_arg_parser,
    row_to_mapping,
    write_csv as helper_write_csv,
    write_json as helper_write_json,
)

try:
    from tqdm import tqdm
except ImportError:

    def tqdm(iterable, **kwargs):
        return iterable


debug = 0

# TODO: Add a self-verification regression test for expired-vs-sold estimation.
# When we have a scan series such as scan1.db, scan2.db, scan3.db, etc. we
# should be able to compare scan1 -> scan3 and ignore the intermediate scan2 to
# generate guessed outcomes, then verify those guesses against the real data in
# scan2.db. The eventual goal is to build a test harness that evaluates how well
# timing, price, and continuity weights predict actual outcomes, and to use that
# verification data to train a simple regression model or neural network that
# selects the best weights for estimating sold versus expired likelihood.
# This should help validate the core heuristics and allow future improvements
# by comparing coarse skipped-scan guesses against fine-grained intermediate
# scan data.





TABLE_NAME = "auctionListings"
DEFAULT_BUCKETS: Dict[int, Tuple[float, float]] = {
    1: (0.0, 0.5),
    2: (0.5, 2.0),
    3: (2.0, 12.0),
    4: (12.0, 48.0),
}

# These fields are scan-specific or are not stable identifiers.
IGNORED_MATCH_FIELDS = {"id", "ropeId", "seenTime", "timeLeft"}
IDENTITY_FIELDS = (
    "server",
    "faction",
    "itemId",
    "itemSuffix",
    "itemFactor",
    "itemEnchant",
    "itemSeed",
    "itemName",
    "link",
    "stackSize",
)
PRICE_FIELDS = ("buyoutPrice", "minBid", "curBid", "price")


@dataclass(frozen=True)
class Listing:
    values: Mapping[str, Any]

    def get(self, name: str) -> Any:
        return self.values.get(name)

    @property
    def seen_time(self) -> Optional[int]:
        value = self.get("seenTime")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def time_left(self) -> Optional[int]:
        value = self.get("timeLeft")
        return int(value) if isinstance(value, (int, float)) else None

    def match_key(self) -> Tuple[Any, ...]:
        return tuple(
            normalize_value(self.get(field))
            for field in sorted(self.values)
            if field not in IGNORED_MATCH_FIELDS
        )

    def identity_key(self) -> Tuple[Any, ...]:
        return tuple(normalize_value(self.get(field)) for field in IDENTITY_FIELDS)

    def price(self) -> Optional[float]:
        for field in ("buyoutPrice", "minBid", "curBid", "price"):
            value = self.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return float(value)
        return None

    def unit_price(self) -> Optional[float]:
        price = self.price()
        quantity = self.get("stackSize")
        if price is None:
            return None
        if isinstance(quantity, (int, float)) and quantity > 0:
            return price / quantity
        return price

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.values)


@dataclass
class DiffResult:
    listing: Listing
    status: str
    sold_likelihood: Optional[float]
    elapsed_hours: Optional[float]
    bucket_min_hours: Optional[float]
    bucket_max_hours: Optional[float]
    reason: str
    before_path: Optional[Path] = None
    after_path: Optional[Path] = None
    peer_count: int = 0
    cheaper_peer_count: int = 0
    repost_average_unit_price: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        result = self.listing.as_dict()
        result.update(
            {
                "status": self.status,
                "sold_likelihood_percent": (
                    round(self.sold_likelihood * 100, 1)
                    if self.sold_likelihood is not None
                    else None
                ),
                "elapsed_hours": (
                    round(self.elapsed_hours, 3) if self.elapsed_hours is not None else None
                ),
                "bucket_min_hours": self.bucket_min_hours,
                "bucket_max_hours": self.bucket_max_hours,
                "peer_count": self.peer_count,
                "cheaper_peer_count": self.cheaper_peer_count,
                "repost_average_unit_price": self.repost_average_unit_price,
                "before_snapshot": str(self.before_path) if self.before_path is not None else None,
                "after_snapshot": str(self.after_path) if self.after_path is not None else None,
                "reason": self.reason,
            }
        )
        return result


def normalize_value(value: Any) -> Any:
    """Normalize values for hashing and comparison.

    Args:
        value: The raw value loaded from SQLite.

    Returns:
        A hashable representation of the value, with NaN converted to None
        and booleans converted to integers.
    """
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, bool):
        return int(value)
    return value


def load_listings(path: Path, limit: Optional[int] = None) -> List[Listing]:
    """Load auction listing rows from a snapshot and return Listing objects.

    Args:
        path: Path to the SQLite snapshot file.
        limit: Optional limit on number of rows to load.

    Returns:
        A list of Listing objects representing rows from the auctionListings table.
    """
    print(f"Loading SQLite snapshot: {path}")
    debug_log(1, debug, f"Loading SQLite snapshot: {path}")
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        query = f"SELECT * FROM {TABLE_NAME}"
        params: Tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        try:
            rows = connection.execute(query, params).fetchall()
        except sqlite3.OperationalError as error:
            raise ValueError(f"{path} does not contain the expected {TABLE_NAME} table") from error
    if limit is not None:
        print(f"Limiting snapshot load to {limit} rows")
    if rows:
        print(f"Converting {len(rows)} rows into Listing objects")
        listings = [
            Listing(row_to_mapping(row))
            for row in tqdm(
                rows, desc=f"Loading rows from {path.name}", total=len(rows), unit="row"
            )
        ]
    else:
        listings = []
    debug_log(1, debug, f"Loaded {len(listings)} listing row(s) from {path}")
    return listings


def snapshot_time(listings: Sequence[Listing]) -> Optional[int]:
    times = [listing.seen_time for listing in listings if listing.seen_time is not None]
    latest = max(times) if times else None
    debug_log(2, debug, f"Snapshot time: {latest!r} from {len(times)} seenTime value(s)")
    return latest


@dataclass(frozen=True)
class SnapshotInfo:
    path: Path
    listings: List[Listing]
    snapshot_time: Optional[int]
    file_timestamp: float


def find_db_paths(directory: Path, exclude: Optional[Path] = None) -> List[Path]:
    """Discover SQLite snapshot files in a directory.

    Args:
        directory: Directory to scan for .db files.
        exclude: Optional path to exclude from discovery (e.g. sales.db).

    Returns:
        A list of .db paths sorted lexicographically.

    Raises:
        ValueError: If no .db files are found after applying the exclude filter.
    """
    db_paths = sorted(directory.glob("*.db"))
    exclude_resolved = (
        exclude.resolve()
        if exclude is not None and exclude.exists()
        else (exclude.absolute() if exclude is not None else None)
    )
    if exclude is not None:
        db_paths = [
            path
            for path in db_paths
            if path.resolve() != exclude_resolved and path.name != exclude.name
        ]
    db_paths = [path for path in db_paths if not path.name.endswith("sales.db")]
    if not db_paths:
        raise ValueError(f"No .db files found in {directory}")
    return db_paths


def get_table_columns(path: Path) -> Tuple[str, ...]:
    with sqlite3.connect(path) as connection:
        columns = helper_get_table_columns(connection, TABLE_NAME)
    if not columns:
        raise ValueError(f"{path} does not contain the expected {TABLE_NAME} table")
    return tuple(column.name for column in columns)


def validate_snapshot_schema(paths: Sequence[Path]) -> None:
    """Validate that all auction snapshot files share the same auctionListings schema.

    Args:
        paths: Paths to SQLite snapshot files.

    Raises:
        ValueError: If any snapshot's auctionListings table differs from the first.
    """
    first_columns = get_table_columns(paths[0])
    for path in paths[1:]:
        current_columns = get_table_columns(path)
        if current_columns != first_columns:
            raise ValueError(
                f"Inconsistent auctionListings schema: {paths[0]} has {first_columns}, "
                f"but {path} has {current_columns}"
            )


def load_snapshot_infos(paths: Sequence[Path], limit: Optional[int]) -> List[SnapshotInfo]:
    """Load snapshot metadata and listing content for each input path.

    Args:
        paths: Paths to snapshot files.
        limit: Optional maximum number of rows to load per snapshot.

    Returns:
        SnapshotInfo objects sorted by their detected snapshot time, falling back
        to file mtime when needed.
    """
    infos: List[SnapshotInfo] = []
    for path in paths:
        listings = load_listings(path, limit)
        snapshot_time_value = snapshot_time(listings)
        file_timestamp = path.stat().st_mtime
        infos.append(
            SnapshotInfo(
                path=path,
                listings=listings,
                snapshot_time=snapshot_time_value,
                file_timestamp=file_timestamp,
            )
        )
    return sorted(
        infos,
        key=lambda info: (
            info.snapshot_time if info.snapshot_time is not None else info.file_timestamp,
            info.file_timestamp,
        ),
    )


# Sales persistence schema.
# The sales database is rebuilt on every run, producing a clean store of the
# inferred outcome for every missing listing encountered across one or more
# sequential snapshot pairs. This database is intended for downstream analysis
# by other scripts and tools.
SALES_TABLE = "sales"
SALES_COLUMNS = [
    "before_snapshot",
    "after_snapshot",
    "first_seen_snapshot",
    "first_disappeared_snapshot",
    "status",
    "sold_likelihood",
    "sold_likelihood_percent",
    "elapsed_hours",
    "bucket_min_hours",
    "bucket_max_hours",
    "peer_count",
    "cheaper_peer_count",
    "repost_average_unit_price",
    "reason",
    "listing_json",
]


def clear_sales_db(path: Path) -> None:
    """Create a fresh sales table in the output database.

    Args:
        path: Path to the output sales database.
    """
    with sqlite3.connect(path) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {SALES_TABLE}")
        connection.execute(
            f"CREATE TABLE {SALES_TABLE} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "before_snapshot TEXT, "
            "after_snapshot TEXT, "
            "first_seen_snapshot TEXT, "
            "first_disappeared_snapshot TEXT, "
            "status TEXT, "
            "sold_likelihood REAL, "
            "sold_likelihood_percent REAL, "
            "elapsed_hours REAL, "
            "bucket_min_hours REAL, "
            "bucket_max_hours REAL, "
            "peer_count INTEGER, "
            "cheaper_peer_count INTEGER, "
            "repost_average_unit_price REAL, "
            "reason TEXT, "
            "listing_json TEXT"
            ");"
        )


def sales_row(result: DiffResult) -> Tuple[Any, ...]:
    """Convert one DiffResult into a sales database row.

    Args:
        result: The diff result to serialize.

    Returns:
        A tuple matching SALES_COLUMNS.
    """
    return (
        str(result.before_path) if result.before_path is not None else None,
        str(result.after_path) if result.after_path is not None else None,
        str(result.before_path) if result.before_path is not None else None,
        str(result.after_path) if result.after_path is not None else None,
        result.status,
        result.sold_likelihood,
        round(result.sold_likelihood * 100, 1) if result.sold_likelihood is not None else None,
        result.elapsed_hours,
        result.bucket_min_hours,
        result.bucket_max_hours,
        result.peer_count,
        result.cheaper_peer_count,
        result.repost_average_unit_price,
        result.reason,
        json.dumps(result.listing.as_dict(), default=str),
    )


def write_sales_db(path: Path, results: Sequence[DiffResult]) -> None:
    """Write all inferred sales rows into the output SQLite database.

    The database is cleared and recreated for every run.

    Args:
        path: Path to the output SQLite database.
        results: Sequence of diff results to persist.
    """
    print(f"Writing {len(results)} sales rows to DB: {path}")
    clear_sales_db(path)
    insert_sql = (
        f"INSERT INTO {SALES_TABLE} ({', '.join(SALES_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in SALES_COLUMNS)})"
    )
    with sqlite3.connect(path) as connection:
        connection.executemany(insert_sql, [sales_row(result) for result in results])


def parse_buckets(value: str) -> Dict[int, Tuple[float, float]]:
    """Parse ``code:min-max,code:min-max`` bucket overrides."""
    buckets: Dict[int, Tuple[float, float]] = {}
    try:
        for entry in value.split(","):
            code_text, range_text = entry.split(":", 1)
            minimum_text, maximum_text = range_text.split("-", 1)
            code = int(code_text)
            minimum = float(minimum_text)
            maximum = float(maximum_text)
            if code < 1 or minimum < 0 or maximum <= minimum:
                raise ValueError
            buckets[code] = (minimum, maximum)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "bucket ranges must look like 1:0-0.5,2:0.5-2,3:2-12,4:12-48"
        ) from error
    return buckets


@dataclass
class ClassifierConfig:
    """Hyperparameters for continuous sold/expired/repost classification."""

    bucket_hours: Mapping[int, Tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_BUCKETS)
    )
    timing_weight: float = 0.30
    price_weight: float = 0.20
    continuity_weight: float = 0.20
    timing_decay_power: float = 1.0
    price_sensitivity: float = 1.5
    sold_threshold: float = 0.55
    expired_threshold: float = 0.45
    repost_threshold: float = 0.60
    repost_min_undercut: float = 0.005
    repost_max_undercut: float = 0.50


def classify_timing(
    listing: Listing,
    elapsed_hours: Optional[float],
    buckets: Mapping[int, Tuple[float, float]],
) -> Tuple[str, Optional[float], Optional[float], Optional[float], str]:
    time_left = listing.time_left
    if elapsed_hours is None or time_left not in buckets:
        debug_log(2, debug, 
            f"Classifying item {listing.get('itemName') or listing.get('itemId')!r} as missing: "
            "scan time or time-left bucket unavailable"
        )
        return (
            "missing",
            None,
            buckets.get(time_left, (None, None))[0] if time_left is not None else None,
            buckets.get(time_left, (None, None))[1] if time_left is not None else None,
            "missing because scan time or time-left bucket is unavailable",
        )

    minimum, maximum = buckets[time_left]
    if elapsed_hours < minimum:
        debug_log(2, debug, 
            (
                f"Classifying item {listing.get('itemName') or listing.get('itemId')!r} "
                f"as likely_sold: elapsed={elapsed_hours:.3f}h, "
                f"bucket={minimum:g}-{maximum:g}h"
            )
        )
        return (
            "likely_sold",
            1.0,
            minimum,
            maximum,
            f"disappeared {elapsed_hours:.2f}h after scan, before the {minimum:g}h minimum expiry",
        )
    if elapsed_hours >= maximum:
        debug_log(2, debug, 
            (
                f"Classifying item {listing.get('itemName') or listing.get('itemId')!r} "
                f"as likely_expired: elapsed={elapsed_hours:.3f}h, "
                f"bucket={minimum:g}-{maximum:g}h"
            )
        )
        return (
            "likely_expired",
            0.0,
            minimum,
            maximum,
            (
                f"disappeared {elapsed_hours:.2f}h after scan, "
                f"beyond the {maximum:g}h maximum duration"
            ),
        )
    debug_log(2, debug, 
        f"Classifying item {listing.get('itemName') or listing.get('itemId')!r} as missing: "
        f"elapsed={elapsed_hours:.3f}h, bucket={format_bucket_range(minimum, maximum)}"
    )
    return (
        "missing",
        None,
        minimum,
        maximum,
        f"disappeared during the {format_bucket_range(minimum, maximum)} expiry window",
    )


def price_peer_counts(listing: Listing, surviving: Sequence[Listing]) -> Tuple[int, int]:
    candidate_price = listing.unit_price()
    peers = [peer for peer in surviving if peer.unit_price() is not None]
    cheaper = [
        peer
        for peer in peers
        if candidate_price is not None and peer.unit_price() < candidate_price
    ]
    return len(peers), len(cheaper)


def score_listing(
    listing: Listing,
    elapsed_hours: Optional[float],
    minimum: Optional[float],
    maximum: Optional[float],
    surviving: Sequence[Listing],
    config: ClassifierConfig,
) -> Tuple[float, float, float, float, str, int, int]:
    """Calculate continuous timing, price, and continuity scores and combined sold likelihood.

    Returns:
        (sold_likelihood, timing_score, price_score, continuity_score, evidence_str, peer_count, cheaper_peer_count)
    """
    # 1. Continuous Timing Score: 1.0 at minimum bound, decreasing continuously to 0.0 at maximum bound
    if elapsed_hours is None or minimum is None or maximum is None or maximum <= minimum:
        timing_score = 0.5
    elif elapsed_hours <= minimum:
        timing_score = 1.0
    elif elapsed_hours >= maximum:
        timing_score = 0.0
    else:
        u = (elapsed_hours - minimum) / (maximum - minimum)
        u = max(0.0, min(1.0, u))
        timing_score = max(0.0, min(1.0, (1.0 - u) ** config.timing_decay_power))

    # 2. Continuous Price Score
    candidate_price = listing.unit_price()
    peers = [peer for peer in surviving if peer.unit_price() is not None]
    peer_count = len(peers)
    cheaper_peers = [
        peer
        for peer in peers
        if candidate_price is not None and peer.unit_price() < candidate_price
    ]
    cheaper_peer_count = len(cheaper_peers)

    if peer_count == 0 or candidate_price is None:
        price_score = 0.5
    else:
        rank_score = max(0.0, min(1.0, 1.0 - (cheaper_peer_count / peer_count)))
        peer_prices = [p.unit_price() for p in peers if p.unit_price() is not None]
        median_price = statistics.median(peer_prices) if peer_prices else candidate_price
        if median_price > 0:
            ratio = candidate_price / median_price
            ratio_score = 1.0 / (1.0 + (ratio ** max(0.1, config.price_sensitivity)))
            price_score = 0.6 * rank_score + 0.4 * ratio_score
        else:
            price_score = rank_score
        price_score = max(0.0, min(1.0, price_score))

    # 3. Continuous Continuity Score
    if peer_count == 0:
        continuity_score = 0.5
    elif cheaper_peer_count == 0:
        continuity_score = 0.75
    else:
        continuity_score = max(0.0, min(1.0, 1.0 - (cheaper_peer_count / peer_count)))

    total_weight = config.timing_weight + config.price_weight + config.continuity_weight
    if total_weight > 0:
        score = (
            timing_score * config.timing_weight
            + price_score * config.price_weight
            + continuity_score * config.continuity_weight
        ) / total_weight
    else:
        score = 0.5
    score = max(0.0, min(1.0, score))

    evidence = (
        f"timing={timing_score:.2f}, price={price_score:.2f}, "
        f"cheaper_survivors={cheaper_peer_count}/{peer_count}, "
        f"continuity={continuity_score:.2f}"
    )
    debug_log(3, debug, 
        f"Sold-likelihood for {listing.get('itemName') or listing.get('itemId')!r}: "
        f"score={score:.3f}, {evidence}"
    )
    return (
        score,
        timing_score,
        price_score,
        continuity_score,
        evidence,
        peer_count,
        cheaper_peer_count,
    )


def score_gray_area(
    listing: Listing,
    elapsed_hours: Optional[float],
    minimum: Optional[float],
    maximum: Optional[float],
    surviving: Sequence[Listing],
    timing_weight: float,
    price_weight: float,
    continuity_weight: float,
) -> Tuple[float, str, int, int]:
    """Legacy wrapper for score_gray_area supporting backward compatibility."""
    config = ClassifierConfig(
        timing_weight=timing_weight,
        price_weight=price_weight,
        continuity_weight=continuity_weight,
    )
    score, _, _, _, evidence, peer_count, cheaper_peer_count = score_listing(
        listing, elapsed_hours, minimum, maximum, surviving, config
    )
    return score, evidence, peer_count, cheaper_peer_count


def seller_name(listing: Listing) -> Optional[str]:
    return (
        listing.get("sellerName")
        or listing.get("owner")
        or listing.get("seller")
        or listing.get("ownerName")
    )


def item_identity_name(listing: Listing) -> Any:
    return listing.get("itemName") or listing.get("itemId")


def format_bucket_range(minimum: Optional[float], maximum: Optional[float]) -> str:
    if minimum is None and maximum is None:
        return "unknown"
    if minimum is None:
        return f"up to {maximum:g}h"
    if maximum is None:
        return f"from {minimum:g}h"
    return f"{minimum:g}-{maximum:g}h"


def average_lower_item_price(listing: Listing, candidates: Sequence[Listing]) -> Optional[float]:
    candidate_price = listing.unit_price()
    if candidate_price is None:
        return None
    lower_prices = [
        peer.unit_price()
        for peer in candidates
        if peer.unit_price() is not None and peer.unit_price() < candidate_price
    ]
    if not lower_prices:
        return None
    return sum(lower_prices) / len(lower_prices)


def repost_candidates(
    listing: Listing, after_by_seller_item: Mapping[Tuple[Optional[str], Any], List[Listing]]
) -> Sequence[Listing]:
    return after_by_seller_item[(seller_name(listing), item_identity_name(listing))]


@dataclass
class RepostEvaluation:
    is_repost: bool
    confidence: float
    average_unit_price: Optional[float]
    reason: str


def evaluate_repost(
    listing: Listing,
    candidates: Sequence[Listing],
    config: ClassifierConfig,
) -> RepostEvaluation:
    """Evaluate whether a missing listing was reposted at a lower price.

    Computes continuous multi-factor confidence incorporating undercut ratio,
    stack size match, and new listing lifetime freshness.
    """
    seller = seller_name(listing)
    item_identity = item_identity_name(listing)
    candidate_price = listing.unit_price()
    if seller is None or item_identity is None or candidate_price is None or not candidates:
        return RepostEvaluation(
            is_repost=False, confidence=0.0, average_unit_price=None, reason=""
        )

    matching_lower = []
    best_confidence = 0.0
    for peer in candidates:
        if seller_name(peer) != seller or item_identity_name(peer) != item_identity:
            continue
        peer_price = peer.unit_price()
        if peer_price is None or peer_price >= candidate_price or candidate_price <= 0:
            continue
        undercut_ratio = (candidate_price - peer_price) / candidate_price
        if (
            undercut_ratio < config.repost_min_undercut
            or undercut_ratio > config.repost_max_undercut
        ):
            continue
        matching_lower.append(peer_price)

        # 1. Undercut price subscore: optimal around 0.5% - 10%
        if undercut_ratio <= 0.10:
            price_subscore = 1.0
        else:
            price_subscore = max(
                0.2,
                1.0 - (undercut_ratio - 0.10) / max(0.01, config.repost_max_undercut - 0.10),
            )

        # 2. Stack size match subscore
        stack_subscore = 1.0 if peer.get("stackSize") == listing.get("stackSize") else 0.6

        # 3. Time left freshness subscore (new post has full duration bucket)
        time_subscore = 1.0 if peer.time_left == 4 else (0.7 if peer.time_left == 3 else 0.4)

        peer_confidence = 0.5 * price_subscore + 0.3 * stack_subscore + 0.2 * time_subscore
        if peer_confidence > best_confidence:
            best_confidence = peer_confidence

    if matching_lower and best_confidence >= config.repost_threshold:
        avg_price = sum(matching_lower) / len(matching_lower)
        reason = (
            f"reposted by same seller at lower unit price (avg {avg_price:.1f}c, "
            f"confidence={best_confidence:.2f})"
        )
        return RepostEvaluation(
            is_repost=True,
            confidence=best_confidence,
            average_unit_price=avg_price,
            reason=reason,
        )
    return RepostEvaluation(
        is_repost=False, confidence=best_confidence, average_unit_price=None, reason=""
    )


def is_reposted(listing: Listing, surviving: Sequence[Listing]) -> bool:
    """Legacy check for whether listing has lower-priced surviving peers by same seller."""
    config = ClassifierConfig()
    return evaluate_repost(listing, surviving, config).is_repost


def diff_snapshots(
    before: Sequence[Listing],
    after: Sequence[Listing],
    buckets: Optional[Mapping[int, Tuple[float, float]]] = None,
    timing_weight: float = 0.30,
    price_weight: float = 0.20,
    continuity_weight: float = 0.20,
    before_path: Optional[Path] = None,
    after_path: Optional[Path] = None,
    config: Optional[ClassifierConfig] = None,
    **kwargs: Any,
) -> List[DiffResult]:
    if config is None:
        bucket_map = buckets if buckets is not None else DEFAULT_BUCKETS
        config = ClassifierConfig(
            bucket_hours=bucket_map,
            timing_weight=timing_weight,
            price_weight=price_weight,
            continuity_weight=continuity_weight,
            timing_decay_power=kwargs.get("timing_decay_power", 1.0),
            price_sensitivity=kwargs.get("price_sensitivity", 1.5),
            sold_threshold=kwargs.get("sold_threshold", 0.55),
            expired_threshold=kwargs.get("expired_threshold", 0.45),
            repost_threshold=kwargs.get("repost_threshold", 0.60),
            repost_min_undercut=kwargs.get("repost_min_undercut", 0.005),
            repost_max_undercut=kwargs.get("repost_max_undercut", 0.50),
        )

    print("Matching listings from before snapshot to after snapshot")
    after_counts = Counter(listing.match_key() for listing in after)
    surviving_by_identity: Dict[Tuple[Any, ...], List[Listing]] = defaultdict(list)
    after_by_seller_item: Dict[Tuple[Optional[str], Any], List[Listing]] = defaultdict(list)
    for listing in after:
        surviving_by_identity[listing.identity_key()].append(listing)
        after_by_seller_item[(seller_name(listing), item_identity_name(listing))].append(listing)
    missing: List[Listing] = []
    for listing in tqdm(before, desc="Matching listings", unit="listing"):
        key = listing.match_key()
        if after_counts[key]:
            after_counts[key] -= 1
        else:
            missing.append(listing)
    debug_log(1, debug, 
        f"Matched {len(before) - len(missing)} listing row(s); "
        f"found {len(missing)} missing row(s)"
    )

    before_time = snapshot_time(before)
    after_time = snapshot_time(after)
    elapsed_hours = (
        (after_time - before_time) / 3600.0
        if before_time is not None and after_time is not None and after_time >= before_time
        else None
    )
    debug_log(1, debug, f"Elapsed scan interval: {elapsed_hours!r} hour(s)")
    print("Classifying missing listings and estimating sold likelihood")
    results: List[DiffResult] = []
    for listing in tqdm(missing, desc="Classifying missing listings", unit="listing"):
        time_left = listing.time_left
        minimum, maximum = (
            config.bucket_hours.get(time_left, (None, None))
            if time_left is not None
            else (None, None)
        )
        surviving = surviving_by_identity.get(listing.identity_key(), [])

        (
            score,
            timing_s,
            price_s,
            cont_s,
            evidence,
            peer_count,
            cheaper_peer_count,
        ) = score_listing(listing, elapsed_hours, minimum, maximum, surviving, config)

        candidates = repost_candidates(listing, after_by_seller_item)
        repost_eval = evaluate_repost(listing, candidates, config)

        repost_average_unit_price = None
        if repost_eval.is_repost and (
            elapsed_hours is None or maximum is None or elapsed_hours < maximum
        ):
            status = "repost"
            likelihood = score
            reason = f"disappeared; {repost_eval.reason}; {evidence}"
            repost_average_unit_price = repost_eval.average_unit_price
        elif elapsed_hours is not None and minimum is not None and elapsed_hours < minimum:
            status = "likely_sold"
            likelihood = max(score, 0.90)
            reason = (
                f"disappeared {elapsed_hours:.2f}h after scan, "
                f"before the {minimum:g}h minimum expiry; {evidence}"
            )
        elif elapsed_hours is not None and maximum is not None and elapsed_hours >= maximum:
            status = "likely_expired"
            likelihood = min(score, 0.10)
            reason = (
                f"disappeared {elapsed_hours:.2f}h after scan, "
                f"beyond the {maximum:g}h maximum duration; {evidence}"
            )
        else:
            # Gray window classification using continuous score thresholds
            if score >= config.sold_threshold:
                status = "likely_sold"
                likelihood = score
                reason = (
                    f"disappeared during the {minimum:g}-{maximum:g}h expiry window "
                    f"(score={score:.2f} >= {config.sold_threshold:.2f}); {evidence}"
                )
            elif score <= config.expired_threshold:
                status = "likely_expired"
                likelihood = score
                reason = (
                    f"disappeared during the {format_bucket_range(minimum, maximum)} expiry window "
                    f"(score={score:.2f} <= {config.expired_threshold:.2f}); {evidence}"
                )
            else:
                status = "missing"
                likelihood = score
                reason = (
                    f"disappeared during the {format_bucket_range(minimum, maximum)} expiry window "
                    f"(uncertain score={score:.2f}); {evidence}"
                )

        results.append(
            DiffResult(
                listing=listing,
                status=status,
                sold_likelihood=likelihood,
                elapsed_hours=elapsed_hours,
                bucket_min_hours=minimum,
                bucket_max_hours=maximum,
                reason=reason,
                before_path=before_path,
                after_path=after_path,
                peer_count=peer_count,
                cheaper_peer_count=cheaper_peer_count,
                repost_average_unit_price=repost_average_unit_price,
            )
        )
    return results


def write_csv(path: Path, results: Sequence[DiffResult]) -> None:
    print(f"Writing {len(results)} result row(s) to CSV: {path}")
    debug_log(1, debug, f"Writing {len(results)} result row(s) to CSV: {path}")
    rows = [result.as_dict() for result in results]
    helper_write_csv(path, rows)


def write_text_report(
    path: Path, results: Sequence[DiffResult], after_listings: Sequence[Listing]
) -> None:
    print(f"Writing text report: {path}")
    debug_log(1, debug, f"Writing {len(results)} result row(s) to text report: {path}")

    sections = [
        ("likely_sold", "These items likely sold because..."),
        ("repost", "These items appear to have reposted at a lower price..."),
        ("missing", "These items we're unsure about but have this likelihood %..."),
        ("likely_expired", "These items likely expired because..."),
    ]

    def item_name(result: DiffResult) -> str:
        listing = result.listing
        return str(listing.get("itemName") or listing.get("itemId") or "Unknown item")

    def item_likelihood(result: DiffResult) -> float:
        return result.sold_likelihood if result.sold_likelihood is not None else 0.5

    def average_likelihood_by_name(items: Sequence[DiffResult]) -> Dict[str, float]:
        totals: Dict[str, float] = {}
        counts: Dict[str, int] = {}
        for result in items:
            name = item_name(result)
            totals[name] = totals.get(name, 0.0) + item_likelihood(result)
            counts[name] = counts.get(name, 0) + 1
        return {name: totals[name] / counts[name] for name in totals}

    def item_line(result: DiffResult, avg: float) -> str:
        listing = result.listing
        bucket = (
            f"{result.bucket_min_hours:g}h-{result.bucket_max_hours:g}h"
            if result.bucket_min_hours is not None and result.bucket_max_hours is not None
            else "bucket unknown"
        )
        price_value = listing.unit_price()
        price_text = format_money(price_value)
        seller = (
            listing.get("sellerName")
            or listing.get("owner")
            or listing.get("seller")
            or listing.get("ownerName")
            or "Unknown seller"
        )
        quantity = listing.get("stackSize")
        likelihood = item_likelihood(result)
        likelihood_text = f"likelihood: {round(likelihood * 100, 1)}%"
        if result.peer_count:
            if result.cheaper_peer_count == 0:
                price_trend = "lowest unit price among peers"
            else:
                price_trend = (
                    f"{result.cheaper_peer_count}/{result.peer_count} peers with lower unit price"
                )
        else:
            price_trend = "no surviving peer price data"
        repost_price_text = ""
        if result.status == "repost" and result.repost_average_unit_price is not None:
            repost_price_text = (
                f" - repost average unit price: {format_money(result.repost_average_unit_price)}"
            )
        reason_text = result.reason
        return (
            f"{likelihood_text} - price/item: {price_text} - {price_trend} - {bucket} - "
            f"Seller: {seller} - Quantity: {quantity or 'unknown'}{repost_price_text} - "
            f"{item_name(result)} - {reason_text}"
        )

    def repost_aggregates(results: Sequence[DiffResult]) -> Tuple[List[str], List[str]]:
        reposts = [
            result
            for result in results
            if result.status == "repost"
            and result.repost_average_unit_price is not None
            and result.listing.unit_price() is not None
        ]
        if not reposts:
            return [], []

        seller_stats: Dict[str, Dict[str, float]] = {}
        item_stats: Dict[Any, Dict[str, Any]] = {}
        for result in reposts:
            seller = seller_name(result.listing) or "Unknown seller"
            item_key = item_identity_name(result.listing)
            item_label = item_name(result)
            original_price = result.listing.unit_price()
            repost_price = result.repost_average_unit_price
            if original_price is None or repost_price is None:
                continue
            price_drop = max(0.0, original_price - repost_price)
            pct_drop = price_drop / original_price if original_price else 0.0

            seller_record = seller_stats.setdefault(
                seller,
                {"count": 0.0, "drop": 0.0, "pct": 0.0, "repost_price": 0.0},
            )
            seller_record["count"] += 1.0
            seller_record["drop"] += price_drop
            seller_record["pct"] += pct_drop
            seller_record["repost_price"] += repost_price

            item_record = item_stats.setdefault(
                item_key,
                {
                    "label": item_label,
                    "count": 0.0,
                    "drop": 0.0,
                    "pct": 0.0,
                    "repost_price": 0.0,
                },
            )
            item_record["count"] += 1.0
            item_record["drop"] += price_drop
            item_record["pct"] += pct_drop
            item_record["repost_price"] += repost_price

        seller_lines: List[Tuple[str, Dict[str, float]]] = sorted(
            seller_stats.items(),
            key=lambda pair: (
                -pair[1]["count"],
                -pair[1]["pct"] / pair[1]["count"],
                -pair[1]["drop"] / pair[1]["count"],
            ),
        )[:5]
        item_lines: List[Tuple[Any, Dict[str, Any]]] = sorted(
            item_stats.items(),
            key=lambda pair: (
                -pair[1]["count"],
                str(pair[1]["label"]).lower(),
            ),
        )[:5]

        seller_summary: List[str] = []
        for seller, stats in seller_lines:
            count = int(stats["count"])
            avg_pct = stats["pct"] / stats["count"]
            avg_repost_price = stats["repost_price"] / stats["count"]
            seller_summary.append(
                "- "
                f"{seller}: {count} repost(s), avg price drop {round(avg_pct * 100, 1)}%, "
                f"avg repost unit price {format_money(avg_repost_price)}"
            )

        item_summary: List[str] = []
        for _, stats in item_lines:
            count = int(stats["count"])
            avg_pct = stats["pct"] / stats["count"]
            avg_repost_price = stats["repost_price"] / stats["count"]
            item_summary.append(
                "- "
                f"{stats['label']}: {count} repost(s), avg price drop {round(avg_pct * 100, 1)}%, "
                f"avg repost unit price {format_money(avg_repost_price)}"
            )

        return seller_summary, item_summary

    def seller_listing_aggregates(listings: Sequence[Listing]) -> List[str]:
        counts: Dict[str, int] = {}
        quantities_by_seller_item: Dict[str, Dict[Any, int]] = {}
        seller_price_totals: Dict[str, Dict[Any, float]] = {}
        seller_price_counts: Dict[str, Dict[Any, int]] = {}
        item_prices: Dict[Any, List[float]] = {}
        for listing in listings:
            seller = seller_name(listing) or "Unknown seller"
            counts[seller] = counts.get(seller, 0) + 1
            quantity = listing.get("stackSize")
            item_key = item_identity_name(listing)
            if isinstance(quantity, (int, float)) and quantity > 0:
                seller_items = quantities_by_seller_item.setdefault(seller, {})
                seller_items[item_key] = seller_items.get(item_key, 0) + int(quantity)
            price = listing.unit_price()
            if price is not None:
                item_prices.setdefault(item_key, []).append(price)
                seller_price_totals.setdefault(seller, {})[item_key] = (
                    seller_price_totals.setdefault(seller, {}).get(item_key, 0.0) + price
                )
                seller_price_counts.setdefault(seller, {})[item_key] = (
                    seller_price_counts.setdefault(seller, {}).get(item_key, 0) + 1
                )

        item_percentiles: Dict[Any, float] = {}
        for item_key, prices in item_prices.items():
            if not prices:
                continue
            sorted_prices = sorted(prices)
            median_price = statistics.median(sorted_prices)
            rank = sum(1 for price in sorted_prices if price < median_price)
            percentile = (rank / len(sorted_prices)) * 100.0
            item_percentiles[item_key] = percentile

        top_sellers = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0].lower()))[:5]
        lines: List[str] = []
        for seller, count in top_sellers:
            lines.append(f"- {seller}: {count} listings")
            item_quantities = quantities_by_seller_item.get(seller, {})
            top_items = sorted(
                item_quantities.items(),
                key=lambda pair: (-pair[1], str(pair[0]).lower()),
            )[:5]
            for item_key, total_qty in top_items:
                item_label = str(item_key)
                percentile = item_percentiles.get(item_key)
                percentile_text = (
                    f" (More expensive than {percentile:.1f}% of all listings)"
                    if percentile is not None
                    else ""
                )
                avg_price = None
                price_total = seller_price_totals.get(seller, {}).get(item_key)
                price_count = seller_price_counts.get(seller, {}).get(item_key)
                if price_total is not None and price_count:
                    avg_price = price_total / price_count
                avg_price_text = (
                    f"(avg {format_money(avg_price)}) " if avg_price is not None else ""
                )
                lines.append(
                    f"  * {item_label}: {total_qty} total units {avg_price_text}{percentile_text}"
                )
        return lines

    def outlandish_sellers(listings: Sequence[Listing]) -> List[str]:
        # Use listings with at least 10 units and a valid unit price.
        item_prices: Dict[Any, List[float]] = {}
        for listing in listings:
            quantity = listing.get("stackSize")
            price = listing.unit_price()
            if price is None or quantity is None:
                continue
            if isinstance(quantity, (int, float)) and quantity >= 10:
                item_key = item_identity_name(listing)
                item_prices.setdefault(item_key, []).append(price)

        item_stats: Dict[Any, Tuple[float, float]] = {}
        for item_key, prices in item_prices.items():
            if len(prices) < 2:
                continue
            median_price = statistics.median(prices)
            stdev_price = statistics.pstdev(prices)
            if stdev_price > 0:
                item_stats[item_key] = (median_price, stdev_price)

        if not item_stats:
            return []

        seller_counts: Dict[str, int] = {}
        seller_outliers: Dict[str, int] = {}
        for listing in listings:
            seller = seller_name(listing) or "Unknown seller"
            seller_counts[seller] = seller_counts.get(seller, 0) + 1

        for listing in listings:
            seller = seller_name(listing) or "Unknown seller"
            if seller_counts.get(seller, 0) < 20:
                continue
            quantity = listing.get("stackSize")
            price = listing.unit_price()
            if price is None or quantity is None:
                continue
            if not isinstance(quantity, (int, float)) or quantity < 10:
                continue
            item_key = item_identity_name(listing)
            stats = item_stats.get(item_key)
            if stats is None:
                continue
            median_price, stdev_price = stats
            if price > median_price + stdev_price:
                seller_outliers[seller] = seller_outliers.get(seller, 0) + 1

        top_outliers = sorted(
            seller_outliers.items(),
            key=lambda pair: (-pair[1], pair[0].lower()),
        )[:5]
        return [f"- {seller}: {count} outlandish listing(s)" for seller, count in top_outliers]

    lines = []
    for status, heading in sections:
        matching = [result for result in results if result.status == status]
        lines.append(heading)
        if matching:
            averages = average_likelihood_by_name(matching)
            matching.sort(
                key=lambda result: (
                    averages[item_name(result)],
                    item_name(result).lower(),
                    item_likelihood(result),
                )
            )
            for result in matching:
                avg = averages[item_name(result)]
                lines.append(f"- {item_line(result, avg)}")
        else:
            lines.append("- none")
        lines.append("")

    seller_summary, item_summary = repost_aggregates(results)
    lines.append("Most aggressive repost sellers:")
    if seller_summary:
        lines.extend(seller_summary)
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Most aggressively reposted items:")
    if item_summary:
        lines.extend(item_summary)
    else:
        lines.append("- none")
    lines.append("")

    lines.append("Top sellers by listing count:")
    listing_summary = seller_listing_aggregates(after_listings)
    if listing_summary:
        lines.extend(listing_summary)
    else:
        lines.append("- none")
    lines.append("")

    lines.append("Sellers with the most outlandish listings:")
    outlandish_summary = outlandish_sellers(after_listings)
    if outlandish_summary:
        lines.extend(outlandish_summary)
    else:
        lines.append("- none")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = make_arg_parser(
        description=__doc__,
        include_debug=False,
        include_verbose=False,
        include_input=False,
        include_output=False,
        include_log=False,
    )
    parser.add_argument(
        "--scan-dir",
        type=Path,
        help=(
            "directory containing .db snapshot files to diff sequentially; "
            "if omitted, the current directory is used"
        ),
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("auction_scan_diff.csv"),
        metavar="FILE",
        help="write detailed missing-listing results as CSV (default auction_scan_diff.csv)",
    )
    parser.add_argument(
        "--sales-db",
        type=Path,
        default=Path("sales.db"),
        help="write inferred sales and expired result data into a fresh SQLite database",
    )
    parser.add_argument(
        "--json",
        type=Path,
        help="also write detailed missing-listing results as JSON",
    )
    parser.add_argument(
        "--text-report",
        type=Path,
        help=(
            "write a simple text report summarizing likely sold, expired, "
            "and uncertain listings (default uses CSV base name with .txt)"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="load at most N rows from each snapshot for testing",
    )
    parser.add_argument(
        "--bucket-hours",
        type=parse_buckets,
        default=DEFAULT_BUCKETS,
        metavar="RANGES",
        help="override timeLeft bounds, e.g. 1:0-0.5,2:0.5-2,3:2-12,4:12-48",
    )
    parser.add_argument("--timing-weight", type=float, default=0.30)
    parser.add_argument("--price-weight", type=float, default=0.20)
    parser.add_argument("--continuity-weight", type=float, default=0.20)
    parser.add_argument("--timing-decay-power", type=float, default=1.0)
    parser.add_argument("--price-sensitivity", type=float, default=1.5)
    parser.add_argument("--sold-threshold", type=float, default=0.55)
    parser.add_argument("--expired-threshold", type=float, default=0.45)
    parser.add_argument("--repost-threshold", type=float, default=0.60)
    parser.add_argument("--repost-min-undercut", type=float, default=0.005)
    parser.add_argument("--repost-max-undercut", type=float, default=0.50)
    parser.add_argument(
        "--debug",
        type=int,
        choices=range(4),
        default=0,
        help="enable verbose logging from 0 (off) through 3 (details)",
    )
    return parser


def classifier_config_from_args(args: argparse.Namespace) -> ClassifierConfig:
    return ClassifierConfig(
        bucket_hours=args.bucket_hours,
        timing_weight=args.timing_weight,
        price_weight=args.price_weight,
        continuity_weight=args.continuity_weight,
        timing_decay_power=args.timing_decay_power,
        price_sensitivity=args.price_sensitivity,
        sold_threshold=args.sold_threshold,
        expired_threshold=args.expired_threshold,
        repost_threshold=args.repost_threshold,
        repost_min_undercut=args.repost_min_undercut,
        repost_max_undercut=args.repost_max_undercut,
    )


def write_json(path: Path, results: Sequence[DiffResult]) -> None:
    print(f"Writing {len(results)} result row(s) to JSON: {path}")
    debug_log(1, debug, f"Writing {len(results)} result row(s) to JSON: {path}")
    helper_write_json(path, [result.as_dict() for result in results], indent=2, ensure_ascii=False)


def format_duration(seconds: float) -> str:
    return f"{seconds:.3f}s"


def run_directory_pipeline(
    directory: Path,
    args: argparse.Namespace,
    csv_path: Path,
    json_path: Optional[Path],
    text_path: Optional[Path],
) -> Tuple[List[DiffResult], Dict[str, float], List[SnapshotInfo]]:
    timings: Dict[str, float] = {}
    start = time.perf_counter()

    section_start = time.perf_counter()
    db_paths = find_db_paths(directory, exclude=args.sales_db)
    validate_snapshot_schema(db_paths)
    timings["discover"] = time.perf_counter() - section_start

    section_start = time.perf_counter()
    snapshot_infos = load_snapshot_infos(db_paths, args.limit)
    timings["load_snapshots"] = time.perf_counter() - section_start

    if len(snapshot_infos) < 2:
        raise ValueError(
            f"Need at least two .db snapshots in {directory} to perform sequential diffs"
        )

    config = classifier_config_from_args(args)
    section_start = time.perf_counter()
    all_results: List[DiffResult] = []
    for before_snapshot, after_snapshot in zip(snapshot_infos, snapshot_infos[1:]):
        debug_log(1, debug, f"Diffing sequential snapshots: {before_snapshot.path} -> {after_snapshot.path}")
        results = diff_snapshots(
            before_snapshot.listings,
            after_snapshot.listings,
            config=config,
            before_path=before_snapshot.path,
            after_path=after_snapshot.path,
        )
        all_results.extend(results)
    timings["diff"] = time.perf_counter() - section_start

    section_start = time.perf_counter()
    write_sales_db(args.sales_db, all_results)
    timings["write_sales_db"] = time.perf_counter() - section_start

    section_start = time.perf_counter()
    write_csv(csv_path, all_results)
    timings["write_csv"] = time.perf_counter() - section_start

    if json_path:
        section_start = time.perf_counter()
        write_json(json_path, all_results)
        timings["write_json"] = time.perf_counter() - section_start

    if text_path:
        section_start = time.perf_counter()
        write_text_report(text_path, all_results, snapshot_infos[-1].listings)
        timings["write_text_report"] = time.perf_counter() - section_start

    timings["total"] = time.perf_counter() - start
    return all_results, timings, snapshot_infos


def print_timings(times: Mapping[str, float]) -> None:
    print("Section timings:")
    for section in [
        "discover",
        "load_snapshots",
        "diff",
        "write_sales_db",
        "write_csv",
        "write_json",
        "write_text_report",
        "total",
    ]:
        if section in times:
            print(f"  {section}: {format_duration(times[section])}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    global debug
    args = build_parser().parse_args(argv)
    debug = args.debug
    debug_log(1, debug, "Starting auction snapshot diff")
    weights = (args.timing_weight, args.price_weight, args.continuity_weight)
    if any(weight < 0 for weight in weights) or sum(weights) <= 0:
        print(
            "weights must be non-negative and at least one must be positive",
            file=sys.stderr,
        )
        return 2

    if args.text_report is None:
        args.text_report = args.csv.with_suffix(".txt")

    try:
        if args.scan_dir is None:
            args.scan_dir = Path(".")

        print(f"Scanning .db files in {args.scan_dir}")
        results, timings, snapshot_infos = run_directory_pipeline(
            args.scan_dir,
            args,
            args.csv,
            args.json,
            args.text_report,
        )
        print("Loaded snapshots successfully")
        print("Completed sequential diff computation")
        print_timings(timings)
        print(
            f"Processed {len(snapshot_infos)} snapshots and "
            f"{len(snapshot_infos) - 1} sequential diff pairs"
        )
        print(
            f"First snapshot: {snapshot_infos[0].path}, "
            f"last snapshot: {snapshot_infos[-1].path}"
        )
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
