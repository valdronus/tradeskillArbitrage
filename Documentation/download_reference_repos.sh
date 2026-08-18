#!/usr/bin/env bash
set -euo pipefail

# download_reference_repos.sh
# --------------------------
# Purpose:
#   Recreate the `Documentation/References/*` directory tree by cloning the
#   original upstream Git repositories for each referenced World of Warcraft
#   addon or project.
#
# Why this exists:
#   The `Documentation/References` folder contains many external repos that were
#   downloaded as standalone git checkouts. Removing them saves space, and this
#   script makes it easy to restore them later from their original sources.
#
# How to run:
#   cd /workspaces/codespaces-blank
#   ./download_reference_repos.sh
#
# What it does:
#   1. Ensures `Documentation/References` exists.
#   2. Deletes any existing checkout for each repository.
#   3. Clones the repository fresh from the known origin URL.
#
# Notes:
#   - This script uses shallow clones (`--depth 1`) to conserve disk space while
#     preserving the ability to fetch updates later if needed.
#   - Each repo is restored with its own `.git` metadata so it remains a proper
#     git checkout.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REF_DIR="$ROOT_DIR/Documentation/References"

mkdir -p "$REF_DIR"

declare -a REPO_NAMES=(
  Altoholic
  TradeSkillInfo
  TrinityCore-3.3.5
  aowow
  auc-advanced
  auc-db
  auc-filter-basic
  auc-filter-flatoutlier
  auc-filter-outlier
  auc-match-undercut
  auc-scandata
  auc-stat-debug
  auc-stat-histogram
  auc-stat-ilevel
  auc-stat-now
  auc-stat-purchased
  auc-stat-sales
  auc-stat-simple
  auc-stat-stddev
  auc-stat-wowecon
  auc-util-ahwindowcontrol
  auc-util-appraiser
  auc-util-askprice
  auc-util-automagic
  auc-util-compactui
  auc-util-easybuyout
  auc-util-fixah
  auc-util-glypher
  auc-util-glypherpost
  auc-util-itemsuggest
  auc-util-pricelevel
  auc-util-scanbutton
  auc-util-scanfinish
  auc-util-scanprogress
  auc-util-scanstart
  auc-util-searchui
  auc-util-simpleauction
  auc-util-vendmarkup
  auctioneer
  auctioneer_stats_overtime
  auctioneer_util_dealfinder
  auctioneer_util_valuer
  beancounter
  database-wotlk
  enchantrix-barker
  enchantrix
  informant
  wow-classic-items
)

