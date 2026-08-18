#!/usr/bin/env python3
"""Synthetic Auctioneer Data Generator.

All development and execution should be done through the repository Makefile.
Use `make generate` to build synthetic data and `make validate` to run the
validation harness. `make all` runs generate then validate.

This module provides the architecture for simulating a dynamic World of Warcraft
auction house economy and generating synthetic Auctioneer snapshot databases
(.db) with known ground-truth events (post, buy, expire, repost).

The resulting snapshots serve as ground truth test vectors to evaluate the
accuracy of inter-scan diff inference algorithms (such as AuctionScanDiff.py).

==============================================================================
OVERARCHING FLOW / CALL HIERARCHY
==============================================================================

CLI Entry Point:
  main()
    │
    ├── parse_args()                     [Parse CLI flags and execution mode]
    │
    └── run_generator(config)            [Coordinate simulation pipeline]
          │
          ├── seedMockDatabase()         [Initialize items, sellers, buyers, listings]
          │     ├── initializeActors()   [Construct rational Seller & Buyer agents]
          │     └── addListing()         [Populate initial market state]
          │
          ├── simulateMarket()           [Run discrete-event / time-step simulation]
          │     │
          │     └── For each time step / scheduled event:
          │           │
          │           ├── advanceSimulationTime()     [Update clock & listing ages]
          │           ├── expireListings()            [Drop expired listings to ground truth]
          │           │
          │           ├── executeSellerDecisions()    [Simulate seller posting & repricing]
          │           │     ├── maybeList()           [Logistic willingness-to-list curve]
          │           │     ├── calculateUndercutPrice() [Undercut 0.5%-2% or floor price]
          │           │     ├── addListing()          [Register new active listing]
          │           │     └── repostListing()       [Cancel older listing & post lower]
          │           │
          │           ├── executeBuyerDecisions()     [Simulate buyer purchasing behavior]
          │           │     ├── maybeBuy()            [Logistic willingness-to-buy curve]
          │           │     └── executeBuy()          [Consume cheapest available stacks]
          │           │
          │           └── If snapshot scheduled:      [Simulate periodic scan observation]
          │                 ├── calculateTimeLeftBucket() [Assign bucket 1..4 based on age]
          │                 └── takeSnapshot()        [Export active listings to SQLite .db]
          │
          └── recordGroundTruth()        [Persist immutable event history to JSON/DB]

==============================================================================
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ============================================================================
# Domain Models & Configurations
# ============================================================================


@dataclass
class SimulationConfig:
    """Configuration parameters controlling the mock market simulation."""

    days: float = 7.0
    seed: int | None = 42
    schedule_profile: str = "all"  # '7pm', 'evening_cluster', 'long_gaps', 'all'
    output_dir: Path = Path("./data")
    item_count: int = 15
    seller_count: int = 8
    buyer_count: int = 20
    market_price_volatility: float = 0.15
    enable_row_duplicates: bool = False
    noise_probability: float = 0.02


@dataclass
class ItemMarketProfile:
    """Represents the fundamental economic parameters of a tradable item."""

    item_id: int
    item_name: str
    golden_market_price: float  # In copper
    daily_demand_volume: int
    daily_supply_volume: int
    stack_size_options: list[int] = field(default_factory=lambda: [1, 5, 20])


@dataclass
class SellerActor:
    """Agent representing a rational, profit-seeking seller."""

    seller_id: str
    name: str
    floor_price_ratio: float = 0.70  # Min price relative to golden price
    desperation: float = 0.0  # 0.0 (patient) to 1.0 (liquidate immediately)
    undercut_min_pct: float = 0.005  # 0.5% min undercut
    undercut_max_pct: float = 0.020  # 2.0% max undercut
    default_duration_hours: float = 24.0


@dataclass
class BuyerActor:
    """Agent representing a consumer seeking lowest-cost supply."""

    buyer_id: str
    name: str
    demand_factor: float = 1.0  # Multiplier on purchase urgency
    max_price_ratio: float = 1.30  # Maximum willingness to pay relative to golden
    exact_quantity_preference: bool = False


@dataclass
class MockListing:
    """Represents a single active or historical auction listing."""

    listing_id: int
    item_id: int
    item_name: str
    seller_name: str
    stack_size: int
    buyout_price: int  # Total buyout in copper
    min_bid: int  # Total min bid in copper
    start_time: float  # Simulation epoch seconds
    duration_hours: float
    time_left_bucket: int = 4  # 1: 0-0.5h, 2: 0.5-2h, 3: 2-12h, 4: 12-48h
    status: str = "active"  # 'active', 'sold', 'expired', 'reposted', 'cancelled'


@dataclass
class MarketEvent:
    """Represents an immutable transaction or lifecycle event in the market."""

    event_id: int
    event_type: str  # 'post', 'buy', 'expire', 'repost', 'cancel', 'snapshot'
    timestamp: float
    listing_id: int
    item_id: int
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketState:
    """Container tracking the complete mutable state of the simulation."""

    current_time: float = 0.0
    active_listings: dict[int, MockListing] = field(default_factory=dict)
    all_listings: dict[int, MockListing] = field(default_factory=dict)
    event_history: list[MarketEvent] = field(default_factory=list)
    catalog: dict[int, ItemMarketProfile] = field(default_factory=dict)
    sellers: list[SellerActor] = field(default_factory=list)
    buyers: list[BuyerActor] = field(default_factory=list)
    snapshot_records: list[dict[str, Any]] = field(default_factory=list)


# ============================================================================
# Abstract Architecture Functions (Stubs)
# ============================================================================


def seedMockDatabase(
    config: SimulationConfig,
    catalog: list[ItemMarketProfile] | None = None,
) -> MarketState:
    """Initialize the mock market state with items, actors, and baseline listings.

    Args:
        config: Simulation configuration options.
        catalog: Optional custom catalog of items. If None, default items are seeded.

    Returns:
        MarketState containing initialized actors, catalog, and active listings.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    random.seed(config.seed)
    sellers, buyers = initializeActors(config)
    state = MarketState(current_time=0.0, sellers=sellers, buyers=buyers)

    if catalog is None:
        catalog = [
            ItemMarketProfile(
                item_id=101 + i,
                item_name=f"Item{i + 1}",
                golden_market_price=float(800 + (i * 150) % 1200),
                daily_demand_volume=40 + (i * 5),
                daily_supply_volume=45 + (i * 3),
            )
            for i in range(config.item_count)
        ]
    state.catalog = {item.item_id: item for item in catalog}

    for seller in sellers:
        item = catalog[len(state.all_listings) % len(catalog)]
        unit_price = int(item.golden_market_price * random.uniform(0.9, 1.1))
        stack_size = random.choice(item.stack_size_options)
        buyout_price = unit_price * stack_size
        listing_id = len(state.all_listings) + 1
        listing = MockListing(
            listing_id=listing_id,
            item_id=item.item_id,
            item_name=item.item_name,
            seller_name=seller.name,
            stack_size=stack_size,
            buyout_price=buyout_price,
            min_bid=int(buyout_price * 0.9),
            start_time=0.0,
            duration_hours=seller.default_duration_hours,
            time_left_bucket=calculateTimeLeftBucket(0.0, seller.default_duration_hours, 0.0),
        )
        addListing(state, listing)
    return state


