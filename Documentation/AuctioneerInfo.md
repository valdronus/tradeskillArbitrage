# AuctioneerInfo

## Download commands

Use this shell snippet to clone the Auctioneer subrepos into `Documentation/References` from scratch.

```bash
mkdir -p Documentation/References
cd Documentation/References
for repo in \
  auc-advanced \
  auc-db \
  auc-filter-basic \
  auc-filter-flatoutlier \
  auc-filter-outlier \
  auc-match-undercut \
  auc-scandata \
  auc-stat-debug \
  auc-stat-histogram \
  auc-stat-ilevel \
  auc-stat-now \
  auc-stat-purchased \
  auc-stat-sales \
  auc-stat-simple \
  auc-stat-stddev \
  auc-stat-wowecon \
  auc-util-ahwindowcontrol \
  auc-util-appraiser \
  auc-util-askprice \
  auc-util-automagic \
  auc-util-compactui \
  auc-util-easybuyout \
  auc-util-fixah \
  auc-util-glypher \
  auc-util-glypherpost \
  auc-util-itemsuggest \
  auc-util-pricelevel \
  auc-util-scanbutton \
  auc-util-scanfinish \
  auc-util-scanprogress \
  auc-util-scanstart \
  auc-util-searchui \
  auc-util-simpleauction \
  auc-util-vendmarkup \
  auctioneer \
  auctioneer_stats_overtime \
  auctioneer_util_dealfinder \
  auctioneer_util_valuer \
  beancounter \
  enchantrix \
  enchantrix-barker \
  informant
 do
  if [ -d "$repo" ]; then
    echo "SKIP $repo"
    continue
  fi
  git clone --depth 1 "https://gitlab.com/norganna-wow/auctioneer/$repo" "$repo" || echo "SKIP $repo"
done
```

## Pricing metrics and SavedVariables extraction

The values below are expressed in copper unless stated otherwise. Auctioneer
does not store one universal `market_price` field. Most statistic modules store
their own history, expose a price array or probability-density function at
runtime, and let Auctioneer combine those results. The source line numbers
refer to the current files in `Documentation/References`.

### Market Price

