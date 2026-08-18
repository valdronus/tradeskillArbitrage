# Comprehensive Validation Hypotheses & Diagnostic Findings

## 1. Current Status & Diagnostic Summary
- The `MockDBData` generator and validator pipeline now runs end-to-end via `make generate` and `make validate`.
- `AuctionScanDiff.py` is invoked in directory mode using `--scan-dir ./data` and `--sales-db ./sales.db`.
- Baseline validation against synthetic ground truth suffered from low reported accuracy (~18.2%), total zero recall on `sold` and `expired` outcomes, and zero precision on `repost`.
- Prediction distribution from baseline `sales.db`: 758 `missing`, 188 `likely_sold`, 0 `likely_expired`, 768 `repost`.

Our deep dive identified **five core root causes**:
1. **Ground-Truth Lifecycle & Key Identity Collisions (Key Mismatch & Lifetime Overwrite)**
2. **Missing Snapshot Paths in Sequential Directory Mode Persistence**
3. **Overly Aggressive Repost Classification Heuristics**
4. **Discontinuous Gray-Area Decision Boundaries (Hard-Coded Bucket Cutoffs vs. Continuous Likelihoods)**
5. **Simulation Timing and Schedule Aliasing**

---

## 2. Root Cause Analysis & Evidence

### 2.1 Ground-Truth Matching & Key Collision Failures
In [MockDBData/validate.py](validate.py):
- **Unit Price vs. Buyout Price mismatch**:
  `GroundTruthRecord` in `loadGroundTruth()` constructed indexing keys formatted as:
  ```python
  key = f"{record.item_id}_{record.seller_name}_{record.stack_size}_{int(record.unit_price)}"
  ```
  While `matchPredictionToGroundTruth()` looked up predictions with:
  ```python
  key = f"{prediction.item_id}_{prediction.seller_name}_{prediction.stack_size}_{int(prediction.buyout_price or 0)}"
  ```
  Because `unit_price = buyout_price / stack_size`, whenever `stack_size > 1` (e.g. 5 or 20), `int(unit_price) != int(buyout_price)`. As a result, all multi-item stack predictions automatically evaluated as `actual_outcome = 'unmatched'`.

- **State Overwrites on Recurring Seller-Item Listings**:
  In `loadGroundTruth()`, a single flat dictionary `records[key]` was overwritten whenever a seller posted multiple listings with the same price and stack size across different time steps. This discarded prior lifecycle histories (such as actual buy or expire events) in favor of the latest active state.
  *Fix Requirement*: Ground-truth matching must maintain list queues per key sorted by posting timestamp and match against the active time window between `before_snapshot` and `after_snapshot`.

- **Missing Details in Event Payloads**:
  `addListing()`, `expireListings()`, and `repostListing()` in [MockDBData/generate.py](generate.py) omitted `item_name` or `unit_price` in some event details dictionaries, causing incomplete record construction.

---

### 2.2 Schema Serialization & Snapshot Path Propagation Bug
In [AuctionScanDiff.py](../AuctionScanDiff.py):
- When running in single-pair mode (`run_pipeline`), `before_path` and `after_path` were passed to `DiffResult` and written to `sales.db`.
- However, inside `diff_snapshots()`, default arguments lacked path propagation unless explicitly passed down.
- In `run_directory_pipeline()`, `sales.db` stored `NULL` for `before_snapshot` and `after_snapshot` if `before_path`/`after_path` weren't propagated into the individual classifier loops.
- `find_db_paths()` also had a subtle bug where if `sales.db` already existed inside `--scan-dir ./data`, path comparison by `.resolve()` could fail if `exclude` was relative and not yet created, causing `sales.db` to be parsed as an input snapshot.
  *Fix Requirement*: Explicitly filter out `sales.db` by filename in `find_db_paths` and pass snapshot paths consistently to all `DiffResult` records.

---