def initializeActors(
    config: SimulationConfig,
) -> tuple[list[SellerActor], list[BuyerActor]]:
    """Instantiate seller and buyer agents with varied pricing parameters.

    Args:
        config: Simulation configuration options.

    Returns:
        A tuple of (seller_actors, buyer_actors).

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    sellers = [
        SellerActor(
            seller_id=f"s{i + 1}",
            name=f"Seller{i + 1}",
            floor_price_ratio=0.70 + (i % 3) * 0.05,
            desperation=min(1.0, (i / max(1, config.seller_count - 1)) * 0.5),
        )
        for i in range(config.seller_count)
    ]
    buyers = [
        BuyerActor(
            buyer_id=f"b{i + 1}",
            name=f"Buyer{i + 1}",
            demand_factor=1.0 + (i % 2) * 0.1,
            max_price_ratio=1.10 + (i % 3) * 0.05,
        )
        for i in range(config.buyer_count)
    ]
    return sellers, buyers


def addListing(market_state: MarketState, listing: MockListing) -> None:
    """Register a new listing in the active market and record a 'post' event.

    Args:
        market_state: The current simulation market state.
        listing: The MockListing to add.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    market_state.active_listings[listing.listing_id] = listing
    market_state.all_listings[listing.listing_id] = listing
    unit_price = listing.buyout_price / listing.stack_size if listing.stack_size else 0.0
    event = MarketEvent(
        event_id=len(market_state.event_history) + 1,
        event_type="post",
        timestamp=listing.start_time,
        listing_id=listing.listing_id,
        item_id=listing.item_id,
        details={
            "seller_name": listing.seller_name,
            "item_name": listing.item_name,
            "stack_size": listing.stack_size,
            "unit_price": unit_price,
            "buyout_price": listing.buyout_price,
            "min_bid": listing.min_bid,
            "duration_hours": listing.duration_hours,
        },
    )
    market_state.event_history.append(event)