**Sources:** [`auc-advanced/CoreAPI.lua`](References/auc-advanced/CoreAPI.lua#L104-L121)
documents `GetMarketValue(itemLink, serverKey, confidence)` and its default
confidence of `0.5`. Module discovery and PDF collection are at

and the numerical percentile search is at
[`auc-advanced/CoreAPI.lua`](References/auc-advanced/CoreAPI.lua#L190-L257).
The tooltip display is in [`auc-advanced/CoreMain.lua`](References/auc-advanced/CoreMain.lua#L108-L121).

Each enabled Stat module supplies a PDF $f_i(x)$, bounds, and an area/weight
$a_i$. Auctioneer searches for $m$ such that:

$$
\int_{-\infty}^{m} \sum_i a_i f_i(x)\,dx
= c \int_{-\infty}^{\infty} \sum_i a_i f_i(x)\,dx
$$

where $c=0.5$ by default. The implementation approximates the integral on a
grid, reduces the grid step by `0.8`, and rounds the midpoint to copper. There
is no persisted Market Price scalar: Python must load the enabled modules and
reproduce their PDFs, or document a proxy such as Histogram median followed by
Simple mean. A combined result is not necessarily equal to either module's
central price because the PDFs are weighted together.

### Vend Markup

**Sources:** [`auc-util-vendmarkup/vendMarkup.lua`](References/auc-util-vendmarkup/vendMarkup.lua#L39-L51)
calls `GetSellValue(itemId)` and multiplies it by the configured percentage;
the default `300` percent is set at
[`auc-util-vendmarkup/vendMarkup.lua`](References/auc-util-vendmarkup/vendMarkup.lua#L78-L82).

$$
\mathrm{VendMarkup}(item)=\mathrm{VendorSellPrice}(item)
	imes \frac{\mathrm{multiplier}}{100}
$$

With a vendor value of `2g` and the default multiplier, the result is
`2g * 300 / 100 = 6g`.

The multiplier is the `util.vendmarkup.multiplier` setting, normally persisted
in the active profile inside `AucAdvancedConfig`; it is not an item-history
table. Vendor sell values come from the WoW client/API item data. A Python
extractor should parse the config setting and combine it with an item database
or Informant vendor value. Auctioneer SavedVariables alone are insufficient.

### Simple

**Sources:** [`auc-stat-simple/Auc-Stat-Simple.toc`](References/auc-stat-simple/Auc-Stat-Simple.toc#L8-L9)
declares `AucAdvancedStatSimpleData`. Scan recording is in
[`auc-stat-simple/StatSimple.lua`](References/auc-stat-simple/StatSimple.lua#L102-L157),
price calculation in [`auc-stat-simple/StatSimple.lua`](References/auc-stat-simple/StatSimple.lua#L163-L226),
and the public array/PDF in [`auc-stat-simple/StatSimple.lua`](References/auc-stat-simple/StatSimple.lua#L276-L360).
The compact readers are at [`auc-stat-simple/StatSimple.lua`](References/auc-stat-simple/StatSimple.lua#L739-L769),
and EMA persistence is at [`auc-stat-simple/StatSimple.lua`](References/auc-stat-simple/StatSimple.lua#L627-L727).

```text
AucAdvancedStatSimpleData.RealmData[server].daily[itemID] =
  "0@totalBuyout;seenCount;minBuyout;auctionCount"
AucAdvancedStatSimpleData.RealmData[server].means[itemID] =
  "0@seenDays;seenCount;EMA3;EMA7;EMA14;averageMinBuyout;auctionCount"
```

Properties are joined with `_` and separated from their values by `@`. The
fields use `;`; property `0` is the combined/base item. Prices are unit prices
because scan recording divides stack buyouts by stack size.

For a day with total buyout $T$ and seen quantity $N$:

$$\mathrm{DailyAverage}=T/N$$

After the first three days, the moving averages are:

$$EMA_3'=(2EMA_3+DailyAverage)/3$$

$$EMA_7'=(6EMA_7+DailyAverage)/7,\qquad EMA_{14}'=(13EMA_{14}+DailyAverage)/14$$

`GetPrice()` combines the daily value and available EMAs into Simple's mean,
then `GetItemPDF()` creates a bell curve around that mean. A Python extractor
should parse the assignment-style Lua, select `RealmData[server]`, split on
`_`, `@`, and `;`, reproduce the weighting in `GetPrice()`, and optionally
reproduce the bell curve. For example, `0@100000;2;40000` means a daily average
of `50000` copper, or `5g`.

### Histogram

**Sources:** [`auc-stat-histogram/Auc-Stat-Histogram.toc`](References/auc-stat-histogram/Auc-Stat-Histogram.toc#L8-L9)
declares `AucAdvancedStatHistogramData`. Delimiters are defined at
[`auc-stat-histogram/StatHistogram.lua`](References/auc-stat-histogram/StatHistogram.lua#L45-L65);
median calculation is at [`auc-stat-histogram/StatHistogram.lua`](References/auc-stat-histogram/StatHistogram.lua#L98-L158);
the public median/PDF methods are at [`auc-stat-histogram/StatHistogram.lua`](References/auc-stat-histogram/StatHistogram.lua#L169-L278);
and bucket recording is at [`auc-stat-histogram/StatHistogram.lua`](References/auc-stat-histogram/StatHistogram.lua#L295-L352).

Conceptually, an item record is:

```text
AucAdvancedStatHistogramData.RealmData[server][itemID] =
  "0@minIndex;maxIndex!step!count!bucket0;bucket1;..."
```

The source packer/unpacker at
[`auc-stat-histogram/StatHistogram.lua`](References/auc-stat-histogram/StatHistogram.lua#L650-L735)
is authoritative for legacy variants. Properties use `@`, multiple properties
use `_`, the control/data sections use `!`, and the bucket sequence uses `;`.
For bucket index $j$:

$$price_j=j\times step$$

The median is the first bucket whose cumulative count reaches
If the repo requires authentication or depends on private access, the command will skip it and continue.
median is about `300` copper (`3s`). Python should reconstruct the buckets and
walk cumulative counts to obtain this median. It is a strong Histogram proxy,
but exact Market Price uses Histogram's PDF rather than merely this median.

### Sales

**Sources:** [`auc-stat-sales/BeanCount.lua`](References/auc-stat-sales/BeanCount.lua#L174-L305)
queries BeanCounter and calculates sold/bought averages, variance, and a
filtered average; [`auc-stat-sales/BeanCount.lua`](References/auc-stat-sales/BeanCount.lua#L312-L345)
exposes `array.price`; and [`auc-stat-sales/BeanCount.lua`](References/auc-stat-sales/BeanCount.lua#L347-L405)
shows that Sales persists only ignore timestamps. BeanCounter's database shape
is initialized at [`beancounter/BeanCounter.lua`](References/beancounter/BeanCounter.lua#L166-L184)
and item transaction lookup is shown at
[`beancounter/BeanCounterAPI.lua`](References/beancounter/BeanCounterAPI.lua#L243-L267).

For successful sales with unit price $p_i$ and quantity $q_i$:

$$\mathrm{SoldMean}=\frac{\sum_i p_iq_i}{\sum_i q_i}$$

Sales computes population variance $\sigma^2=\sum_i(\bar p-p_i)^2/n$,
keeps $|p_i-\bar p|<1.5\sigma$, and returns:

$$\mathrm{NormalizedAverage}=\frac{\sum_{filtered}p_iq_i}{\sum_{filtered}q_i}$$

`array.price` is `average or mean`; its PDF is a bell curve centered on the
filtered average. Raw history is in `BeanCounterDB[realm][player]`, especially
completed-auction tables whose transaction strings are semicolon-delimited.
The Sales setting `stat.sales.ignoredsigs` contains per-signature timestamps in
Auctioneer profile/config data. Python should parse each player's transactions,
identify successful sales, quantity, unit price, and timestamp, apply the
ignore timestamp, then apply the formulas. There is no
`AucAdvancedStatSalesData` price file. Ten items sold at `8g` and five at `10g`
give `(80g + 50g) / 15 = 8g 66s 66c` before filtering.

### StdDev

**Sources:** [`auc-stat-stddev/Auc-Stat-StdDev.toc`](References/auc-stat-stddev/Auc-Stat-StdDev.toc#L8-L9)
declares `AucAdvancedStatStdDevData`. Recording is at
[`auc-stat-stddev/StatStdDev.lua`](References/auc-stat-stddev/StatStdDev.lua#L128-L153),
calculation at [`auc-stat-stddev/StatStdDev.lua`](References/auc-stat-stddev/StatStdDev.lua#L225-L293),
public output at [`auc-stat-stddev/StatStdDev.lua`](References/auc-stat-stddev/StatStdDev.lua#L300-L325),
and persistence encoding at [`auc-stat-stddev/StatStdDev.lua`](References/auc-stat-stddev/StatStdDev.lua#L456-L474).

```text
AucAdvancedStatStdDevData.RealmData[server][itemID] =
  "property:buyout1,buyout2/stack2,buyout3,..."
```

Properties are comma-separated, fields use `:`, and a stacked observation is
`totalBuyout/stackSize`. For unit prices $x_i=p_i/q_i$:

$$\bar x=\frac{\sum_i p_i}{\sum_i q_i},\qquad
\sigma=\sqrt{\frac{1}{n}\sum_i(\bar x-x_i)^2}$$

The filtered value is:

$$P=\frac{\sum_{|x_i-\bar x|<1.5\sigma}p_i}
{\sum_{|x_i-\bar x|<1.5\sigma}q_i}$$

`array.price` is `P` or the unfiltered mean. Python should parse the packed
records, normalize stack prices, calculate population standard deviation, apply
the `1.5 * sigma` cutoff, and return the weighted filtered average. A distant
`100g` observation among `10g` and `11g` observations is a typical candidate
for removal.

### Purchased

**Sources:** [`auc-stat-purchased/Auc-Stat-Purchased.toc`](References/auc-stat-purchased/Auc-Stat-Purchased.toc#L8-L9)
declares `AucAdvancedStatPurchasedData`. Early-deletion purchase detection and
daily totals are at [`auc-stat-purchased/StatPurchased.lua`](References/auc-stat-purchased/StatPurchased.lua#L137-L175);
mean/stddev estimation is at [`auc-stat-purchased/StatPurchased.lua`](References/auc-stat-purchased/StatPurchased.lua#L177-L225);
reads and output are at [`auc-stat-purchased/StatPurchased.lua`](References/auc-stat-purchased/StatPurchased.lua#L289-L374);
and EMA archiving is at [`auc-stat-purchased/StatPurchased.lua`](References/auc-stat-purchased/StatPurchased.lua#L499-L577).

```text
AucAdvancedStatPurchasedData.RealmData[server].daily[itemID] =
  "property:dailyTotal;dailyCount"
AucAdvancedStatPurchasedData.RealmData[server].means[itemID] =
  "property:seenDays;seenCount;EMA3;EMA7;EMA14"
```

An auction deleted before its expected expiry is treated as bought. The daily
price is $dailyTotal/dailyCount$. Archive updates are:

$$EMA_3'=(2EMA_3+DailyAverage)/3,\quad
EMA_7'=(6EMA_7+DailyAverage)/7,\quad
EMA_{14}'=(13EMA_{14}+DailyAverage)/14$$

Normal mode reports EMA3 or daily average. Safe mode mixes EMA3/EMA7/EMA14
according to `seenCount` and `seenDays` in `GetPriceArray()`. Python should
parse the comma/colon/semicolon format, calculate the daily average, and either
select EMA3 or reproduce the safe-mode branch. This module uses inferred early
auction purchases, not raw scan history.

### Fixed Price

**Sources:** [`auc-util-appraiser/AprSettings.lua`](References/auc-util-appraiser/AprSettings.lua#L42-L61)
adds `fixed` as an Appraiser model; selection and reads are in
[`auc-util-appraiser/Appraiser.lua`](References/auc-util-appraiser/Appraiser.lua#L315-L347);
user writes are in [`auc-util-appraiser/AprFrame.lua`](References/auc-util-appraiser/AprFrame.lua#L867-L917);
and the config SavedVariables declaration is in
[`auc-advanced/Auc-Advanced.toc`](References/auc-advanced/Auc-Advanced.toc#L7-L10).

Fixed Price is not a statistical metric:

$$\mathrm{FixedBuyout}(item)=B_{item},\qquad
\mathrm{FixedBid}(item)=D_{item}$$

where the values are explicitly entered by the user. The profile keys have the
shape:

```text
util.appraiser.item.<item-signature>.model = "fixed"
util.appraiser.item.<item-signature>.fixed.buy = <copper>
util.appraiser.item.<item-signature>.fixed.bid = <copper>
```

For a fixed buyout of `12g` and bid of `10g`, Appraiser uses exactly those
values and does not consult Market Price for that item. A Python extractor
should recursively locate `util.appraiser.item.*` keys in the active
`AucAdvancedConfig` profile, verify `model == "fixed"`, and read `.fixed.buy`
and `.fixed.bid`. The signature can encode suffixes or bonuses, so it should
not always be matched to an item by numeric ID alone.

## Repository layout summary

All repositories are cloned under `Documentation/References/`.

### Key repos and their purpose

- `auc-advanced`
  - Main Auctioneer advanced engine modules.
  - Contains the scan engine, query API, config, and support code.
  - Key files: `CoreScan.lua`, `CoreAPI.lua`, `CoreMain.lua`, `CoreConfig.lua`, `CoreConst.lua`, `CoreResources.lua`, `CorePost.lua`, `CoreUtil.lua`, `DataBonusIDs.lua`.
  - Additional subfolders: `Libs`, `Modules`, `Textures`, `includes`.

- `auc-db`
  - Auctioneer database layer.
  - Key files: `DbCore.lua`, `DbMain.lua`, `DbStrings.lua`, `Support/StringRope.lua`.

- `auc-scandata`
  - Scan data persistence and helper functions.
  - Key files: `ScanData.lua`, `StringRope.lua`.

- `auctioneer`
  - The core Auctioneer addon wrapper.
  - Key files: `Main.lua`, `GUI.lua`, `Items.lua`, `Handlers.lua`, `Statistics.lua`, `Const.lua`, `Internal.lua`, `Register.lua`.

- `beancounter`
  - Auctioneer accounting/transaction tracker.
  - Key files: `BeanCounter.lua`, `BeanCounterConfig.lua`, `BeanCounterMail.lua`, `PostMonitor.lua`, `MatchBeanCount.lua`.

- `enchantrix`
  - Enchantrix addon integration and item/tooltip utilities.
  - Key files: `EnxMain.lua`, `EnxUtil.lua`, `EnxTooltip.lua`, `EnxAutoDisenchant.lua`.

- `informant`
  - Informant data module for item pricing and economy info.
  - Key files: `InfMain.lua`, `InfTooltip.lua`, `InfData.lua`, `InfSettings.lua`, `InfStrings.lua`.

### Utility & stat modules

These repos are smaller Auctioneer modules, usually one or a few Lua source files plus a `.toc` and `Embed.xml`.

- Scan-related utilities:
  - `auc-util-scanbutton`: `ScanButton.lua`
  - `auc-util-scanfinish`: `ScanFinish.lua`
  - `auc-util-scanprogress`: `ScanProgress.lua`
  - `auc-util-scanstart`: `ScanStart.lua`
  - `auc-util-searchui`: search UI modules and searchers
  - `auc-util-simpleauction`: `AucSimple.lua`, `SimpFrame.lua`

- Other utilities:
  - `auc-util-appraiser`
  - `auc-util-askprice`
  - `auc-util-automagic`
  - `auc-util-compactui`
  - `auc-util-easybuyout`
  - `auc-util-fixah`
  - `auc-util-glypher`
  - `auc-util-glypherpost`
  - `auc-util-itemsuggest`
  - `auc-util-pricelevel`
  - `auc-util-vendmarkup`

- Stat modules:
  - `auc-stat-debug`
  - `auc-stat-histogram`
  - `auc-stat-ilevel`
  - `auc-stat-now`
  - `auc-stat-purchased`
  - `auc-stat-sales`
  - `auc-stat-simple`
  - `auc-stat-stddev`
  - `auc-stat-wowecon`

## Recommended module set for scan/stat analysis

For this project, the most important repositories are the scan core and stat modules. The following subset is enough to understand the Auctioneer scan/stat workflow:

### Necessary modules

- `auc-advanced`
- `auc-scandata`
- `auc-db`
- `auc-filter-basic`
- `auc-filter-flatoutlier`
- `auc-filter-outlier`
- `auc-match-undercut`
- `auc-stat-debug`
- `auc-stat-histogram`
- `auc-stat-ilevel`
- `auc-stat-now`
- `auc-stat-purchased`
- `auc-stat-sales`
- `auc-stat-simple`
- `auc-stat-stddev`
- `auc-stat-wowecon`

### Optional extra modules

These additional repos are useful for broader Auctioneer integration, but are not required for the core scan/stat analysis:

- `auctioneer`
- `auc-util-searchui`
- `auc-util-scanbutton`
- `auc-util-scanstart`
- `auc-util-scanprogress`
- `auc-util-scanfinish`
- `auc-util-simpleauction`
- `auc-util-compactui`
- `auc-util-itemsuggest`
- `auc-util-easybuyout`
- `auc-util-pricelevel`
- `auc-util-vendmarkup`
- `auc-util-fixah`
- `auc-util-automagic`
- `auc-util-appraiser`
- `auc-util-glypher`
- `auc-util-glypherpost`
- `auctioneer_stats_overtime`
- `auctioneer_util_valuer`
- `auctioneer_util_dealfinder`
- `enchantrix`
- `enchantrix-barker`
- `beancounter`
- `informant`

- `Documentation/References/auc-advanced/CoreMain.lua`
- `Documentation/References/auc-advanced/CoreManifest.lua`
- `Documentation/References/auc-advanced/CoreModule.lua`
- `Documentation/References/auc-advanced/CorePost.lua`
- `Documentation/References/auc-advanced/CoreResources.lua`
- `Documentation/References/auc-advanced/CoreScan.lua`
- `Documentation/References/auc-advanced/CoreServers.lua`
- `Documentation/References/auc-advanced/CoreSettings.lua`
- `Documentation/References/auc-advanced/CoreStrings.lua`
- `Documentation/References/auc-advanced/CoreUtil.lua`
- `Documentation/References/auc-advanced/DataBonusIDs.lua`
- `Documentation/References/auc-advanced/Modules/Auc-Stat-Example2/StatExample2.lua`
- `Documentation/References/auc-advanced/Modules/Auc-Util-Example/Example.lua`
- `Documentation/References/auc-db/DbCore.lua`
- `Documentation/References/auc-db/DbMain.lua`
- `Documentation/References/auc-filter-basic/BasicFilter.lua`
- `Documentation/References/auc-filter-flatoutlier/FlatOutlier.lua`
- `Documentation/References/auc-filter-outlier/OutlierFilter.lua`
- `Documentation/References/auc-match-undercut/Undercut.lua`
- `Documentation/References/auc-scandata/ScanData.lua`
- `Documentation/References/auc-scandata/StringRope.lua`
- `Documentation/References/auc-stat-debug/StatDebug.lua`
- `Documentation/References/auc-stat-histogram/StatHistogram.lua`
- `Documentation/References/auc-stat-ilevel/iLevel.lua`
- `Documentation/References/auc-stat-now/Auc-Stat-Now.lua`
- `Documentation/References/auc-stat-purchased/StatPurchased.lua`
- `Documentation/References/auc-stat-simple/StatSimple.lua`
- `Documentation/References/auc-stat-stddev/StatStdDev.lua`
- `Documentation/References/auc-util-appraiser/Appraiser.lua`
- `Documentation/References/auc-util-appraiser/AprFrame.lua`
- `Documentation/References/auc-util-appraiser/AprSettings.lua`
- `Documentation/References/auc-util-automagic/Core.lua`
- `Documentation/References/auc-util-automagic/Mail-GUI.lua`
- `Documentation/References/auc-util-compactui/CompactUI.lua`
- `Documentation/References/auc-util-fixah/PageOneReturn.lua`
- `Documentation/References/auc-util-glypher/Glypher.lua`
- `Documentation/References/auc-util-glypherpost/GlypherPost.lua`
- `Documentation/References/auc-util-itemsuggest/Auc-Util-ItemSuggest.lua`
- `Documentation/References/auc-util-scanbutton/ScanButton.lua`
- `Documentation/References/auc-util-scanfinish/ScanFinish.lua`
- `Documentation/References/auc-util-scanprogress/ScanProgress.lua`
- `Documentation/References/auc-util-scanstart/ScanStart.lua`
- `Documentation/References/auc-util-searchui/FilterItemAuctionHistory.lua`
- `Documentation/References/auc-util-searchui/SearchMain.lua`
- `Documentation/References/auc-util-searchui/SearchRealTime.lua`
- `Documentation/References/auc-util-searchui/SearcherGeneral.lua`
- `Documentation/References/auc-util-searchui/SearcherSnatch.lua`
- `Documentation/References/auc-util-simpleauction/AucSimple.lua`
- `Documentation/References/auc-util-simpleauction/SimpFrame.lua`
- `Documentation/References/auctioneer/Modules/Scanner/Scanner.lua`
- `Documentation/References/auctioneer/Statistics.lua`
- `Documentation/References/beancounter/BeanCounterConfig.lua`
- `Documentation/References/beancounter/BeanCounterMail.lua`
- `Documentation/References/beancounter/BeanCounterStrings.lua`
- `Documentation/References/beancounter/MatchBeanCount.lua`
- `Documentation/References/beancounter/PostMonitor.lua`
- `Documentation/References/enchantrix/EnxAutoDisenchant.lua`
- `Documentation/References/enchantrix/EnxSettings.lua`
- `Documentation/References/enchantrix/EnxStrings.lua`
- `Documentation/References/enchantrix/EnxUtil.lua`
- `Documentation/References/informant/Data/InfData.lua`
- `Documentation/References/informant/Data/InfQuests.lua`
- `Documentation/References/informant/InfMain.lua`
- `Documentation/References/informant/InfSettings.lua`
- `Documentation/References/informant/InfStrings.lua`

## Notes on scan and rope generation

- `CoreScan.lua` (both standalone `Documentation/References/CoreScan.lua` and `Documentation/References/auc-advanced/CoreScan.lua`) is the main scan engine.
- `auc-scandata/ScanData.lua` and `auc-scandata/StringRope.lua` contain the persistence layer for scan data and the rope string operations.
- `auc-advanced/CoreAPI.lua` exposes scan-related APIs, including `QueryImage`, `UnpackImageItem`, and scan statistics access.
- `auc-advanced/CoreConfig.lua` and `CoreSettings.lua` define scan settings and scan command handling.
- `auc-advanced/CoreMain.lua` and `CorePost.lua` integrate scanning into the addon lifecycle and post-scan processing.
- `auctioneer/Modules/Scanner/Scanner.lua` is the client-facing scanner module in the top-level `auctioneer` addon.
- `auc-util-scan*` repos contain UI and scan control helpers.

### What a rope is

- A "rope" is a serialized scan image stored as one or more Lua source chunks in `scandata.ropes`.
- `StringRope` is the helper used to build the chunked string efficiently, avoiding repeated concatenation.
- `ScanData.lua` writes `scandata.image = "rope"` and `scandata.ropes = { ... }` on save, then `Unpack()` later loads those chunks with `loadstring()`.
- In other words, the rope is the saved scan result encoded as executable Lua text, split into chunks for storage and later reconstruction.

## Practical entry points

- Start from `auc-advanced/CoreScan.lua` for the scan engine internals.
- Use `auc-scandata/ScanData.lua` to understand scan data storage and string encoding.
- Use `auc-scandata/StringRope.lua` for rope-specific string/serialization operations.
- Inspect `auc-advanced/CoreAPI.lua` for the public Auctioneer scan/scan data API.
- Check `auctioneer/Modules/Scanner/Scanner.lua` and `auctioneer/Statistics.lua` for addon integration.

## Repo file layout reference

The document above describes the most important repos and files. For each repository, start with its top-level Lua module files, then examine the `Embed.xml`/`.toc` files and any subfolders such as `Libs`, `Data`, or `Textures`.
