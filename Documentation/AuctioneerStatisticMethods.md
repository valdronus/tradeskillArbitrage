# Auctioneer Stat Module Methods

This document describes the Auctioneer statistic modules beyond:
- `Market Price` (`auc-advanced/CoreAPI.lua`)
- `VendMarkup` (`auc-util-vendmarkup/vendMarkup.lua`)
- `Simple` (`auc-stat-simple/StatSimple.lua`)
- `Histogram` (`auc-stat-histogram/StatHistogram.lua`)

The additional modules are:
- `Stat-Debug`
- `Stat-iLevel`
- `Stat-Now`
- `Stat-Purchased`
- `Stat-Sales`
- `Stat-StdDev`
- `Stat-WoWEcon`

Each section below includes the core behavior, the relevant files, and the main formulas or logic used.

---

## 1. Stat-Debug

Purpose: developer/debug tool that stores the last several unit prices seen for an item and exposes them for inspection.

Files:
- `Documentation/References/auc-stat-debug/StatDebug.lua`

Key functions:
- `lib.ScanProcessors.create` (line 65)
- `lib.GetPrice` (line 117)
- `lib.GetPriceArray` (line 128)
- `lib.GetItemPDF` (line 149)

Behavior:
- Stores the last 10 per-item buyout prices seen during scans.
- `GetPriceArray` returns `price` as the most recent price and `seen` as the count of stored values.
- `GetItemPDF` returns nil, so Debug does not influence Market Price.

Outlier handling:
- No outlier filtering is applied. All recent samples are kept until the 10-entry history is full.

Formula:
- No statistical formula is used. This module is a raw sample list.

Notes:
- `Stat-Debug` is useful only for inspection and developer troubleshooting.

---

## 2. Stat-iLevel

Purpose: price statistics based on item level, quality, and equip location, using filtered standard deviation.

Files:
- `Documentation/References/auc-stat-ilevel/iLevel.lua`

Key functions:
- `lib.ScanProcessors.create` (line 102)
- `lib.GetPrice` (line 194)
- `lib.GetPriceColumns` (line 260)
- `lib.GetPriceArray` (line 265)
- `lib.GetItemPDF` (line 126)

Behavior:
- Only records buyouts for stack size 1 items.
- Groups history by item equip position and quality into buckets defined by `itemSig`.
- Computes mean and filtered average via a 1.5*stddev cutoff.
- Builds a bell-curve PDF from the filtered average and stddev for Market Price.

Outlier handling:
- Uses a 1.5 * stddev cutoff to exclude outliers from the normalized average.
- Values with `|price - mean| >= 1.5 * stddev` are treated as outliers and omitted from the filtered average.

Key formula steps:
- `mean = (sum of prices) / (sum of stacks)`
- `variance = (Σ(price_i/stack_i - mean)^2) / count`
- `stddev = sqrt(variance)`
- `deviation = 1.5 * stddev`
- Filtered average uses only values where `|price_i/stack_i - mean| < deviation`
- `confidence` is derived via a Z-value mapping and a formula like:
  - `confidence = 0.01` initially
  - `confidence = (.15 * average) * sqrt(number) / stdev`
  - converted by `private.GetCfromZ`

PDF:
- bell curve parameters: `average`, `stddev`, `area`
- bounds: `lower = average - 3 * stddev`, `upper = average + 3 * stddev`

---

## 3. Stat-Now

Purpose: current-scan-only statistics derived from the current auction snapshot, with no persistent storage.

Files:
- `Documentation/References/auc-stat-now/Auc-Stat-Now.lua`

Key functions:
- `lib.ScanProcessors.create` (line 182)
- `lib.GetPrice` (line 192)
- `lib.GetPriceColumns` (line 228)
- `lib.GetPriceArray` (line 233)
- `lib.GetItemPDF` (line 265)

