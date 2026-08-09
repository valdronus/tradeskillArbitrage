# New Auctioneer Stat Module Plan

This document explains how to build a new Auctioneer stat module that:
- uses all available price data,
- excludes extreme outliers,
- weights prices continuously by percentile,
- emphasizes the 10th–25th percentile range,
- still allows 5–10 and 25–75 to contribute moderately,
- keeps 0–5 and 75–100 ranges as minimal but non-zero input.

It also gives explicit Lua implementation suggestions and cites existing Auctioneer files for inspiration.

## Goals

1. Build on the existing `StatHistogram` architecture.
2. Use percentile-based weighting instead of hard cutoffs.
3. Use a robust outlier definition based on the 10–90 percentile spread.
4. Prefer a weighted mean rather than a trimmed or hard-clipped mean.
5. Produce a module that can integrate into Auctioneer Advanced's `Market Price` system.

## Why `StatHistogram` is the best starting point

Use `Documentation/References/auc-stat-histogram/StatHistogram.lua` as the implementation base because:
- it stores price frequency data in buckets,
- it already computes quantile boundaries (`Q1`, `median`, `Q3`, `percent30`, `percent40`),
- it can produce a probability distribution function for market price.

Key reference points:
- `private.GetPriceData()` in `StatHistogram.lua` computes quantiles.
- `lib.GetPriceArray()` in `StatHistogram.lua` returns computed statistics.
- `auc-advanced/CoreAPI.lua` uses `GetItemPDF()` from stat modules to compute market price.

## Core math

### 1. Robust outlier pass

Compute a central distribution from the 10–90 percentile range and exclude extreme values based on its standard deviation.

1. Compute percentiles for each price bucket:
   - 10th percentile: `p10`
   - 25th percentile: `p25`
   - 75th percentile: `p75`
   - 90th percentile: `p90`

2. Define the central subset `C`:
   - include all prices between `p10` and `p90`.

3. Compute robust mean and stddev from `C`:
   - `mu_c = sum(x in C) / |C|`
   - `sigma_c = sqrt(sum((x - mu_c)^2) / |C|)`

4. Exclude extreme outliers:
   - keep prices where `|x - mu_c| <= 3 * sigma_c`

This means you still base the distribution on the center of the market, but outliers beyond a robust 3-sigma range are dropped.

### 2. Percentile rank and weight function

For each remaining price point, compute its percentile rank `p` in `[0,100]`.

Then apply a continuous weight function `w(p)` that satisfies:
- full weight in `[10,25]`
- moderate weight in `[5,10]` and `[25,75]`
- minimal but non-zero weight in `[0,5]` and `[75,100]`

#### Example weight function

Use a piecewise smooth shape.

Constants:
```lua
local EPS = 0.05
local W5 = 0.2
local W75 = 0.4
```

Weight function:
```lua
local function weightForPercentile(p)
    if p < 0 then p = 0 end
    if p > 100 then p = 100 end

    if p < 5 then
        local t = p / 5
        return EPS + (W5 - EPS) * t * t
    elseif p < 10 then
        local t = (p - 5) / 5
        return W5 + (1 - W5) * (3*t*t - 2*t*t*t) -- smoothstep
    elseif p <= 25 then
        return 1
    elseif p <= 75 then
        local t = (p - 25) / 50
        return W75 + (1 - W75) * (1 - t*t)
    else
        local t = (p - 75) / 25
        return EPS + (W75 - EPS) * (1 - t*t)
    end
end
```

This gives:
- `0–5`: very low weight rising from `EPS` to `W5`
- `5–10`: ramp from `W5` to `1`
- `10–25`: weight `1`
- `25–75`: slowly decaying from `1` to `W75`
- `75–100`: rapidly decaying from `W75` to `EPS`

### 3. Weighted mean computation

If you have raw price values `xi` and weights `wi`:

	ratio = sum(wi * xi) / sum(wi)

If you have histogram buckets with center price `xi` and count `ci`, then:

	weighted_mean = sum(wi * ci * xi) / sum(wi * ci)

This ensures every price influences the result, but prices in the 10–25 bracket dominate.

### 4. Volume and sales awareness

For low-volume items, the highest-probability price may legitimately sit above the lowest percentiles because there are fewer listings and each sale event matters more. For high-volume items, the market tends to stay close to the lower percentiles and the 10–25 percentile range becomes a much stronger signal.