### 2.3 Fragile Repost Classification Heuristics
In the baseline `diff_snapshots()`:
- `is_reposted()` classified *any* missing listing as `status = "repost"` if the seller had *any* active listing of the same item at a lower unit price in the subsequent scan.
- Because rational seller agents in [MockDBData/generate.py](generate.py) frequently post additional lower-priced inventory without cancelling existing inventory, existing listings that actually **sold** were misclassified as `repost` simply because the seller had another lower-priced stack in the next scan.
- Furthermore, `is_reposted` lacked continuous confidence scoring (e.g., checking if the undercut ratio was realistic, matching stack size preference, or checking if the replacement listing was fresh in bucket 4).
  *Fix Requirement*: Introduce multi-factor continuous confidence scoring for reposts:
  $$\text{Confidence}_{\text{repost}} = w_{\text{undercut}} \cdot S_{\text{undercut}} + w_{\text{stack}} \cdot S_{\text{stack}} + w_{\text{freshness}} \cdot S_{\text{freshness}}$$
  And require $\text{Confidence}_{\text{repost}} \ge \theta_{\text{repost}}$.

---

### 2.4 Hard-Coded Bucket Boundaries vs Continuous Gray-Area Scoring
Auctioneer uses 4 discrete time-left bucket codes:
- Bucket 1: $0.0 - 0.5$ hours (Short)
- Bucket 2: $0.5 - 2.0$ hours (Medium)
- Bucket 3: $2.0 - 12.0$ hours (Long)
- Bucket 4: $12.0 - 48.0$ hours (Very Long)

In the baseline implementation:
- If a listing disappeared during an interval that fell inside $[t_{\min}, t_{\max}]$, it was hardcoded to `status = "missing"`.
- Because synthetic snapshots are spaced 4.0 hours apart, almost every listing in bucket 3 ($2-12\text{h}$) or bucket 4 ($12-48\text{h}$) disappeared *inside* the gray window and became `missing`, yielding $0\%$ recall on `sold` and `expired`.
- In practice, a continuous model should compute a unified sold-likelihood score $P(\text{sold} \mid \vec{x})$ across timing, competitor price distribution, and continuity evidence, then use continuous tunable decision thresholds $\theta_{\text{sold}}$ and $\theta_{\text{expired}}$:
  - If $P(\text{sold}) \ge \theta_{\text{sold}} \implies \text{likely\_sold}$
  - If $P(\text{sold}) \le \theta_{\text{expired}} \implies \text{likely\_expired}$
  - Otherwise $\implies \text{missing}$ (uncertain)

---

## 3. Continuous Hyperparameter Model Formulation

To allow fine-grained parameter sweeps and machine learning / grid optimization, we model listing outcome likelihoods continuously:

### 3.1 Timing Score $S_{\text{timing}} \in [0, 1]$
Given elapsed scan gap $\Delta t$ and bucket bounds $[t_{\min}, t_{\max}]$:
$$u = \text{clamp}\left(\frac{\Delta t - t_{\min}}{t_{\max} - t_{\min}}, 0.0, 1.0\right)$$
$$S_{\text{timing}} = (1.0 - u)^{\gamma_{\text{decay}}}$$
- When $\Delta t \le t_{\min}$, $S_{\text{timing}} = 1.0$ (definite sale).
- When $\Delta t \ge t_{\max}$, $S_{\text{timing}} = 0.0$ (definite expiration).
- $\gamma_{\text{decay}} > 0$ controls the non-linear decay rate over time.

### 3.2 Price Competitiveness Score $S_{\text{price}} \in [0, 1]$
Let $p_{\text{unit}}$ be the listing's unit price, and $\mathcal{P}_{\text{surviving}}$ be the unit prices of all surviving competitor listings of the same item:
- **Rank component**:
  $$S_{\text{rank}} = 1.0 - \frac{|\{p \in \mathcal{P}_{\text{surviving}} : p < p_{\text{unit}}\}|}{|\mathcal{P}_{\text{surviving}}|}$$
- **Market ratio component** (relative to median surviving price $\tilde{p}$):
  $$S_{\text{ratio}} = \frac{1}{1 + \left(\frac{p_{\text{unit}}}{\tilde{p}}\right)^{\alpha_{\text{sensitivity}}}}$$
- **Combined Price Score**:
  $$S_{\text{price}} = 0.6 \cdot S_{\text{rank}} + 0.4 \cdot S_{\text{ratio}}$$

