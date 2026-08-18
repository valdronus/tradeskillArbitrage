# Inter-Scan Matching Discussions

This document captures the current reasoning, validation goals, and future testing ideas for the Auctioneer scan diff / inferred sales pipeline.

## Purpose

The goal is to document how sequential Auctioneer scans are compared, what assumptions the inference logic currently makes, where those assumptions are fragile, and how to validate the outcome of inferred sold/expired/repost classification.

This is not a specification of any one script. It is a discussion note for:

- `AuctionScanDiff.py` — which diffs two scan snapshots and writes `sales.db`
- `trace_item_sales.py` — which inspects item traces across scan DBs and `sales.db`
- `analyze_sales_db.py` — which summarizes inferred outcomes from `sales.db`

## Key terms

- **Snapshot**: a single Auctioneer SQLite export containing `auctionListings` rows.
- **Listing row**: one row in `auctionListings`, representing one observed auction listing at scan time.
- **Match key**: the hashable signature used to decide whether a row in one scan corresponds to a row in another scan.
- **Identity key**: a more stable signature used to compare an item across scans by seller/item/stack identity rather than row-level fields.
- **Time-left bucket**: Auctioneer's reported lifetime code for a listing, typically 1..4, representing 0–0.5h, 0.5–2h, 2–12h, and 12–48h.
- **Gray window**: the interval between the minimum and maximum bucket duration, where a missing listing can plausibly have either sold or expired.
- **Repost**: a listing by the same seller, same item, lower unit price appearing in the later scan.

## Current inference flow

The existing pipeline generally does the following:

1. Load two successive scan DBs.
2. Compare `auctionListings` rows while ignoring known unstable fields such as `id`, `ropeId`, `seenTime`, and `timeLeft`.
3. Consider a row missing if its match-key signature is present in the first scan but not enough times in the second scan.
4. Classify missing rows by interval:
   - `likely_sold` if the later scan arrived before the bucket's minimum expiry time.
   - `likely_expired` if the later scan arrived after the bucket's maximum expiry time.
   - `missing` if the elapsed interval lies in the gray window.
5. Within the gray window, estimate a sold likelihood with a heuristic that blends:
   - timing proximity to the start/end of the bucket,
   - relative price among surviving same-item peers,
   - continuity evidence from cheaper surviving peers.
6. Flag a missing listing as `repost` when a same-seller same-item listing remains in scan two at a lower unit price.
7. Persist the inferred results into `sales.db` and use downstream analysis scripts to summarize counts, averages, and market behavior.

## Recent diagnosis and fragile assumptions

The most important recent finding is that row-level duplicates within a single scan can drastically distort inferred totals.

### Duplicate/identical listing inflation

- Some scan DBs contain many repeated rows with identical observable fields.
- If the diff logic treats each row as an independent listing, identical duplicates can create artificial volume and inflate the number of inferred missing rows.
- The trace output showed the same item listing repeated hundreds of times in the same scan.
- This makes `sales.db` counts unreliable unless duplicate grouping is handled explicitly.

### Unstable fields and noisy matching

- The current match key ignores `seenTime`, but the same listing can still appear as separate rows due to scan revisit behavior.
- `timeLeft` is also unstable across scans: a listing that persists will often move to a lower bucket in a later scan.
- A naive exact match by stable fields may fail when the same seller-item listing transitions time-left buckets or is split into duplicate rows.

### Count vs. identity

- There are two separate concepts:
  - whether a listing exists at all in the later scan, and
  - how many copies of that signature exist.
- The core algorithm should probably track grouped counts for identical listings rather than raw row counts.
- However, this grouping introduces complexity because the same seller/item may also legitimately have multiple independent stacks at the same price.

### Repost detection

- Repost inference is currently based on the same seller and item identity with a lower unit price in the later scan.
- That logic is useful, but it can also be triggered incorrectly when multiple independent listings exist for the same seller/item.
- Any future count or grouping logic must retain the ability to distinguish true reposts from parallel duplicate inventory.

## What to test next

The foundation of the entire test approach is mock data with known transactions. We want to generate lots of synthetic `.db` snapshots where the truth is controlled by our own randomly generated events, and then compare the inferred outcomes from `AuctionScanDiff.py` against that known truth.

### Foundational test framework

The first iteration should be simple and repeatable:

- Build mock databases with individual item listings.
- Add random listing post events and sale events with random timestamps.
- Take random scans between these events to produce aliased snapshot `.db` files.
- Run the analysis script on the snapshots.
- Compare the analysis output to the known event history.

The mock engine should let us express the behavior of sellers and buyers directly, and then force the diff code to infer the same outcomes from the aliased scan data.

### First-phase actor model

Start with a fully rational, liquidation-focused market model:

- sellers are rational actors who always undercut the current best competitor by a random amount in the range 0.5% to 2%,
- buyers are rational and always buy the cheapest available listings up to their target quantity,
- listing quantities and stack sizes are explicit, so buyers can choose exact quantities when possible.

