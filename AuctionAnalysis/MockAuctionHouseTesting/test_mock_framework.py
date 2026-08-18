#!/usr/bin/env python3
"""Sanity check unit tests for MockDBData generation and validation framework.

This is a barebones, dependency-free test script that exercises the core
functions of generate.py and validate.py.

These tests contain real assertions and data structures, and are expected to
fail currently because the underlying architecture functions are stubbed out.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from pathlib import Path

import generate
import validate

# ============================================================================
# Generation Module Unit Tests
# ============================================================================


def test_seedMockDatabase() -> None:
    """Sanity check seedMockDatabase initialization."""
    config = generate.SimulationConfig(item_count=5, seller_count=3, buyer_count=5)
    catalog = [
        generate.ItemMarketProfile(
            item_id=101,
            item_name="Peacebloom",
            golden_market_price=1000.0,
            daily_demand_volume=50,
            daily_supply_volume=50,
        )
    ]
    state = generate.seedMockDatabase(config, catalog)
    assert isinstance(state, generate.MarketState), "Expected MarketState instance"
    assert len(state.sellers) == 3, f"Expected 3 sellers, got {len(state.sellers)}"
    assert len(state.buyers) == 5, f"Expected 5 buyers, got {len(state.buyers)}"
    assert 101 in state.catalog, "Expected item 101 in catalog"
    assert len(state.active_listings) > 0, "Expected initial active listings"


def test_initializeActors() -> None:
    """Sanity check actor instantiation."""
    config = generate.SimulationConfig(seller_count=4, buyer_count=8)
    sellers, buyers = generate.initializeActors(config)
    assert len(sellers) == 4, f"Expected 4 sellers, got {len(sellers)}"
    assert len(buyers) == 8, f"Expected 8 buyers, got {len(buyers)}"
    assert isinstance(sellers[0], generate.SellerActor)
    assert isinstance(buyers[0], generate.BuyerActor)


def test_addListing() -> None:
    """Sanity check adding a listing to the active market state."""
    state = generate.MarketState()
    listing = generate.MockListing(
        listing_id=1,
        item_id=101,
        item_name="Peacebloom",
        seller_name="SellerA",
        stack_size=20,
        buyout_price=20000,
        min_bid=18000,
        start_time=1000.0,
        duration_hours=24.0,
    )
    generate.addListing(state, listing)
    assert 1 in state.active_listings, "Listing 1 should be present in active_listings"
    assert len(state.event_history) == 1, "Expected 1 recorded event"
    assert state.event_history[0].event_type == "post"


def test_maybeList() -> None:
    """Sanity check seller listing probability evaluation."""
    seller = generate.SellerActor(seller_id="s1", name="SellerA", desperation=0.1)
    item = generate.ItemMarketProfile(101, "Peacebloom", 1000.0, 50, 50)
    current_listings: list[generate.MockListing] = []
    decision = generate.maybeList(seller, item, current_listings)
    assert isinstance(decision, bool), "Expected boolean listing decision"


def test_calculateUndercutPrice() -> None:
    """Sanity check undercut price calculation."""
    item = generate.ItemMarketProfile(101, "Peacebloom", 1000.0, 50, 50)
    seller = generate.SellerActor("s1", "SellerA", floor_price_ratio=0.7)
    price = generate.calculateUndercutPrice(item, 1000.0, seller)
    assert 700.0 <= price < 1000.0, f"Expected undercut price between 700 and 1000, got {price}"


def test_maybeBuy() -> None:
    """Sanity check buyer purchasing probability evaluation."""
    buyer = generate.BuyerActor(buyer_id="b1", name="BuyerA", max_price_ratio=1.2)
    item = generate.ItemMarketProfile(101, "Peacebloom", 1000.0, 50, 50)
    listings = [generate.MockListing(1, 101, "Peacebloom", "SellerA", 1, 950, 900, 1000.0, 24.0)]
    decision = generate.maybeBuy(buyer, item, listings)
    assert isinstance(decision, bool), "Expected boolean purchase decision"


def test_executeBuy() -> None:
    """Sanity check purchase execution against active listings (all-or-nothing listing purchase)."""
    state = generate.MarketState()
    # A listing can be posted for any quantity (e.g. 13 items). A buyer must purchase the entire listing (all 13).
    listing = generate.MockListing(1, 101, "Peacebloom", "SellerA", 13, 13000, 11700, 1000.0, 24.0)
    state.active_listings[1] = listing
    buyer = generate.BuyerActor("b1", "BuyerA")
    # Even if target_quantity is 10, the buyer must buy the entire listing of 13 items (all or nothing)
    events = generate.executeBuy(state, buyer, item_id=101, target_quantity=10)
    assert len(events) == 1, "Expected exactly 1 purchase event"
    assert events[0].event_type == "buy"
    assert (
        events[0].details["quantity"] == 13
    ), "Expected entire listing quantity (13) to be purchased"
    assert listing.status == "sold", "Expected listing to be marked sold"
    assert (
        1 not in state.active_listings
    ), "Expected sold listing to be removed from active listings"


def test_repostListing() -> None:
    """Sanity check canceling and reposting at a lower price."""
    state = generate.MarketState()
    old_listing = generate.MockListing(
        1, 101, "Peacebloom", "SellerA", 20, 20000, 18000, 1000.0, 24.0
    )
    state.active_listings[1] = old_listing
    seller = generate.SellerActor("s1", "SellerA")
    event, new_listing = generate.repostListing(state, seller, old_listing, new_unit_price=900.0)
    assert event.event_type == "repost"
    assert new_listing.buyout_price == 18000
    assert 1 not in state.active_listings


def test_expireListings() -> None:
    """Sanity check expiration of lapsed listings."""
    state = generate.MarketState()
    expired_listing = generate.MockListing(
        1, 101, "Peacebloom", "SellerA", 20, 20000, 18000, 1000.0, 2.0  # 2 hours duration
    )
    state.active_listings[1] = expired_listing
    # Advance time 3 hours past start
    events = generate.expireListings(state, current_time=1000.0 + 3 * 3600.0)
    assert len(events) == 1, "Expected 1 expired listing event"
    assert 1 not in state.active_listings


def test_calculateTimeLeftBucket() -> None:
    """Sanity check duration bucket classification."""
    start = 1000.0
    duration = 24.0  # 24 hours total
    # 20 hours elapsed -> 4 hours remaining -> bucket 3 (2-12 hours)
    bucket = generate.calculateTimeLeftBucket(start, duration, start + 20 * 3600)
    assert bucket == 3, f"Expected bucket 3, got {bucket}"


def test_takeSnapshot() -> None:
    """Sanity check SQLite snapshot export."""
    state = generate.MarketState()
    out_path = Path("/tmp/test_snapshot.db")
    result = generate.takeSnapshot(state, out_path, "scan_01")
    assert isinstance(result, Path)
    assert result.exists()


def test_recordGroundTruth() -> None:
    """Sanity check ground truth persistence."""
    state = generate.MarketState()
    out_path = Path("/tmp/test_ground_truth.json")
    result = generate.recordGroundTruth(state, out_path)
    assert isinstance(result, Path)


# ============================================================================
# Validation Module Unit Tests
# ============================================================================


def test_discoverSnapshots() -> None:
    """Sanity check snapshot directory discovery."""
    snapshots = validate.discoverSnapshots(Path("./data"))
    assert isinstance(snapshots, list)


def test_loadGroundTruth() -> None:
    """Sanity check loading ground truth records."""
    records = validate.loadGroundTruth(Path("./data/ground_truth.json"))
    assert isinstance(records, dict)


def test_runAuctionScanDiff() -> None:
    """Sanity check invoking AuctionScanDiff pipeline."""
    result = validate.runAuctionScanDiff(
        Path("../AuctionScanDiff.py"),
        Path("./data"),
        Path("/tmp/sales.db"),
    )
    assert result is not None, "AuctionScanDiff script did not run: verify the path and environment"
    stderr_lines = [line for line in result.stderr.splitlines() if line.strip()]
    stderr_short = stderr_lines[-1] if stderr_lines else "<no stderr>"
    assert result.returncode == 0, (
        f"AuctionScanDiff failed rc={result.returncode}; stderr={stderr_short}"
    )


def test_loadDiffResults() -> None:
    """Sanity check parsing sales.db predictions."""
    predictions = validate.loadDiffResults(Path("/tmp/sales.db"))
    assert isinstance(predictions, list)


def test_matchPredictionToGroundTruth() -> None:
    """Sanity check prediction-to-ground-truth association."""
    prediction = validate.DiffPredictionRecord(
        before_snapshot="scan_01.db",
        after_snapshot="scan_02.db",
        item_id=101,
        item_name="Peacebloom",
        seller_name="SellerA",
        stack_size=20,
        buyout_price=20000,
        predicted_status="likely_sold",
        sold_likelihood=0.9,
        reason="bucket duration",
    )
    ground_truth = {
        "101_SellerA_20_1000": [
            validate.GroundTruthRecord(
                listing_id=1,
                item_id=101,
                item_name="Peacebloom",
                seller_name="SellerA",
                unit_price=1000.0,
                stack_size=20,
                post_time=1000.0,
                actual_outcome="sold",
            )
        ]
    }
    match = validate.matchPredictionToGroundTruth(prediction, ground_truth)
    assert match is not None
    assert match.actual_outcome == "sold"


def test_evaluatePredictions() -> None:
    """Sanity check batch evaluation of predictions."""
    predictions = [
        validate.DiffPredictionRecord(
            "scan_01.db",
            "scan_02.db",
            101,
            "Peacebloom",
            "SellerA",
            20,
            20000,
            "likely_sold",
            0.9,
            "reason",
        )
    ]
    ground_truth = {
        "101_SellerA_20_1000": [
            validate.GroundTruthRecord(1, 101, "Peacebloom", "SellerA", 1000.0, 20, 1000.0, "sold")
        ]
    }
    records = validate.evaluatePredictions(predictions, ground_truth)
    assert len(records) == 1
    assert records[0].is_correct is True


def test_computeMetrics() -> None:
    """Sanity check accuracy, precision, recall, and F1 calculations."""
    records = [
        validate.EvaluationRecord(
            prediction=validate.DiffPredictionRecord(
                "s1", "s2", 1, "Item", "Seller", 1, 10, "likely_sold", 0.9, "r"
            ),
            ground_truth=None,
            is_correct=True,
            error_category="none",
        )
    ]
    metrics = validate.computeMetrics(records)
    assert isinstance(metrics, validate.ValidationMetrics)
    assert metrics.total_evaluated == 1
    assert metrics.accuracy == 1.0


def test_extractFailureCases() -> None:
    """Sanity check extraction of misclassified examples."""
    records = [
        validate.EvaluationRecord(
            prediction=validate.DiffPredictionRecord(
                "s1", "s2", 1, "Item", "Seller", 1, 10, "likely_sold", 0.9, "r"
            ),
            ground_truth=None,
            is_correct=False,
            error_category="sold_vs_expired_confusion",
        )
    ]
    failures = validate.extractFailureCases(records, max_cases=5)
    assert isinstance(failures, list)
    assert len(failures) == 1


def test_generateValidationReport() -> None:
    """Sanity check validation report text generation."""
    metrics = validate.ValidationMetrics(total_evaluated=10, correct_count=8, accuracy=0.8)
    failures: list[validate.FailureCase] = []
    report = validate.generateValidationReport(metrics, failures)
    assert isinstance(report, str)
    assert "Validation Report" in report


# ============================================================================
# Test Runner Harness
# ============================================================================

ALL_TESTS: list[tuple[str, Callable[[], None]]] = [
    ("test_seedMockDatabase", test_seedMockDatabase),
    ("test_initializeActors", test_initializeActors),
    ("test_addListing", test_addListing),
    ("test_maybeList", test_maybeList),
    ("test_calculateUndercutPrice", test_calculateUndercutPrice),
    ("test_maybeBuy", test_maybeBuy),
    ("test_executeBuy", test_executeBuy),
    ("test_repostListing", test_repostListing),
    ("test_expireListings", test_expireListings),
    ("test_calculateTimeLeftBucket", test_calculateTimeLeftBucket),
    ("test_takeSnapshot", test_takeSnapshot),
    ("test_recordGroundTruth", test_recordGroundTruth),
    ("test_discoverSnapshots", test_discoverSnapshots),
    ("test_loadGroundTruth", test_loadGroundTruth),
    ("test_runAuctionScanDiff", test_runAuctionScanDiff),
    ("test_loadDiffResults", test_loadDiffResults),
    ("test_matchPredictionToGroundTruth", test_matchPredictionToGroundTruth),
    ("test_evaluatePredictions", test_evaluatePredictions),
    ("test_computeMetrics", test_computeMetrics),
    ("test_extractFailureCases", test_extractFailureCases),
    ("test_generateValidationReport", test_generateValidationReport),
]


def run_all_tests() -> int:
    """Run all sanity checks and display results."""
    print("=" * 70)
    print("Running MockDBData Architecture Sanity Checks")
    print("Note: All stubbed functions are expected to fail until implemented.")
    print("=" * 70)

    passed = 0
    failed = 0

    for test_name, test_fn in ALL_TESTS:
        try:
            test_fn()
            print(f"  [PASS] {test_name}")
            passed += 1
        except NotImplementedError:
            print(f"  [FAIL - Stubbed] {test_name} (NotImplementedError)")
            failed += 1
        except AssertionError as err:
            message = str(err).splitlines()[0] if err.args else "assertion failed"
            print(f"  [FAIL - Assertion] {test_name}: {message}")
            failed += 1
        except Exception as exc:
            print(f"  [FAIL - Error] {test_name}: {exc}")
            failed += 1

    print("-" * 70)
    print(f"Results: {passed} passed, {failed} failed (out of {len(ALL_TESTS)} tests)")
    print("=" * 70)

    # Return 0 if all passed, 1 if any failed
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_all_tests())
