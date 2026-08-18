# AdvancedMultiScanLogic

## Expiration Tracking and Matching

This guide describes an advanced matching technique for Auctioneer scan diffs that treats each listing as an internally tracked object, even though the WoW API does not expose a stable listing ID. The technique is called **Expiration Tracking and Matching**.

It is designed to let `AuctionScanDiff.py` maintain a persistent notion of a listing identity across sequential scans, and to track both posting and expiration uncertainty using time ranges.

---

## Why this matters

The native Auctioneer snapshot data only gives us observed rows. Rows are not stable across scans because auctions can change their `timeLeft` bucket, the same seller can post multiple same-price stacks, and the underlying export rows are not unique.

A multi-scan matching layer gives us:

- a consistent internal `unique_id` for each inferred listing,
- narrow possible expiration ranges with each subsequent scan,
- stronger evidence to tell `sold` vs `expired` vs `removed` when a listing vanishes,
- stable tracking of listing continuity even when exact row-level fields fluctuate.

This technique is especially useful when scans are taken frequently, because small changes in a listing's reported time window can dramatically tighten the inferred lifetime.

---

## Core concepts

### Tracked listing state

A `TrackedListing` is the object we maintain for each inferred auction listing. It is built from one or more observed rows, and it keeps the current best estimate for the listing's life cycle.

Key fields to add or derive:

- `unique_id`: internal ID assigned when a listing is first recognized.
- `earliestPossiblePostingTime`: earliest time the listing could have been posted.
- `latestPossiblePostingTime`: latest time the listing could have been posted.
- `earliestPossibleExpirationTime`: earliest time the listing could expire or disappear.
- `latestPossibleExpirationTime`: latest time the listing could expire or disappear.
- `first_seen_time`: first scan observation timestamp.
- `last_seen_time`: last scan observation timestamp.
- `match_key` / `identity_key`: stable signature fields used to compare listings.
- `observed_count`: how many scan rows have been matched into this tracked listing.

### Temporary scan candidates

Each listing row from a new scan is first converted into a scan-time candidate. The candidate includes observed fields plus its own inferred expiration range based on the current scan time and the reported `timeLeft` bucket.

For example:

```python
@dataclass
class ScanCandidate:
    listing: Listing
    scan_time: int
    earliest_expiration: int
    latest_expiration: int
    matched_prior_id: Optional[int] = None
```

### Matching rule

Each new scan candidate is matched against prior tracked listings with these conditions:

1. Same seller identity, same stack size, and other immutible attributes.
2. Same observed price fields (buyout price, min bid, or current bid) when available.
3. A valid overlap between the prior listing's current expiration range and the candidate's expiration range.
4. One-to-one matching: once a prior tracked listing is matched, it is not reused for another candidate in the same scan.

This match is intentionally narrower than a pure count-based diff. It preserves real continuity only when both identity and temporal evidence support it.

---

## Data structure changes

### 1. Extend listing model

Add an enriched representation for tracked listings.

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class TrackedListing:
    unique_id: int
    listing: Listing
    first_seen_time: int
    last_seen_time: int
    earliestPossiblePostingTime: int
    latestPossiblePostingTime: int
    earliestPossibleExpirationTime: int
    latestPossibleExpirationTime: int
    matched_scan_count: int = 1
```

If the existing `Listing` dataclass is kept as a raw row wrapper, `TrackedListing` should wrap it and add metadata.

### 2. Add candidate representation

A scan-time candidate is a lightweight object used while evaluating a new scan.

```python
@dataclass
class ScanCandidate:
    listing: Listing
    scan_time: int
    earliest_expiration: int
    latest_expiration: int
    match_key: Tuple[Any, ...]
    identity_key: Tuple[Any, ...]
    matched_prior_id: Optional[int] = None
```

### 3. Maintain match indexes

Use maps keyed by stable identity values to speed up matching:

- `tracked_by_match_key: Dict[Tuple[Any, ...], List[TrackedListing]]`
- `tracked_by_identity_key: Dict[Tuple[Any, ...], List[TrackedListing]]`

The first index is useful for exact listing continuity. The second index is useful when a listing's price or buckets shift while the seller/item identity remains the same.

### 4. Add a `ListingState` store

This can be an in-memory structure for a single run, or a persistent state table in `sales.db` or a separate tracking database.

For persistent tracking, a simple schema could include:

- `unique_id INTEGER PRIMARY KEY`
- `seller TEXT`
- `itemId INTEGER`
- `stackSize INTEGER`
- `buyoutPrice INTEGER`
- `first_seen_time INTEGER`
- `last_seen_time INTEGER`
- `earliest_posting INTEGER`
- `latest_posting INTEGER`
- `earliest_expiration INTEGER`
- `latest_expiration INTEGER`
- `status TEXT`
- `row_json TEXT`

Persisting this state makes it easy to replay many scans and keep a canonical listing collection over longer periods.

---

## Functional changes needed

### 1. Compute possible expiration bounds

Add a helper that converts a scan row into an expiration interval.

```python
def compute_expiration_range(scan_time: int, time_left_code: int, buckets: Mapping[int, Tuple[float, float]]) -> Tuple[int, int]:
    minimum_hours, maximum_hours = buckets[time_left_code]
    return (
        int(scan_time + minimum_hours * 3600),
        int(scan_time + maximum_hours * 3600),
    )