A simple volume-aware adjustment can be built on top of the weighted percentile mean:
- measure `seen` or `total_count` from the histogram data,
- define a `volumeFactor` that decays from 1 for low volume to a smaller value for high volume,
- allow the result to shift slightly upward when volume is low and the market shows thin supply.

Example:
```lua
local function volumeAdjustment(weightedMean, typicalLowPrice, seen)
    local minSeen, maxSeen = 5, 200
    local v = math.min(math.max((seen - minSeen) / (maxSeen - minSeen), 0), 1)
    local lift = (1 - v) * 0.15  -- up to +15% on very low volume
    return weightedMean + (typicalLowPrice - weightedMean) * lift
end
```

With this approach, a low-volume item can price closer to the observed market floor without fully trusting a tiny sample, while a high-volume item remains anchored by the 10–25 percentile distribution.

### 5. Smart buyer / Average Minimum Buyout

The module should also consider that a "smart buyer" will not pay far above a common accepted low price. In Auctioneer, this is already captured by concepts like `Average Minimum Buyout` and other floor-based statistics.

Use this idea as a sanity check or secondary floor:
- compute an `avgMinBuyout` from the lowest accepted prices in recent scans,
- treat it as a soft ceiling for how far above the percentile-weighted result you should allow the module to quote,
- if the weighted mean is significantly higher than `avgMinBuyout`, bias the output back toward the lower historical floor.

Example soft-ceiling rule:
```lua
local function smartBuyerFloor(price, avgMinBuyout)
    local maxRatio = 1.15
    return math.min(price, avgMinBuyout * maxRatio)
end
```

This keeps the final quote consistent with buyer expectations: the model still uses broader percentile data, but it avoids recommending a price that is too far above the market’s lowest typical acceptable buyout.

### 6. Inflation and reset-aware pricing

In your environment, the market has an annual reset event that effectively halves gold supply while leaving item supply unchanged. During the period between resets, prices tend to drift upward as gold inflation builds.

There is also a hard minimum price floor immediately after the reset. That floor is not simply vendor price plus fees; it is the vendor-equivalent sale price adjusted for the auction house cut, plus the cost to post the auction.

A practical floor calculation is:
- `vendorFloor = ceil(vendorPrice / AHCutAdjust) + deposit`

Where:
- `vendorPrice` is the item vendor sell price,
- `AHCutAdjust` is the multiplier from `Documentation/References/auc-advanced/CoreResources.lua` that converts vendor price into the break-even buyout after the 5% auction house brokerage,
- `deposit` is calculated with `AucAdvanced.Post.GetDepositCost()` from `Documentation/References/auc-advanced/CorePost.lua`.

This means the true minimum buyout after reset is effectively the vendor-equivalent price plus posting cost, and it can act as a hard floor for the model.

A time-aware price adjustment can make the module more consistent over long intervals:
- track the number of days since the last halving event,
- define an inflation multiplier that grows with elapsed days,
- apply the multiplier to historical price estimates or to the final weighted result,
- optionally normalize older data by downgrading prices from earlier in the current cycle.

Example:
```lua
local function inflationMultiplier(daysSinceReset)
    local annualGrowth = 2.0  -- price roughly doubles over a full cycle
    local cycleDays = 365
    local rate = math.log(annualGrowth) / cycleDays
    return math.exp(rate * daysSinceReset)
end
```

Using this rule:
- if `daysSinceReset` is near 0, the multiplier is near 1,
- if `daysSinceReset` is near 365, the multiplier is near 2,
- intermediate values scale prices smoothly with the in-game inflation trend.

For a more robust module, keep both:
- an inflation-adjusted published price for current quoting,
- an inflation-normalized historical baseline for trend analysis.

Example normalization of historical values:
```lua
local function normalizePrice(price, ageDays)
    local ageFactor = inflationMultiplier(ageDays)
    return price / ageFactor
end
```

That normalization means older price data can be compared on a consistent basis before the percentile weights are computed.

### 6.1 Item-specific growth factors

Not every item will double at the same rate. Some items are investment plays, others are commoditized and follow the general market drift more closely. To capture that, the model can learn a per-item growth factor from historical price data.