def maybeList(
    seller: SellerActor,
    item: ItemMarketProfile,
    current_listings: list[MockListing],
) -> bool:
    """Evaluate whether a seller decides to post a listing given market conditions.

    Uses a logistic probability curve based on current lowest competitor price
    versus the item's golden market price, modified by seller desperation.

    Args:
        seller: The seller evaluating the decision.
        item: The item under consideration.
        current_listings: Currently active listings for this item.

    Returns:
        True if the seller decides to list; False otherwise.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    if not current_listings:
        return True
    lowest_unit_price = min(
        listing.buyout_price / listing.stack_size for listing in current_listings
    )
    threshold = item.golden_market_price * (1.0 - seller.desperation * 0.2)
    return random.random() < 0.5 or lowest_unit_price > threshold


def calculateUndercutPrice(
    item: ItemMarketProfile,
    lowest_competing_price: float,
    seller: SellerActor,
) -> float:
    """Calculate the posting price for an item, applying undercutting logic.

    Applies a random undercut between 0.5% and 2.0% unless constrained by the
    seller's floor price ratio. If no competition exists, posts at a markup
    (e.g., +50% to +100% over golden price).

    Args:
        item: The item profile containing golden market price.
        lowest_competing_price: Lowest current unit buyout price, or 0.0 if none.
        seller: The seller actor whose floor price applies.

    Returns:
        Calculated unit price in copper.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    floor_price = item.golden_market_price * seller.floor_price_ratio
    if lowest_competing_price <= 0.0:
        return max(item.golden_market_price * 1.5, floor_price)
    undercut_pct = random.uniform(seller.undercut_min_pct, seller.undercut_max_pct)
    price = lowest_competing_price * (1.0 - undercut_pct)
    return max(price, floor_price)