```

For bucket code 4 at 4:00pm, this yields `4:00am tomorrow` to `4:00pm + 48h`.

### 2. Create candidate listings from each scan row

When processing a scan, convert rows into `ScanCandidate`s with their candidate expiration range.

```python
candidate = ScanCandidate(
    listing=row_listing,
    scan_time=current_scan_time,
    earliest_expiration=earliest_expiration,
    latest_expiration=latest_expiration,
    match_key=row_listing.match_key(),
    identity_key=row_listing.identity_key(),
)
```

### 3. Match new scan candidates against prior tracked listings

Use a matching function that returns the best prior tracked listing for each candidate.

```python
def find_match(candidate: ScanCandidate, priors: Sequence[TrackedListing]) -> Optional[TrackedListing]:
    for prior in priors:
        if prior.unique_id in matched_prior_ids:
            continue
        if not ranges_overlap(prior.earliestPossibleExpirationTime, prior.latestPossibleExpirationTime,
                               candidate.earliest_expiration, candidate.latest_expiration):
            continue
        if not same_listing_identity(prior.listing, candidate.listing):
            continue
        return prior
    return None
```

Prefer exact price/quantity matches first, then relax into identity matches if duplicates are present.

### 4. Update tracked intervals after a match

When a candidate matches a prior tracked listing, intersect the prior estimates with the new evidence.

```python
prior.earliestPossibleExpirationTime = max(
    prior.earliestPossibleExpirationTime,
    candidate.earliest_expiration,
)
prior.latestPossibleExpirationTime = min(
    prior.latestPossibleExpirationTime,
    candidate.latest_expiration,
)
prior.last_seen_time = candidate.scan_time
prior.matched_scan_count += 1
```

If the candidate has a narrower bucket than the prior observation, that new information will tighten the range.

### 5. Create new tracked listings for unmatched candidates

If a candidate does not match any prior tracked listing, it becomes a new listing.

```python
new_listing = TrackedListing(
    unique_id=next_unique_id(),
    listing=candidate.listing,
    first_seen_time=candidate.scan_time,
    last_seen_time=candidate.scan_time,
    earliestPossiblePostingTime=candidate.scan_time - max_bucket_seconds,
    latestPossiblePostingTime=candidate.scan_time,
    earliestPossibleExpirationTime=candidate.earliest_expiration,
    latestPossibleExpirationTime=candidate.latest_expiration,
)
```

The new listing's posting window reflects the fact that it must have been posted sometime before the scan, and the expiration window is derived from the scan's `timeLeft` bucket.

### 6. Classify unmatched prior listings

Any prior tracked listing that is not matched by a current scan candidate is a disappearance event for the current scan interval.

Use the prior listing's existing expiration range plus the current scan time to infer an outcome.

```python
if current_scan_time > prior.latestPossibleExpirationTime:
    status = "expired"
elif current_scan_time < prior.earliestPossibleExpirationTime:
    status = "sold"
else:
    status = "missing"