A simple regression-style approach is:
- normalize each historical data point by age using a base inflation curve,
- fit a growth factor `g_item` so that the item’s normalized prices are as flat as possible over the cycle,
- treat `g_item` as an item-specific multiplier that adjusts the general inflation multiplier.

Example:
```lua
local function estimateItemGrowthFactor(priceHistory)
    local sumX, sumY, sumXX, sumXY, n = 0, 0, 0, 0, 0
    for _, entry in ipairs(priceHistory) do
        local t = entry.daysSinceReset
        local x = t
        local y = math.log(entry.price)
        sumX = sumX + x
        sumY = sumY + y
        sumXX = sumXX + x * x
        sumXY = sumXY + x * y
        n = n + 1
    end
    if n < 2 then return 1 end
    local slope = (n * sumXY - sumX * sumY) / (n * sumXX - sumX * sumX)
    return math.exp(slope)
end
```

This gives a fitted per-item growth curve instead of forcing every item to use the same annual doubling.

### 6.2 Inflation phases

Importantly, inflation in your environment is not smooth. There is:
- a rapid growth phase just after the reset,
- a slower middle phase during the steady season,
- another rapid growth phase approaching the next reset.

A piecewise or phase-aware inflation model is more realistic than a single exponential curve. For example:
- `0–60 days` after reset: rapid growth,
- `60–300 days` mid-cycle: slow growth,
- `300–365 days` before reset: rapid growth again.

A practical function could be:
```lua
local function phaseInflation(daysSinceReset)
    local p = daysSinceReset / 365
    if p < 0.16 then
        return 1 + 0.5 * (p / 0.16)
    elseif p < 0.82 then
        return 1.5 + 0.25 * ((p - 0.16) / 0.66)
    else
        return 1.75 + 0.25 * ((p - 0.82) / 0.18)
    end
end
```

This is just an example; the actual parameters should be fit from the observed market cycle.

### 6.3 Hard floor and investment items

After reset, the postable floor is often the vendor-equivalent floor plus auction posting cost. Near the next reset, well-chosen investment items may be priced at or slightly above twice that floor, making them especially valuable for strategy.

Use this in the model by:
- calculating `vendorFloor` for each item,
- enforcing a lower bound on the adjusted price,
- flagging items whose predicted price is close to `2 * vendorFloor` as potential investment candidates.

Example:
```lua
local function vendorFloorEstimate(vendorPrice, duration, faction, stack)
    local deposit = AucAdvanced.Post.GetDepositCost(itemLink, duration, faction, stack) or 0
    local floorPrice = math.ceil(vendorPrice / Resources.AHCutAdjust) + deposit
    return floorPrice
end

local function investmentScore(predictedPrice, vendorFloor)
    return predictedPrice / vendorFloor
end
```

If `investmentScore` is near 2 late in the cycle, the item may behave like a strong inflation-linked investment.

## Implementation plan

### Module structure

Create a new stat module under `Documentation/References/auc-stat-<name>`.

Suggested files:
- `auc-stat-percentile/StatPercentile.lua`
- `auc-stat-percentile/Auc-Stat-Percentile.toc`
- `auc-stat-percentile/Embed.xml`

Use the same module pattern as `StatHistogram.lua` and `StatSimple.lua`.

### Main Lua module

The new module should:
- register as `libType = "Stat"`, `libName = "Percentile"`
- use `AucAdvanced.NewModule` and local module locals
- define `lib.ScanProcessors.create` to collect bucketed price data
- define `lib.GetPrice` / `lib.GetPriceArray`
- define `lib.GetItemPDF` so it can supply a PDF to `Market Price`
- define `private.SetupConfigGui` for enable/disable and optional parameters

### Pricing computation flow

1. `ScanProcessors.create` records per-auction buyout values into histogram buckets.
   - similar to `StatHistogram.lua` lines 281–340
   - bucket price by `ceil(buyout / step)` or by a fixed bucket size

2. When `GetPrice` is called:
   - open the stored histogram data for the item
   - compute cumulative counts
   - derive percentiles: `p10`, `p25`, `p75`, `p90`
   - calculate robust `mu_c` and `sigma_c` from the 10–90 subset
   - exclude any bucket outside `mu_c ± 3*sigma_c`
   - assign percentile rank `p` to each bucket center
   - compute weight `w(p)`
   - calculate weighted mean