def maybeBuy(
    buyer: BuyerActor,
    item: ItemMarketProfile,
    current_listings: list[MockListing],
) -> bool:
    """Evaluate whether a buyer decides to execute a purchase.

    Uses a logistic probability curve comparing the cheapest available unit
    price against the item's golden market price and buyer demand factor.

    Args:
        buyer: The buyer evaluating the decision.
        item: The item profile being considered.
        current_listings: Currently active listings for this item.

    Returns:
        True if the buyer decides to proceed with a purchase; False otherwise.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    if not current_listings:
        return False
    cheapest_unit_price = min(
        listing.buyout_price / listing.stack_size for listing in current_listings
    )
    threshold = item.golden_market_price * buyer.max_price_ratio * buyer.demand_factor
    return cheapest_unit_price <= threshold


def executeBuy(
    market_state: MarketState,
    buyer: BuyerActor,
    item_id: int,
    target_quantity: int,
) -> list[MarketEvent]:
    """Execute a purchase against the cheapest available active listings.

    Handles whole-listing consumption (buyers must buy the entire listing in an
    all-or-nothing transaction), removes purchased listings from market_state,
    and records 'buy' events.

    Args:
        market_state: The current simulation market state.
        buyer: The buyer actor making the purchase.
        item_id: The ID of the item to buy.
        target_quantity: The target number of units to acquire.

    Returns:
        List of generated MarketEvent objects representing the purchases.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    candidates = [
        listing for listing in market_state.active_listings.values() if listing.item_id == item_id
    ]
    candidates.sort(key=lambda listing: listing.buyout_price / listing.stack_size)
    remaining = target_quantity
    events: list[MarketEvent] = []
    for listing in candidates:
        if remaining <= 0:
            break
        # Buyers cannot buy partial listings: a listing of any size (e.g., 13) is bought entirely
        quantity = listing.stack_size
        unit_price = listing.buyout_price / listing.stack_size
        event = MarketEvent(
            event_id=len(market_state.event_history) + 1,
            event_type="buy",
            timestamp=market_state.current_time,
            listing_id=listing.listing_id,
            item_id=listing.item_id,
            details={
                "buyer_name": buyer.name,
                "quantity": quantity,
                "unit_price": unit_price,
            },
        )
        market_state.event_history.append(event)
        events.append(event)
        remaining -= quantity
        listing.status = "sold"
        market_state.active_listings.pop(listing.listing_id, None)
    return events


def repostListing(
    market_state: MarketState,
    seller: SellerActor,
    old_listing: MockListing,
    new_unit_price: float,
) -> tuple[MarketEvent, MockListing]:
    """Cancel an active listing and replace it with a lower-priced repost.

    Args:
        market_state: The current simulation market state.
        seller: The seller performing the repost.
        old_listing: The existing listing to be replaced.
        new_unit_price: The new lower unit price in copper.

    Returns:
        A tuple of (repost_event, new_mock_listing).

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    market_state.active_listings.pop(old_listing.listing_id, None)
    market_state.all_listings.setdefault(old_listing.listing_id, old_listing)
    old_listing.status = "reposted"
    next_id = max(list(market_state.all_listings.keys()) + [old_listing.listing_id]) + 1
    new_buyout = int(new_unit_price * old_listing.stack_size)
    new_listing = MockListing(
        listing_id=next_id,
        item_id=old_listing.item_id,
        item_name=old_listing.item_name,
        seller_name=old_listing.seller_name,
        stack_size=old_listing.stack_size,
        buyout_price=new_buyout,
        min_bid=int(new_buyout * 0.9),
        start_time=market_state.current_time,
        duration_hours=old_listing.duration_hours,
        time_left_bucket=calculateTimeLeftBucket(
            market_state.current_time,
            old_listing.duration_hours,
            market_state.current_time,
        ),
        status="active",
    )
    market_state.active_listings[next_id] = new_listing
    market_state.all_listings[next_id] = new_listing
    event = MarketEvent(
        event_id=len(market_state.event_history) + 1,
        event_type="repost",
        timestamp=market_state.current_time,
        listing_id=new_listing.listing_id,
        item_id=new_listing.item_id,
        details={
            "old_listing_id": old_listing.listing_id,
            "seller_name": old_listing.seller_name,
            "item_name": old_listing.item_name,
            "stack_size": old_listing.stack_size,
            "old_unit_price": (
                old_listing.buyout_price / old_listing.stack_size if old_listing.stack_size else 0.0
            ),
            "new_unit_price": new_unit_price,
            "new_buyout": new_buyout,
        },
    )
    market_state.event_history.append(event)
    return event, new_listing


def expireListings(market_state: MarketState, current_time: float) -> list[MarketEvent]:
    """Identify and remove all listings whose duration has lapsed.

    Args:
        market_state: The current simulation market state.
        current_time: Current simulation epoch time in seconds.

    Returns:
        List of MarketEvent objects for all expired listings.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    events: list[MarketEvent] = []
    expired_ids: list[int] = []
    for listing in list(market_state.active_listings.values()):
        expiry_time = listing.start_time + listing.duration_hours * 3600.0
        if current_time >= expiry_time:
            listing.status = "expired"
            expired_ids.append(listing.listing_id)
            event = MarketEvent(
                event_id=len(market_state.event_history) + 1,
                event_type="expire",
                timestamp=current_time,
                listing_id=listing.listing_id,
                item_id=listing.item_id,
                details={
                    "seller_name": listing.seller_name,
                    "item_name": listing.item_name,
                    "stack_size": listing.stack_size,
                    "unit_price": (
                        listing.buyout_price / listing.stack_size if listing.stack_size else 0.0
                    ),
                    "buyout_price": listing.buyout_price,
                },
            )
            market_state.event_history.append(event)
            events.append(event)
    for listing_id in expired_ids:
        market_state.active_listings.pop(listing_id, None)
    return events