This gives us a controlled baseline where the expected behavior is easy to reason about, while still producing realistic auction activity.

### Controlled mock event examples

Example 1: simple liquidation behavior

- Seller A posts 20 units of Item X at 1000 copper.
- Seller B posts 20 units of Item X at 995 copper.
- Seller A reposts at 985 copper (random undercut of 1.0%).
- A buyer arrives and purchases 10 units from the cheapest listing.
- We take a scan before the sale and another after the sale.

The mock event stream should have timestamps and should continue long enough to simulate a virtual week of activity. The snapshot schedule itself should be noisy and varied, possibly following some patterns like:

- regular daily scans around 7pm ± 1 hour,
- clusters of 6–8 scans in an evening over 3–4 hours at least 15 minutes apart,
- occasional long gaps of 36+ hours or even 2 days to exercise full-expiration behavior.

The core test approach is built on generating lots of mock `.db` snapshots from this event stream and comparing the known transaction history against the inferred scan analysis.

There should also be a known “golden” market price for each item. The further a current listing price is from that market price, the more likely sellers and buyers are to change behavior.

- If the market price is 10g and a listing is at 7g, a seller may think “I could make more if I wait” and may undercut less aggressively, or even refuse to list the item.
- If listings for that item are at 15g, a seller may think “this is a great time to sell” and be more aggressive about undercutting.
- This can be modeled with `maybeBuy` and `maybeList` functions that use a logistic-style probability curve to cancel or delay a transaction when the price is too far from the market price.
- If no items are on the market, a seller may post at 50%–100% above the usual market price, because there is no competition.

Ground truth: the cheapest listing lost 10 units and should appear as a partial sale or disappearance depending on how the scan aliases the listing.

### Future stretch goals: more complicated seller and consumer behaviors

These are deliberate stretch goals for a later phase of the mock-data framework. They are not part of the initial baseline, but they should be captured as future behavior to model and validate.

- buyer exact-quantity preference and more complex consumer behavior
  - a buyer may want precisely 12 units, and the cheapest total-cost option can be a combination of higher-per-item stacks rather than a single large stack
  - buyers should have a configurable “buyer demand” knob that raises their willingness to pay above market price when demand is strong
  - this should be modeled later as a buyer choice that evaluates total cost and quantity fit, not just per-item price
  - ground truth here is a nontrivial sale pattern where `AuctionScanDiff.py` must infer which listings were consumed and which remained

- seller floor pricing and ebb/flow
  - a seller may choose to hold to a floor price even when cheaper items exist, because they expect cheaper supply to be consumed first
  - sellers should have a configurable “desperation” or liquidation desire knob that makes them more aggressively undercut when they need to sell quickly
  - cheap posters may have low quantity, so after those stacks are bought the higher-priced seller becomes the cheapest available supply
  - ground truth here is a listing that can persist through several scans while still being consistent with eventual demand-driven repricing, not a simple repost signal

### Validation harness blueprint

A formal harness should be built around this pattern:

- Generate mock snapshots `scan_N.db`, `scan_N1.db`, `scan_N2.db` from a sequence of synthetic posting and purchase events.
- Run `AuctionScanDiff.py scan_N.db scan_N2.db` and collect the inferred missing rows and statuses.
- Compare the inferred status for each missing listing against the known mock event stream.

### What to compare in the harness

For each predicted missing listing from N→N+2, compare against the known mock sequence:

- whether a matching listing exists in N+1 after ignoring unstable fields such as `seenTime` and `timeLeft`
- whether the row count in N+1 is zero, partially reduced, or unchanged
- whether the same seller/item appears in N+2 at lower price, indicating a repost or repricing event
- whether the timing aligns with the random event timestamps and chosen bucket durations

This turns abstract inference into concrete failure modes:

- “predicted `likely_sold`, but the mock timeline shows the listing persisted in N+1, so the algorithm missed a continuation case.”
- “predicted `likely_expired`, but the known events show the item survived into N+1 and only disappeared later, so the expiration threshold is too aggressive.”

### Practical harness output

The harness should produce both numerical summaries and example failures:

- counts of predicted status vs. actual ground truth outcome
- precision/recall for `repost` detection
- a list of worst misclassified examples, including item, seller, stack size, price, and scan timestamps
- a small set of “should have been missing but was sold/expired” cases based on the synthetic data

### Why this matters

This framework is important because it lets us test algorithm changes against known truth before we trust them in real scan data.

- generating LOTS of mock `.db` snapshots with known transactions is the foundation of the test framework.
- if grouping identical rows is added, the harness will show whether it improves counts and classification.
- if lower `timeLeft` transitions are allowed, the harness will show whether more true continuations are preserved.
- if repost logic is changed, the harness will show whether precision improves without sacrificing recall.

With this language in place, the document now describes the exact test framework shape, the mock-data foundation, and the kinds of example cases the harness should produce.