### 3.3 Continuity / Survivor Score $S_{\text{cont}} \in [0, 1]$
If cheaper competitors survived into the subsequent scan, the likelihood that the higher-priced listing was purchased before cheaper inventory drops significantly:
$$S_{\text{cont}} = \begin{cases} 
0.75 & \text{if no cheaper survivors exist} \\
1.0 - \frac{|\text{cheaper survivors}|}{|\text{all survivors}|} & \text{if cheaper survivors exist} \\
0.50 & \text{if no peer data available}
\end{cases}$$

### 3.4 Composite Likelihood & Tunable Decision Boundaries
$$P(\text{sold}) = \frac{w_{\text{timing}} \cdot S_{\text{timing}} + w_{\text{price}} \cdot S_{\text{price}} + w_{\text{cont}} \cdot S_{\text{cont}}}{w_{\text{timing}} + w_{\text{price}} + w_{\text{cont}}}$$

Hyperparameters exposed via CLI & Configuration:
- `--timing-weight` ($w_{\text{timing}}$): weight for duration elapsed ($[0.0, 1.0]$, default: 0.45)
- `--price-weight` ($w_{\text{price}}$): weight for competitor pricing ($[0.0, 1.0]$, default: 0.35)
- `--continuity-weight` ($w_{\text{cont}}$): weight for survivor continuity ($[0.0, 1.0]$, default: 0.20)
- `--timing-decay-power` ($\gamma_{\text{decay}}$): non-linear decay power (default: 1.0)
- `--price-sensitivity` ($\alpha_{\text{sensitivity}}$): price elasticity scaling (default: 1.5)
- `--sold-threshold` ($\theta_{\text{sold}}$): minimum likelihood for `likely_sold` (default: 0.50)
- `--expired-threshold` ($\theta_{\text{expired}}$): maximum likelihood for `likely_expired` (default: 0.45)
- `--repost-threshold` ($\theta_{\text{repost}}$): minimum confidence for `repost` (default: 0.60)
- `--repost-min-undercut`: minimum undercut percentage (default: 0.005)
- `--repost-max-undercut`: maximum plausible undercut percentage (default: 0.50)

---

## 4. Testable Hypotheses

| ID | Hypothesis Statement | Verification Method | Expected Outcome |
|---|---|---|---|
| **H1** | Aligning matching keys by unit price and active timestamp windows will resolve `unmatched` errors in ground truth matching. | Run `make validate` and compare `unmatched` count in the confusion matrix. | `unmatched` count drops from >700 to near 0. |
| **H2** | Introducing continuous sold thresholds ($\theta_{\text{sold}}, \theta_{\text{expired}}$) will convert arbitrary `missing` gray-window listings into accurate `likely_sold` and `likely_expired` predictions. | Compare Precision, Recall, and F1 across `sold` and `expired` classes. | `sold` recall increases from 0.0% to >75%; `expired` recall increases from 0.0% to >70%. |
| **H3** | Multi-factor continuous repost confidence ($\theta_{\text{repost}} \ge 0.60$, stack size and undercut bounds) will eliminate false positive reposts. | Inspect `repost` precision and `active`/`sold` confusion in validation reports. | `repost` precision increases from 0.0% to >80%. |
| **H4** | Continuous hyperparameter sweeping via a validation grid search will identify global optima for $w_{\text{timing}}, w_{\text{price}}, w_{\text{cont}}, \theta_{\text{sold}}, \theta_{\text{expired}}$. | Execute `--sweep` parameter grid over multiple simulation schedule profiles (`all`, `7pm`, `evening_cluster`, `long_gaps`). | Optimal parameter combinations achieve overall classification accuracy $>85\%$. |

---

## 5. Next Execution Steps
1. Complete timestamp-aware and unit-price-normalized matching in [MockDBData/validate.py](validate.py).
2. Wire up parameter sweeping (`--sweep`) in `validate.py` and add `make sweep` in [MockDBData/Makefile](Makefile).
3. Update [MockDBData/test_mock_framework.py](test_mock_framework.py) with full coverage unit tests for the classifier and validation harness.
4. Run validation across different snapshot schedules (`7pm`, `evening_cluster`, `long_gaps`), record metric benchmarks, and update the final validation report.