def advanceSimulationTime(market_state: MarketState, target_time: float) -> None:
    """Advance the simulation clock to target_time and update listing time-left buckets.

    Args:
        market_state: The simulation state to update.
        target_time: New target epoch time in seconds.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    market_state.current_time = target_time
    for listing in market_state.active_listings.values():
        listing.time_left_bucket = calculateTimeLeftBucket(
            listing.start_time,
            listing.duration_hours,
            target_time,
        )


def calculateTimeLeftBucket(
    listing_start_time: float,
    duration_hours: float,
    current_time: float,
) -> int:
    """Map remaining auction duration to Auctioneer time-left bucket code.

    Codes:
      1: Short (0 - 0.5 hours remaining)
      2: Medium (0.5 - 2.0 hours remaining)
      3: Long (2.0 - 12.0 hours remaining)
      4: Very Long (12.0 - 48.0 hours remaining)

    Args:
        listing_start_time: Start timestamp in seconds.
        duration_hours: Total initial duration in hours.
        current_time: Current timestamp in seconds.

    Returns:
        Integer bucket code (1..4).

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    elapsed = max(0.0, current_time - listing_start_time)
    remaining_hours = max(0.0, duration_hours - elapsed / 3600.0)
    if remaining_hours <= 0.5:
        return 1
    if remaining_hours <= 2.0:
        return 2
    if remaining_hours <= 12.0:
        return 3
    return 4