declare -A REPO_URLS=(
  [Altoholic]="https://github.com/Wrath-AddOns/Altoholic"
  [TradeSkillInfo]="https://github.com/Ravendwyr/TradeSkillInfo"
  [TrinityCore-3.3.5]="https://github.com/TrinityCore/TrinityCore.git"
  [aowow]="https://github.com/Sarjuuk/aowow"
  [auc-advanced]="https://gitlab.com/norganna-wow/auctioneer/auc-advanced"
  [auc-db]="https://gitlab.com/norganna-wow/auctioneer/auc-db"
  [auc-filter-basic]="https://gitlab.com/norganna-wow/auctioneer/auc-filter-basic"
  [auc-filter-flatoutlier]="https://gitlab.com/norganna-wow/auctioneer/auc-filter-flatoutlier"
  [auc-filter-outlier]="https://gitlab.com/norganna-wow/auctioneer/auc-filter-outlier"
  [auc-match-undercut]="https://gitlab.com/norganna-wow/auctioneer/auc-match-undercut"
  [auc-scandata]="https://gitlab.com/norganna-wow/auctioneer/auc-scandata"
  [auc-stat-debug]="https://gitlab.com/norganna-wow/auctioneer/auc-stat-debug"
  [auc-stat-histogram]="https://gitlab.com/norganna-wow/auctioneer/auc-stat-histogram"
  [auc-stat-ilevel]="https://gitlab.com/norganna-wow/auctioneer/auc-stat-ilevel"
  [auc-stat-now]="https://gitlab.com/norganna-wow/auctioneer/auc-stat-now"
  [auc-stat-purchased]="https://gitlab.com/norganna-wow/auctioneer/auc-stat-purchased"
  [auc-stat-sales]="https://gitlab.com/norganna-wow/auctioneer/auc-stat-sales"
  [auc-stat-simple]="https://gitlab.com/norganna-wow/auctioneer/auc-stat-simple"
  [auc-stat-stddev]="https://gitlab.com/norganna-wow/auctioneer/auc-stat-stddev"
  [auc-stat-wowecon]="https://gitlab.com/norganna-wow/auctioneer/auc-stat-wowecon"
  [auc-util-ahwindowcontrol]="https://gitlab.com/norganna-wow/auctioneer/auc-util-ahwindowcontrol"
  [auc-util-appraiser]="https://gitlab.com/norganna-wow/auctioneer/auc-util-appraiser"
  [auc-util-askprice]="https://gitlab.com/norganna-wow/auctioneer/auc-util-askprice"
  [auc-util-automagic]="https://gitlab.com/norganna-wow/auctioneer/auc-util-automagic"
  [auc-util-compactui]="https://gitlab.com/norganna-wow/auctioneer/auc-util-compactui"
  [auc-util-easybuyout]="https://gitlab.com/norganna-wow/auctioneer/auc-util-easybuyout"
  [auc-util-fixah]="https://gitlab.com/norganna-wow/auctioneer/auc-util-fixah"
  [auc-util-glypher]="https://gitlab.com/norganna-wow/auctioneer/auc-util-glypher"
  [auc-util-glypherpost]="https://gitlab.com/norganna-wow/auctioneer/auc-util-glypherpost"
  [auc-util-itemsuggest]="https://gitlab.com/norganna-wow/auctioneer/auc-util-itemsuggest"
  [auc-util-pricelevel]="https://gitlab.com/norganna-wow/auctioneer/auc-util-pricelevel"
  [auc-util-scanbutton]="https://gitlab.com/norganna-wow/auctioneer/auc-util-scanbutton"
  [auc-util-scanfinish]="https://gitlab.com/norganna-wow/auctioneer/auc-util-scanfinish"
  [auc-util-scanprogress]="https://gitlab.com/norganna-wow/auctioneer/auc-util-scanprogress"
  [auc-util-scanstart]="https://gitlab.com/norganna-wow/auctioneer/auc-util-scanstart"
  [auc-util-searchui]="https://gitlab.com/norganna-wow/auctioneer/auc-util-searchui"
  [auc-util-simpleauction]="https://gitlab.com/norganna-wow/auctioneer/auc-util-simpleauction"
  [auc-util-vendmarkup]="https://gitlab.com/norganna-wow/auctioneer/auc-util-vendmarkup"
  [auctioneer]="https://gitlab.com/norganna-wow/auctioneer/auctioneer"
  [auctioneer_stats_overtime]="https://gitlab.com/norganna-wow/auctioneer/auctioneer_stats_overtime"
  [auctioneer_util_dealfinder]="https://gitlab.com/norganna-wow/auctioneer/auctioneer_util_dealfinder"
  [auctioneer_util_valuer]="https://gitlab.com/norganna-wow/auctioneer/auctioneer_util_valuer"
  [beancounter]="https://gitlab.com/norganna-wow/auctioneer/beancounter"
  [database-wotlk]="https://github.com/azerothcore/database-wotlk.git"
  [enchantrix-barker]="https://gitlab.com/norganna-wow/auctioneer/enchantrix-barker"
  [enchantrix]="https://gitlab.com/norganna-wow/auctioneer/enchantrix"
  [informant]="https://gitlab.com/norganna-wow/auctioneer/informant"
  [wow-classic-items]="https://github.com/nexus-devs/wow-classic-items.git"
)