```

This leverages the temporal uncertainty range directly instead of only relying on static bucket thresholds.

### 7. Prevent double-matching

A key functional detail is that matching is one-to-one per scan. Once a prior tracked listing is paired with a candidate, it must be excluded from further matches in that scan.

This is what makes the concept of an internal `unique_id` useful: it means we can say, "candidate A matched tracked listing #42," and we can avoid pairing another candidate with #42.

### 8. Track scan series rather than only snapshot pairs

`AuctionScanDiff.py` already has support for loading multiple scan snapshots sequentially, but this technique extends that behavior into a persistent tracked-listing lifecycle.

To support full expiration tracking, the logic should:

- load all snapshots in chronological order by their detected scan time,
- maintain a live set of tracked listings across scans,
- update each tracked listing as new scans arrive,
- report disappearance outcomes when prior tracked listings are not matched in a later scan.

This is the architectural shift from discrete snapshot comparison to continuous listing tracking.

---

## Example behavior across scans

### Scan 1 at 16:00

Observed:

- Linen Cloth, qty 20, buyout 20s, remaining 12-48h
- Linen Cloth, qty 20, buyout 20s, remaining 12-48h
- Linen Cloth, qty 5, buyout 5s, remaining 12-48h

New tracked listings are created for all three. Their expiration ranges are:

- `20s / qty 20`: earliest 04:00 tomorrow, latest 16:00 two days later
- `20s / qty 20`: same as above
- `5s / qty 5`: same as above

### Scan 2 at 16:32

Observed:

- Linen Cloth, qty 20, buyout 20s, remaining 2-12h
- Linen Cloth, qty 20, buyout 20s, remaining 12-48h
- Linen Cloth, qty 5, buyout 5s, remaining 12-48h

Candidate ranges are:

- candidate 1: earliest 04:32 tomorrow, latest 18:32 today
- candidate 2: earliest 04:32 two days later, latest 04:32 tomorrow
- candidate 3: earliest 04:32 two days later, latest 04:32 tomorrow

Matching proceeds:

- candidate 1 matches one prior `20s / qty 20` listing because the ranges overlap and the price/stack/seller identity matches.
- candidate 2 matches the other prior `20s / qty 20` listing.
- candidate 3 matches the prior `5s / qty 5` listing.

The first prior listing's expiration range narrows to the intersection of the two observations, producing a much tighter window.

### Scan 3 at 16:53

Observed:

- Linen Cloth, qty 20, buyout 20s, remaining 12-48h
- Linen Cloth, qty 5, buyout 5s, remaining 12-48h

One `20s / qty 20` tracked listing is not matched in this scan. Because the scan time is outside its prior expected expiration range, we can infer it was delisted or sold.

The remaining tracked listings stay alive and continue to have their intervals updated.

---

## Guiding implementation changes

### New helpers to add

- `build_scan_candidates(listings, scan_time, buckets)`
- `find_best_prior_match(candidate, tracked_listings)`
- `ranges_overlap(start1, end1, start2, end2)`
- `intersect_range(existing_start, existing_end, new_start, new_end)`
- `create_tracked_listing(candidate, unique_id, buckets)`
- `classify_prior_disappearance(prior, current_scan_time)`

### Existing structures to modify

- `Listing`: extend or wrap it with a tracked metadata container.
- `SnapshotInfo`: if using multi-scan series, keep a `status` or `tracked_listings` field.
- `DiffResult`: add fields for `unique_id`, `earliestPossibleExpirationTime`, and `latestPossibleExpirationTime` if you want them in output.

### New structures to create

- `TrackedListing`
- `ScanCandidate`
- persistent tracking state or table in sales DB for `listing_ids`
- optional `MatchResult` or `UpdateEvent` object to store the scan-by-scan decision.

### Major functional changes

1. Use the existing multi-scan load behavior as the default path and process scans in chronological order.
2. Introduce a stable internal listing identity for each inferred tracked listing.
3. Compute expiration intervals from `timeLeft` buckets on each scan.
4. Match new scan rows to prior tracked listings one-to-one.
5. Update posting and expiry ranges by intersecting new evidence.
6. Treat unmatched prior listings as disappearance events rather than assuming every missing row is a new sale/expiry.
7. Persist or cache the tracked listing collection so it can survive more than two scans in a single run.

---

## Practical notes

- Use the existing `IGNORE_MATCH_FIELDS` set as a starting point for stable-field matching, but the new model should explicitly choose which fields are part of identity matching and which are just evidence.
- If two identical stacks appear in the same scan, the new matching technique should still allow them to become two separate tracked listings, provided they are matched one-to-one across scans.
- If the current scan contains fewer copies than the prior scan, the unmatched prior copies are the candidates for disappearance classification.
- If a prior tracked listing disappears and a new candidate appears with the same seller, same quantity, and same price, do not immediately assume the same listing continued; only continue it if the time ranges are consistent and the prior listing is currently unmatched.
- The most robust implementation will use a greedy or weighted matching step that prefers the best candidate-prior pair first, then falls back to less exact matches.

---

## Summary

**Expiration Tracking and Matching** turns the problem of anonymous auction rows into a time-aware tracking problem.

Instead of only asking whether a listing observed in one scan is missing in the next scan, it asks:

- "which prior listing is this new row most likely continuing?"
- "how narrow is the listing's expiration window after this scan?"
- "what is the best internal ID to assign to this inferred auction listing?"

Supporting this technique in `AuctionScanDiff.py` requires new tracked-state structures, candidate matching, interval intersections, and a scan-series workflow. The payoff is more confident classification of disappearance events and a richer inferred history for each tracked auction.