Behavior:
- Uses `AucAdvanced.API.QueryImage` to read the current AH snapshot for the item.
- Decides value type based on number of auctions:
  - if `count < 5` or config forces it: MBO (minimum buyout)
  - if `5 <= count < 20`: simple mean
  - if `count >= 20`: trimmed mean
- The module is designed for a lightweight, session-only estimate.

Outlier handling:
- For larger samples, a trimmed mean removes extreme values from both ends of the sorted price list.
- This reduces the impact of very low or very high outliers on the final reported price.
- For small samples, no explicit outlier removal is performed beyond choosing min buyout.

Formulas:
- Mean: `mean = Σ(price_i) / n`
- Variance: `variance = Σ(price_i - mean)^2 / n`
- Standard deviation: `stddev = sqrt(variance)`
- Trimmed mean (when configured): remove a fraction `trim = floor(n * trimPercent / 100)` from each tail, then compute mean of remaining values.

PDF:
- When market PDF is enabled, it uses normal distribution bounds:
  - `lower = mean - 3 * stddev`
  - `upper = mean + 3 * stddev`

Note:
- This module is meant for current snapshot pricing and does not preserve history across sessions.

---

## 4. Stat-Purchased

Purpose: statistics based on auctions that were likely purchased, using daily buyout averages and moving averages.

Files:
- `Documentation/References/auc-stat-purchased/StatPurchased.lua`

Key functions:
- `lib.ScanProcessors.delete` (line 137)
- `lib.GetPrice` (line 289)
- `lib.GetPriceColumns` (line 337)
- `lib.GetPriceArray` (line 342)
- `lib.GetItemPDF` (line 243)
- `private.EstimateStandardDeviation` (inside file)
- `private.PushStats` (later in file)

Behavior:
- Captures assumed buyouts from auctions deleted before expected expiry in `ScanProcessors.delete`.
- Stores daily totals and counts, then archives them into 3-/7-/14-day EMAs.
- `GetPrice` returns daily average plus stored averages.
- `GetPriceArray` chooses a price using either the 3-day average or a safe weighted mix of 3/7/14-day averages.

Outlier handling:
- There is no explicit outlier filter on individual purchase prices.
- Outliers are handled indirectly through averaging and the use of longer-term EMAs, which smooth volatile daily values.
- The standard deviation estimate used for PDF weighting may also moderate extreme volatility.

EMA formula (from comments in the file):
- `TodaysMovingAverage = ((X-1) * YesterdaysMovingAverage + TodaysAverage) / X`
- where `X` is number of days (3, 7, or 14)

PDF estimation:
- Uses estimated mean/stddev from `private.EstimateStandardDeviation`
- Applies low-sample taper and clamping similar to `Stat-Simple`
- Bounds: `lower = mean - 3 * stddev`, `upper = mean + 3 * stddev`

---

## 5. Stat-Sales

Purpose: price statistics based on your personal BeanCounter sales history, using sold/bought average and filtered standard deviation.

Files:
- `Documentation/References/auc-stat-sales/BeanCount.lua`

Key functions:
- `lib.GetPrice` (line 174)
- `lib.GetPriceColumns` (line 306)
- `lib.GetPriceArray` (line 312)
- `lib.GetItemPDF` (line 83)

Behavior:
- Queries BeanCounter transaction history for the item.
- Separates bought and sold events.
- Computes weighted averages for total sales, 3-day sales, 7-day sales, and purchases.
- Uses sold price as the base mean for variance.
- Filters sale prices within `1.5 * stddev` of the mean to compute a normalized average.

Outlier handling:
- Uses a 1.5 * stddev cutoff on sold prices to remove unusually high or low sale prices.
- Only entries with `|priceper - mean| < 1.5 * stddev` contribute to the normalized average.

Formulas:
- Sold mean: `mean = total sold price / sold quantity`
- Variance: `variance = Σ(mean - priceper)^2 / count`
- Stddev: `stdev = sqrt(variance)`
- Filter cutoff: `deviation = 1.5 * stdev`
- Normalized average: average price of sold entries where `|priceper - mean| < deviation`
- Confidence uses the same Z-based mapping as `Stat-StdDev` and `Stat-iLevel`.