declare -A REPO_DESCRIPTIONS=(
  [Altoholic]="A popular Wrath of the Lich King addon for managing inventories, mail, auctions, and alts in one place."
  [TradeSkillInfo]="A crafting addon that tracks reagent costs, profits, and vendor value for tradeskill recipes."
  [TrinityCore-3.3.5]="A server emulator for WoW WotLK used for reference and compatibility research."
  [aowow]="A WoW database browser project for viewing item, spell, and NPC data."
  [auc-advanced]="Auctioneer module for advanced auction house pricing and statistical analysis."
  [auc-db]="Auctioneer database utilities for managing saved scan and pricing data."
  [auc-filter-basic]="Basic Auctioneer filter plugin for automatically filtering auction search results."
  [auc-filter-flatoutlier]="Auctioneer filter plugin that removes flat outlier auction prices."
  [auc-filter-outlier]="Auctioneer filter plugin that removes statistically anomalous auction events."
  [auc-match-undercut]="Auctioneer module for finding matching auctions and undercut opportunities."
  [auc-scandata]="Auctioneer scan data handler, stores and retrieves auction house scan information."
  [auc-stat-debug]="Auctioneer statistic module for debugging auction house pricing calculations."
  [auc-stat-histogram]="Auctioneer statistic module that builds price histograms from scan data."
  [auc-stat-ilevel]="Auctioneer statistic module for item level-based pricing."
  [auc-stat-now]="Auctioneer module that reports current auction house pricing."
  [auc-stat-purchased]="Auctioneer statistic module tracking purchased item pricing."
  [auc-stat-sales]="Auctioneer statistic module tracking auction sales price history."
  [auc-stat-simple]="Auctioneer module providing simple average pricing metrics."
  [auc-stat-stddev]="Auctioneer statistic module using standard deviation to detect price variance."
  [auc-stat-wowecon]="Auctioneer statistic module that integrates with WoWconomy pricing data."
  [auc-util-ahwindowcontrol]="Auctioneer utility for controlling the auction house window behavior."
  [auc-util-appraiser]="Auctioneer valuation utility for quickly appraising auction items."
  [auc-util-askprice]="Auctioneer utility to manage ask price targets for auctions."
  [auc-util-automagic]="Auctioneer utility that automates selected auction workflows."
  [auc-util-compactui]="Auctioneer UI enhancement for a compact auction interface."
  [auc-util-easybuyout]="Auctioneer utility to make buying out auctions fast and easy."
  [auc-util-fixah]="Auctioneer utility for repairing or optimizing auction house data."
  [auc-util-glypher]="Auctioneer utility related to glyph pricing and selling."
  [auc-util-glypherpost]="Auctioneer utility for posting glyph auctions."
  [auc-util-itemsuggest]="Auctioneer utility that suggests items to buy or sell."
  [auc-util-pricelevel]="Auctioneer utility that calculates price levels for auctions."
  [auc-util-scanbutton]="Auctioneer UI utility that adds a dedicated scan button."
  [auc-util-scanfinish]="Auctioneer utility that acts when a scan completes."
  [auc-util-scanprogress]="Auctioneer utility tracking progress during auction scans."
  [auc-util-scanstart]="Auctioneer utility that triggers actions when scanning starts."
  [auc-util-searchui]="Auctioneer UI module that improves auction search functionality."
  [auc-util-simpleauction]="Auctioneer utility for simple auction posting workflows."
  [auc-util-vendmarkup]="Auctioneer utility that calculates vendor markup and sell-back values."
  [auctioneer]="The core Auctioneer addon providing auction house analysis, posting, and scanning."
  [auctioneer_stats_overtime]="Auctioneer module tracking auction statistics over time for trend analysis."
  [auctioneer_util_dealfinder]="Auctioneer utility to find profitable deals and bargains."
  [auctioneer_util_valuer]="Auctioneer utility used to value items based on auction and vendor data."
  [beancounter]="Addon that tracks purchase costs, vendor prices, and historical transaction values."
  [database-wotlk]="AzerothCore database definitions for World of Warcraft Wrath of the Lich King."
  [enchantrix-barker]="Enchantrix companion module for pricing and buying/selling enchants."
  [enchantrix]="Addon that estimates disenchant, vendor, and auction values for items."
  [informant]="Addon that displays vendor buy/sell prices and useful item pricing info."
  [wow-classic-items]="A community-maintained Classic item database for WoW Classic data lookup."
)

rm -rf "$REF_DIR"
mkdir -p "$REF_DIR"

echo "Recreating Documentation/References from upstream repositories..."

for repo_name in "${REPO_NAMES[@]}"; do
  repo_url="${REPO_URLS[$repo_name]}"
  repo_description="${REPO_DESCRIPTIONS[$repo_name]}"
  dest_dir="$REF_DIR/$repo_name"

  echo
  echo "=============================================="
  echo "Repository: $repo_name"
  echo "URL:        $repo_url"
  echo "Purpose:    $repo_description"
  echo "Destination:$dest_dir"
  echo "=============================================="

  git clone --depth 1 "$repo_url" "$dest_dir"

done

echo
 echo "All reference repos have been cloned into: $REF_DIR"
 echo "If you need to refresh an individual repo later, run this script again."