3. `GetPriceArray` returns:
   - `.price = weighted_mean`
   - `.seen = total_count`
   - optional fields: `.p10`, `.p25`, `.p75`, `.p90`, `.weight10to25`

4. `GetItemPDF` can optionally expose a PDF for `Market Price`.
   - could return a bell curve around `weighted_mean` with stddev derived from the filtered values
   - or return `nil` if you prefer this module only as a raw price source

### Explicit code suggestions

#### Percentile extraction helper

```lua
local function percentileFromHistogram(table, count, step, target)
    local threshold = count * target / 100
    local cum = 0
    for i = table.min, table.max do
        cum = cum + (table[i] or 0)
        if cum >= threshold then
            return i * step
        end
    end
    return table.max * step
end
```

#### Weighted mean helper

```lua
local function percentileWeight(p)
    local EPS, W5, W75 = 0.05, 0.2, 0.4
    if p < 5 then
        local t = p / 5
        return EPS + (W5 - EPS) * t * t
    elseif p < 10 then
        local t = (p - 5) / 5
        return W5 + (1 - W5) * (3*t*t - 2*t*t*t)
    elseif p <= 25 then
        return 1
    elseif p <= 75 then
        local t = (p - 25) / 50
        return W75 + (1 - W75) * (1 - t*t)
    else
        local t = (p - 75) / 25
        return EPS + (W75 - EPS) * (1 - t*t)
    end
end
```

#### Weighted mean from histogram

```lua
local function weightedMeanFromHistogram(StatTable)
    local count = StatTable.count
    if not count or count == 0 then return end

    local p10 = percentileFromHistogram(StatTable, count, StatTable.step, 10)
    local p25 = percentileFromHistogram(StatTable, count, StatTable.step, 25)
    local p75 = percentileFromHistogram(StatTable, count, StatTable.step, 75)
    local p90 = percentileFromHistogram(StatTable, count, StatTable.step, 90)

    local sumC, sumC2 = 0, 0
    for i = StatTable.min, StatTable.max do
        local c = StatTable[i] or 0
        if c > 0 then
            local price = i * StatTable.step
            if price >= p10 and price <= p90 then
                sumC = sumC + c * price
                sumC2 = sumC2 + c * price * price
            end
        end
    end
    local n = 0
    for i = StatTable.min, StatTable.max do
        if StatTable[i] then n = n + StatTable[i] end
    end
    local mu_c = sumC / n
    local sigma_c = math.sqrt((sumC2 / n) - (mu_c * mu_c))

    local lower = mu_c - 3 * sigma_c
    local upper = mu_c + 3 * sigma_c

    local weightedSum, weightTotal = 0, 0
    for i = StatTable.min, StatTable.max do
        local c = StatTable[i] or 0
        if c > 0 then
            local price = i * StatTable.step
            if price >= lower and price <= upper then
                local p = percentileRank(price, StatTable, count)
                local w = percentileWeight(p)
                weightedSum = weightedSum + w * c * price
                weightTotal = weightTotal + w * c
            end
        end
    end
    if weightTotal == 0 then return mu_c end
    return weightedSum / weightTotal
end
```

#### Percentile rank helper

```lua
local function percentileRank(price, StatTable, count)
    local cum = 0
    for i = StatTable.min, StatTable.max do
        local c = StatTable[i] or 0
        cum = cum + c
        if i * StatTable.step >= price then
            return (cum / count) * 100
        end
    end
    return 100
end
```

### Integration into Auctioneer market pricing

If you want this module to contribute to `Market Price`, implement `lib.GetItemPDF` as a bell curve or another distribution function.

Use `auc-advanced/CoreAPI.lua` for reference:
- how `Market Price` discovers stat modules: `AucAdvanced.GetAllModules(nil, "Stat")`
- how `lib.GetItemPDF` is expected to return: `pdf, lower, upper, area`

If this module is only a raw price engine, it can return no PDF and still be useful as a tooltip price provider.

## Implementation steps

1. Copy `Documentation/References/auc-stat-histogram/StatHistogram.lua` into a new `auc-stat-percentile` module.
2. Rename the module metadata and `.toc` entries.
3. Keep the bucket storage and load/save patterns from `StatHistogram`.
4. Replace the quantile-only output with the weighted percentile mean logic above.
5. Add configuration options:
   - enable/disable module
   - bucket precision or step size
   - minimum data count before rating
   - optional weight constants (`EPS`, `W5`, `W75`)