def takeSnapshot(
    market_state: MarketState,
    output_path: str | Path,
    snapshot_id: str,
    row_duplicates: bool = False,
) -> Path:
    """Export current active listings into an Auctioneer-compatible SQLite database.

    Creates the `auctionListings` table adhering to the standard schema with
    columns (server, faction, itemId, itemName, stackSize, buyoutPrice, minBid,
    timeLeft, seenTime, sellerName, etc.).

    Args:
        market_state: Current state containing active listings.
        output_path: Target SQLite file path (e.g., ./data/scan_01.db).
        snapshot_id: Identifier label for this snapshot.
        row_duplicates: If True, inject duplicate rows to simulate scanner noise.

    Returns:
        Path to the written SQLite database.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    conn = sqlite3.connect(str(output_path))
    try:
        cursor = conn.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS auctionListings ("
            "id INTEGER PRIMARY KEY, server TEXT, faction TEXT, itemId INTEGER, "
            "itemName TEXT, stackSize INTEGER, buyoutPrice INTEGER, minBid INTEGER, "
            "timeLeft INTEGER, seenTime REAL, sellerName TEXT, snapshotId TEXT"
            ")"
        )
        insert_sql = (
            "INSERT INTO auctionListings (server, faction, itemId, itemName, "
            "stackSize, buyoutPrice, minBid, timeLeft, seenTime, sellerName, "
            "snapshotId) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        for listing in market_state.active_listings.values():
            cursor.execute(
                insert_sql,
                (
                    "MockRealm",
                    "Horde",
                    listing.item_id,
                    listing.item_name,
                    listing.stack_size,
                    listing.buyout_price,
                    listing.min_bid,
                    listing.time_left_bucket,
                    market_state.current_time,
                    listing.seller_name,
                    snapshot_id,
                ),
            )
            if row_duplicates and random.random() < 0.15:
                for _ in range(random.randint(1, 3)):
                    cursor.execute(
                        insert_sql,
                        (
                            "MockRealm",
                            "Horde",
                            listing.item_id,
                            listing.item_name,
                            listing.stack_size,
                            listing.buyout_price,
                            listing.min_bid,
                            listing.time_left_bucket,
                            market_state.current_time,
                            listing.seller_name,
                            snapshot_id,
                        ),
                    )
        conn.commit()
    finally:
        conn.close()
    return output_path


def recordGroundTruth(
    market_state: MarketState,
    output_path: str | Path,
) -> Path:
    """Serialize complete simulation event history and listing ground truths.

    Args:
        market_state: Simulation state containing all historical events.
        output_path: Destination JSON/DB file path.

    Returns:
        Path to the written ground truth artifact.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    event_list = []
    for event in market_state.event_history:
        event_list.append(
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "timestamp": event.timestamp,
                "listing_id": event.listing_id,
                "item_id": event.item_id,
                "details": event.details,
            }
        )
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump({"event_history": event_list}, handle, indent=2)
    return output_path


def simulateMarket(
    config: SimulationConfig,
    market_state: MarketState,
) -> MarketState:
    """Execute the core discrete-event simulation loop over the configured timespan.

    Coordinates periodic seller posts, undercuts, buyer purchases, natural
    expirations, and snapshot generation according to the chosen schedule profile.

    Args:
        config: Simulation parameters and profile settings.
        market_state: Initialized market state.

    Returns:
        Completed MarketState after all virtual time has elapsed.

    Raises:
        NotImplementedError: Stubbed for architectural design.
    """
    total_seconds = config.days * 24 * 3600
    steps = int(config.days * 6)
    steps = max(steps, 2)
    step_seconds = total_seconds / steps
    snapshot_index = 1
    for step in range(steps + 1):
        current_time = step * step_seconds
        advanceSimulationTime(market_state, current_time)
        expireListings(market_state, current_time)
        for item in market_state.catalog.values():
            current_listings = [
                listing
                for listing in market_state.active_listings.values()
                if listing.item_id == item.item_id
            ]
            for seller in market_state.sellers:
                seller_active = [l for l in current_listings if l.seller_name == seller.name]
                lowest_price = min(
                    (l.buyout_price / l.stack_size for l in current_listings),
                    default=0.0,
                )
                reposted = False
                if seller_active and lowest_price > 0:
                    old_listing = seller_active[0]
                    old_unit_price = old_listing.buyout_price / old_listing.stack_size
                    if lowest_price < old_unit_price and random.random() < (
                        0.15 + seller.desperation * 0.35
                    ):
                        new_unit_price = calculateUndercutPrice(item, lowest_price, seller)
                        if new_unit_price < old_unit_price:
                            repostListing(market_state, seller, old_listing, new_unit_price)
                            reposted = True
                if not reposted and maybeList(seller, item, current_listings):
                    unit_price = calculateUndercutPrice(
                        item,
                        (
                            min(
                                (listing.buyout_price / listing.stack_size)
                                for listing in current_listings
                            )
                            if current_listings
                            else 0.0
                        ),
                        seller,
                    )
                    stack_size = random.choice(item.stack_size_options)
                    listing_id = len(market_state.all_listings) + 1
                    listing = MockListing(
                        listing_id=listing_id,
                        item_id=item.item_id,
                        item_name=item.item_name,
                        seller_name=seller.name,
                        stack_size=stack_size,
                        buyout_price=int(unit_price * stack_size),
                        min_bid=int(unit_price * stack_size * 0.9),
                        start_time=current_time,
                        duration_hours=seller.default_duration_hours,
                        time_left_bucket=calculateTimeLeftBucket(
                            current_time, seller.default_duration_hours, current_time
                        ),
                    )
                    addListing(market_state, listing)
        for buyer in market_state.buyers:
            for item in market_state.catalog.values():
                current_listings = [
                    listing
                    for listing in market_state.active_listings.values()
                    if listing.item_id == item.item_id
                ]
                if maybeBuy(buyer, item, current_listings):
                    target_quantity = random.randint(1, 5)
                    executeBuy(market_state, buyer, item.item_id, target_quantity)
        snapshot_path = config.output_dir / f"scan_{snapshot_index:03}.db"
        takeSnapshot(
            market_state,
            snapshot_path,
            f"scan_{snapshot_index:03}",
            row_duplicates=config.enable_row_duplicates,
        )
        snapshot_index += 1
    return market_state