PDF:
- Bell curve with `average`, `stddev`, and bounding range `average ± 3*stddev`

Notes:
- Only works when BeanCounter is installed and loaded.
- Returns separate sold/bought history fields in `GetPriceArray`.

---

## 6. Stat-StdDev

Purpose: filtered standard deviation pricing from recent auction prices.

Files:
- `Documentation/References/auc-stat-stddev/StatStdDev.lua`

Key functions:
- `lib.ScanProcessors.create` (line 128)
- `lib.GetPrice` (line 225)
- `lib.GetPriceColumns` (line 295)
- `lib.GetPriceArray` (line 300)
- `lib.GetItemPDF` (line 158)

Behavior:
- Stores up to 100 buyout values per item in the saved database.
- Computes mean, variance, and stddev from all stored entries.
- Filters values within `1.5 * stddev` of the mean to compute a normalized average.
- Computes confidence using a Z-value mapping.

Outlier handling:
- Filters values to include only those within `1.5 * stddev` of the mean.
- This removes prices that are far from the central tendency, reducing the impact of anomalous data.

Formulas:
- Mean: `mean = total / number`
- Variance: `variance = Σ((mean - price/stack)^2) / count`
- Stddev: `stdev = sqrt(variance)`
- Filter cutoff: `deviation = 1.5 * stdev`
- Normalized average: `average = Σ(price_i) / Σ(stack_i)` for filtered entries
- Confidence:
  - `c = (.15 * average) * sqrt(number) / stdev`
  - `confidence = GetCfromZ(c)`

PDF:
- Uses bell curve with parameters `average`, `stddev`, and area.
- Bounds: `lower = average - 3 * stddev`, `upper = average + 3 * stddev`
- Applies clamping to avoid very low/high stddev values.

---

## 7. Stat-WoWEcon

Purpose: legacy adapter for WoWEcon price data; now deprecated and effectively disabled.

Files:
- `Documentation/References/auc-stat-wowecon/WOWEcon.lua`

Behavior:
- Does not provide `GetPrice`, `GetPriceArray`, or `GetItemPDF`.
- Cleans obsolete settings on load.
- Exists only to preserve compatibility with older Auctioneer configurations.

Notes:
- This module is no longer a pricing source.
- It is present primarily for historical reference.

---

## Summary of stat method categories

- `Market Price` is an aggregator that combines PDFs from stat modules.
- `VendMarkup` is a `Util` module that returns vendor-based fallback pricing.
- `Simple` and `Histogram` are the best-known item-history modules.
- The additional modules above provide complementary statistical approaches:
  - direct recent-scan pricing (`Stat-Now`)
  - personal purchase history (`Stat-Purchased`, `Stat-Sales`)
  - filtered standard deviation models (`Stat-StdDev`, `Stat-iLevel`)
  - support/debug modules (`Stat-Debug`, `Stat-WoWEcon`)

## Recommended reading order

1. `Documentation/References/auc-stat-simple/StatSimple.lua`
2. `Documentation/References/auc-stat-histogram/StatHistogram.lua`
3. `Documentation/References/auc-stat-stddev/StatStdDev.lua`
4. `Documentation/References/auc-stat-ilevel/iLevel.lua`
5. `Documentation/References/auc-stat-purchased/StatPurchased.lua`
6. `Documentation/References/auc-stat-sales/BeanCount.lua`
7. `Documentation/References/auc-stat-now/Auc-Stat-Now.lua`
8. `Documentation/References/auc-stat-debug/StatDebug.lua`
9. `Documentation/References/auc-stat-wowecon/WOWEcon.lua`

If you want, I can also add a second document describing how `Market Price` combines these modules in `auc-advanced/CoreAPI.lua`.