6. Add `GetItemPDF` if Market Price integration is desired.
7. Add tooltip support similar to `StatHistogram`.

## Suggested file citations

- `Documentation/References/auc-stat-histogram/StatHistogram.lua`
  - quantile computation and histogram storage
- `Documentation/References/auc-stat-simple/StatSimple.lua`
  - `GetPriceArray()` and price array conventions
- `Documentation/References/auc-advanced/CoreAPI.lua`
  - `GetItemPDF()` integration and how `Market Price` aggregates PDFs
- `Documentation/References/auc-stat-stddev/StatStdDev.lua`
  - Z-based confidence and filtered mean pattern
- `Documentation/References/auc-stat-now/Auc-Stat-Now.lua`
  - trimmed-mean logic and snapshot-based estimation

## Example plan outline

1. **Module scaffold**
   - create `auc-stat-percentile/StatPercentile.lua`
   - use `libType = "Stat"`, `libName = "Percentile"`
2. **Data capture**
   - reuse `StatHistogram.lua` scan processing
   - store histogram counts per price bucket
3. **Percentile math**
   - compute `p10`, `p25`, `p75`, `p90`
   - compute central mean and stddev from 10–90 range
   - exclude values outside `mu_c ± 3 sigma_c`
4. **Weight function**
   - implement `weightForPercentile(p)`
   - use it to compute a weighted mean
5. **Output**
   - `GetPrice` returns the weighted percentile mean
   - `GetPriceArray` exposes `.price`, `.seen`, and percentile fields
   - `GetItemPDF` optionally returns a PDF for `Market Price`
6. **Testing**
   - compare output to current `StatHistogram` median
   - verify extremes have minimal effect
   - verify the 10–25 range is dominant

## Example module skeleton

```lua
local libType, libName = "Stat", "Percentile"
local lib,parent,private = AucAdvanced.NewModule(libType, libName)
if not lib then return end

local aucPrint,decode,_,_,replicate,empty,get,set,default,debugPrint,fill = AucAdvanced.GetModuleLocals()
local Resources = AucAdvanced.Resources
local ResolveServerKey = AucAdvanced.ResolveServerKey
local GetStoreKey = AucAdvanced.API.GetStoreKeyFromLinkB

local DATABASE_VERSION = 1
local TABLE_DIVIDER = ";"
local ITEM_DIVIDER = "_"
local PET_BAND = 10

local tinsert, tremove, wipe = table.insert, table.remove, wipe
local floor, ceil, sqrt = floor, ceil, sqrt

local StatTable = {}

function lib.ScanProcessors.create(operation, itemData, oldData)
    if not get("stat.percentile.enable") then return end
    local buyout = itemData.buyoutPrice
    if not buyout or buyout == 0 then return end

    if itemData.stackSize > 1 then
        buyout = buyout / itemData.stackSize
    end

    local itemID, property = GetStoreKey(itemData.link, PET_BAND)
    if not itemID then return end

    local serverKey = Resources.ServerKey
    private.StatTableOpen(serverKey, itemID, property, true)
    -- bucket and store the value, similar to StatHistogram
    private.AddHistogramValue(buyout)
    private.StatTableWrite(serverKey, itemID, property)
    private.StatTableClose()
end

function lib.GetPrice(link, serverKey)
    if not get("stat.percentile.enable") then return end
    serverKey = ResolveServerKey(serverKey)
    if not serverKey then return end

    local itemID, property = GetStoreKey(link, PET_BAND)
    if not itemID then return end

    if not private.StatTableOpen(serverKey, itemID, property) then return end
    local price = private.GetWeightedPercentileMean()
    private.StatTableClose()
    return price
end
```

## Conclusion

This new module is best built as a `StatHistogram` variant with a robust weighted-percentile engine. The final algorithm is:
- keep almost all data,
- robustly reject only extreme outliers,
- compute percentile weight for every price,
- produce a weighted mean centered on 10–25 percentile prices.

If you want, I can also add a second document that explicitly compares this method against `Stat-Simple`, `Stat-StdDev`, and `Stat-Histogram` in terms of bias and robustness.