def run_generator(config: SimulationConfig) -> None:
    """High-level pipeline coordinator for mock data generation.

    Args:
        config: Simulation configuration.
    """
    market_state = seedMockDatabase(config)
    market_state = simulateMarket(config, market_state)
    recordGroundTruth(market_state, config.output_dir / "ground_truth.json")


# ============================================================================
# CLI & Entry Point
# ============================================================================


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for synthetic database generation.

    Args:
        args: Optional command-line argument list.

    Returns:
        Parsed argparse.Namespace.
    """
    parser = argparse.ArgumentParser(
        description="Generate synthetic Auctioneer SQLite snapshots and ground truth data.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--days",
        type=float,
        default=7.0,
        help="Virtual days of auction house activity to simulate.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible market simulations.",
    )
    parser.add_argument(
        "--schedule",
        choices=["7pm", "evening_cluster", "long_gaps", "all"],
        default="all",
        help=(
            "Snapshot schedule profile (e.g. daily 7pm, clustered evening "
            "scans, long gaps, or all)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./data"),
        help="Directory where generated snapshot DBs and ground truth will be stored.",
    )
    parser.add_argument(
        "--items",
        type=int,
        default=15,
        dest="item_count",
        help="Number of distinct item commodities in the market.",
    )
    parser.add_argument(
        "--sellers",
        type=int,
        default=8,
        dest="seller_count",
        help="Number of synthetic seller agents.",
    )
    parser.add_argument(
        "--buyers",
        type=int,
        default=20,
        dest="buyer_count",
        help="Number of synthetic buyer agents.",
    )
    parser.add_argument(
        "--enable-row-duplicates",
        action="store_true",
        help="Inject duplicate listing rows into snapshots to simulate scanner noise.",
    )
    return parser.parse_args(args)


def main(args: Sequence[str] | None = None) -> int:
    """Main CLI entry point invoking the stubbed architecture functions.

    Args:
        args: Optional CLI arguments.

    Returns:
        Exit code (0 for success).
    """
    parsed = parse_args(args)
    config = SimulationConfig(
        days=parsed.days,
        seed=parsed.seed,
        schedule_profile=parsed.schedule,
        output_dir=parsed.output_dir,
        item_count=parsed.item_count,
        seller_count=parsed.seller_count,
        buyer_count=parsed.buyer_count,
        enable_row_duplicates=parsed.enable_row_duplicates,
    )

    print(f"[*] Starting Mock DB Generator with schedule profile: '{config.schedule_profile}'")
    print(f"[*] Target output directory: {config.output_dir}")

    # Demonstrate architectural call graph (unimplemented stubs raise NotImplementedError)
    try:
        run_generator(config)
    except NotImplementedError as exc:
        print(f"[!] Architecture stub encountered: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
