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

If the repo requires authentication or depends on private access, the command will skip it and continue.

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

## Scan-related Lua files

These Lua files include the word `scan` and are likely the most relevant to Auctioneer scanning and rope generation.

- `Documentation/References/CoreScan.lua`
- `Documentation/References/auc-advanced/CoreAPI.lua`
- `Documentation/References/auc-advanced/CoreBuy.lua`
- `Documentation/References/auc-advanced/CoreConfig.lua`
- `Documentation/References/auc-advanced/CoreConst.lua`
- `Documentation/References/auc-advanced/CoreFinal.lua`
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
