#!/usr/bin/env python3
"""Altoholic + Auctioneer sell recommendation skeleton.

This script is designed to load Altoholic DataStore saved-variable files
and Auctioneer AucScanData saved-variable files, then compute a CSV
sell recommendation list.

Because this environment does not contain real saved-variable data, this
script is intentionally a skeleton with detailed logging, heuristics, and
debug output so it can be run locally once the real files are provided.

Expected input:
  - Altoholic inventory / container saved-variable file(s); typically
    named like DataStore_Inventory.lua, DataStore_Containers.lua, or any
    Altoholic DataStore module file.
  - Auctioneer scan-data saved-variable file; typically AucScanData.lua.

The script performs:
  - Lua file parsing with slpp
  - nested table walking to find item links and related counts
  - Auctioneer scan data extraction from image/ropes structures
  - simple market-data heuristics based on available scan fields
  - CSV output sorted by recommendation priority
  - detailed logs written to a file for debugging

Default SavedVariables paths (WoW 3.3.5):
  World of Warcraft stores saved variables under:
    <WoWRoot>/WTF/Account/<AccountName>/SavedVariables/

  Common local roots:
    ~/Documents/World of Warcraft
    ~/Documents/WoW
    ~/World of Warcraft
    ~/Games/World of Warcraft
    ~/.wine/drive_c/Program Files/World of Warcraft
    ~/.wine/drive_c/Program Files (x86)/World of Warcraft

Example usage:
  python3 altoholic_auctioneer_sell_recommend.py \
    --wow-root "~/Documents/World of Warcraft" \
    --wow-account "YourAccount" \
    --output sell_recommend.csv \
    --log sell_recommend.log

  python3 altoholic_auctioneer_sell_recommend.py \
    --altoholic "/path/to/DataStore_Inventory.lua" \
    --auctioneer "/path/to/AucScanData.lua" \
    --output sell_recommend.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from slpp import slpp

LuaValue = Union[str, int, float, bool, None, Dict[str, Any], List[Any]]

ITEM_LINK_RGX = re.compile(r"\|H(item:[^|]+)\|h")
ITEM_NAME_RGX = re.compile(r"\[([^\]]+)\]")

SCAN_FIELD_NAMES = [
    "id",
    "link",
    "useLevel",
    "itemLevel",
    "itemType",
    "subType",
    "equipPos",
    "price",
    "timeLeft",
    "seenTime",
    "itemName",
    "texture",
    "stackSize",
    "quality",
    "canUse",
    "minBid",
    "curBid",
    "increment",
    "sellerName",
    "buyoutPrice",
    "amBidder",
    "dataFlag",
    "itemId",
    "itemSuffix",
    "itemFactor",
    "itemEnchant",
    "itemSeed",
]

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"

DEFAULT_WOW_SAVEDVARS_ROOTS = [
    Path.home() / "Documents" / "World of Warcraft",
    Path.home() / "Documents" / "WoW",
    Path.home() / "World of Warcraft",
    Path.home() / "Games" / "World of Warcraft",
    Path.home() / ".wine" / "drive_c" / "Program Files" / "World of Warcraft",
    Path.home() / ".wine" / "drive_c" / "Program Files (x86)" / "World of Warcraft",
]
DEFAULT_ALTOHOLIC_FILES = ["DataStore_Inventory.lua", "DataStore_Containers.lua", "DataStore_Characters.lua"]
DEFAULT_AUCTIONEER_FILES = ["AucScanData.lua", "Auc-ScanData.lua"]


@dataclass
class AltoItemRecord:
    source_path: str
    item_link: Optional[str]
    item_id: Optional[int]
    item_name: Optional[str]
    count: int = 1
    context: str = ""
    raw_value: Any = None
    notes: List[str] = field(default_factory=list)


@dataclass
class AuctioneerScanRow:
    server: Optional[str]
    faction: Optional[str]
    rope_id: Optional[int]
    row: Dict[str, Any]
    item_id: Optional[int] = None
    item_link: Optional[str] = None
    buyout_price: Optional[int] = None
    bid_price: Optional[int] = None
    quantity: Optional[int] = None
    seen_time: Optional[int] = None


@dataclass
class RecommendationRow:
    item_id: Optional[int]
    item_name: Optional[str]
    item_link: Optional[str]
    count: int
    best_buyout: Optional[int]
    best_bid: Optional[int]
    average_market: Optional[float]
    q1_price: Optional[float]
    q3_price: Optional[float]
    recommendation_score: Optional[float]
    excluded: bool
    source_context: str
    notes: str


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("sell_recommend")
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    logger.handlers = []
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def normalize_lua_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("return"):
        cleaned = cleaned[len("return") :].strip()
    cleaned = strip_lua_comments(cleaned)
    return cleaned


def strip_lua_comments(text: str) -> str:
    text = re.sub(r"--\[\[.*?\]\]", "", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", "", text)
    return text


def parse_lua_file(path: Path, logger: logging.Logger) -> LuaValue:
    logger.debug("Parsing Lua file: %s", path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        raise ValueError(f"Lua file is empty: {path}")
    try:
        parsed = slpp.decode(normalize_lua_text(text))
        logger.debug("Parsed Lua object type: %s", type(parsed).__name__)
        return parsed
    except Exception as exc:
        logger.exception("Failed to parse Lua file %s", path)
        raise RuntimeError(f"Could not parse Lua file '{path}': {exc}") from exc


def parse_item_link(value: str) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    link = None
    item_id = None
    item_name = None
    match = ITEM_LINK_RGX.search(value)
    if match:
        link = match.group(1)
    else:
        if value.startswith("item:"):
            link = value

    if link:
        item_name_match = ITEM_NAME_RGX.search(value)
        if item_name_match:
            item_name = item_name_match.group(1)
        try:
            tokens = link.split(":")
            if tokens and tokens[0] == "item" and tokens[1].isdigit():
                item_id = int(tokens[1])
        except Exception:
            pass
    return link, item_id, item_name


def load_lua_inputs(paths: Sequence[Path], logger: logging.Logger) -> List[Tuple[Path, LuaValue]]:
    loaded = []
    for path in paths:
        if not path.exists():
            logger.warning("Input path does not exist: %s", path)
            continue
        if path.is_dir():
            for child in sorted(path.glob("*.lua")):
                loaded.extend(load_lua_inputs([child], logger))
            continue
        loaded.append((path, parse_lua_file(path, logger)))
    return loaded


def is_item_link_string(value: str) -> bool:
    if ITEM_LINK_RGX.search(value):
        return True
    if value.startswith("item:") and ":" in value:
        return True
    return False


def collect_item_links(
    value: LuaValue,
    source_path: str,
    logger: logging.Logger,
    path: str = "",
    parent: Optional[Dict[str, Any]] = None,
) -> List[AltoItemRecord]:
    results: List[AltoItemRecord] = []
    if isinstance(value, str):
        if is_item_link_string(value):
            item_link, item_id, item_name = parse_item_link(value)
            count = 1
            if isinstance(parent, dict):
                for count_key in ("count", "quantity", "stack", "stackSize", "qty"):
                    candidate = parent.get(count_key)
                    if isinstance(candidate, (int, float)) and candidate > 0:
                        count = int(candidate)
                        break
            results.append(
                AltoItemRecord(
                    source_path=source_path,
                    item_link=item_link,
                    item_id=item_id,
                    item_name=item_name,
                    count=count,
                    context=path,
                    raw_value=value,
                )
            )
    elif isinstance(value, dict):
        for key, nested in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            results.extend(collect_item_links(nested, source_path, logger, child_path, value))
    elif isinstance(value, list):
        for index, nested in enumerate(value, start=1):
            child_path = f"{path}[{index}]" if path else f"[{index}]"
            results.extend(collect_item_links(nested, source_path, logger, child_path, parent))
    return results


def extract_altoholic_items(parsed_inputs: Sequence[Tuple[Path, LuaValue]], logger: logging.Logger) -> List[AltoItemRecord]:
    items: List[AltoItemRecord] = []
    for path, parsed in parsed_inputs:
        logger.info("Scanning Altoholic input: %s", path)
        items.extend(collect_item_links(parsed, str(path), logger))
    logger.info("Found %d Altoholic item candidate strings", len(items))
    return items


def flatten_table(value: LuaValue, parent_key: str = "") -> List[Tuple[str, LuaValue]]:
    rows: List[Tuple[str, LuaValue]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            key_name = f"{parent_key}.{key}" if parent_key else str(key)
            if isinstance(nested, (dict, list)):
                rows.extend(flatten_table(nested, key_name))
            else:
                rows.append((key_name, nested))
    elif isinstance(value, list):
        for index, nested in enumerate(value, start=1):
            key_name = f"{parent_key}[{index}]" if parent_key else f"[{index}]"
            if isinstance(nested, (dict, list)):
                rows.extend(flatten_table(nested, key_name))
            else:
                rows.append((key_name, nested))
    else:
        rows.append((parent_key or "value", value))
    return rows


def get_scan_field(row: Union[Dict[Any, Any], List[Any]], position: int) -> Any:
    if isinstance(row, dict):
        if position in row:
            return row[position]
        if str(position) in row:
            return row[str(position)]
        return row.get(str(position), row.get(position))
    if isinstance(row, list):
        idx = position - 1
        if 0 <= idx < len(row):
            return row[idx]
    return None


def parse_scan_row(
    row: Union[Dict[Any, Any], List[Any]],
    server: Optional[str],
    faction: Optional[str],
    rope_id: Optional[int],
) -> AuctioneerScanRow:
    parsed: Dict[str, Any] = {}
    for index, name in enumerate(SCAN_FIELD_NAMES, start=1):
        parsed[name] = get_scan_field(row, index)
    item_id = parsed.get("itemId") or parsed.get("id")
    item_link = parsed.get("link")
    buyout_price = parsed.get("buyoutPrice")
    bid_price = parsed.get("price") or parsed.get("curBid")
    quantity = parsed.get("stackSize")
    seen_time = parsed.get("seenTime")
    return AuctioneerScanRow(
        server=server,
        faction=faction,
        rope_id=rope_id,
        row=parsed,
        item_id=item_id,
        item_link=item_link,
        buyout_price=buyout_price,
        bid_price=bid_price,
        quantity=quantity,
        seen_time=seen_time,
    )


def extract_auctioneer_scan_data(parsed_inputs: Sequence[Tuple[Path, LuaValue]], logger: logging.Logger) -> List[AuctioneerScanRow]:
    scan_rows: List[AuctioneerScanRow] = []
    for path, parsed in parsed_inputs:
        logger.info("Scanning Auctioneer input: %s", path)
        if isinstance(parsed, dict) and "scans" in parsed:
            scans = parsed.get("scans")
            if isinstance(scans, dict):
                for server_key, server_data in scans.items():
                    if not isinstance(server_data, dict):
                        continue
                    logger.debug("Found scan serverKey=%s", server_key)
                    image = server_data.get("image")
                    ropes = server_data.get("ropes")
                    rope_id = None
                    if isinstance(server_data.get("scanstats"), dict):
                        rope_id = server_data.get("scanstats").get("ImageUpdated")
                    if isinstance(image, (list, dict)):
                        if isinstance(image, dict):
                            rows = [entry for entry in image.values()]
                        else:
                            rows = image
                        for row in rows:
                            if isinstance(row, (dict, list)):
                                scan_rows.append(parse_scan_row(row, server_key, None, rope_id))
                    if isinstance(ropes, list):
                        for rope in ropes:
                            logger.debug("Found raw rope entry length=%s", len(str(rope)))
        else:
            logger.debug("Auctioneer input does not contain top-level scans table: %s", path)
    logger.info("Extracted %d Auctioneer scan row candidates", len(scan_rows))
    return scan_rows


def aggregate_scan_prices(rows: Sequence[AuctioneerScanRow], logger: logging.Logger) -> Dict[int, Dict[str, Any]]:
    aggregated: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        if row.item_id is None:
            continue
        item_id = int(row.item_id)
        item_data = aggregated.setdefault(item_id, {
            "buyouts": [],
            "bids": [],
            "quantities": [],
            "links": set(),
            "rows": [],
        })
        if row.buyout_price is not None:
            item_data["buyouts"].append(row.buyout_price)
        if row.bid_price is not None:
            item_data["bids"].append(row.bid_price)
        if row.quantity is not None:
            item_data["quantities"].append(row.quantity)
        if row.item_link:
            item_data["links"].add(row.item_link)
        item_data["rows"].append(row.row)
    for item_id, item_data in aggregated.items():
        logger.debug(
            "Aggregated item %s: buyouts=%s bids=%s quantities=%s links=%s",
            item_id,
            item_data["buyouts"][:5],
            item_data["bids"][:5],
            item_data["quantities"][:5],
            list(item_data["links"])[:3],
        )
    return aggregated


def load_exclude_list(path: Optional[Path], logger: logging.Logger) -> set[int]:
    excluded = set()
    if not path:
        return excluded
    if not path.exists():
        logger.warning("Exclude list path does not exist: %s", path)
        return excluded
    logger.info("Loading excluded item IDs from %s", path)
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            excluded.add(int(stripped))
        except ValueError:
            logger.debug("Skipping non-integer exclude entry: %s", stripped)
    logger.info("Loaded %d excluded item IDs", len(excluded))
    return excluded


def compute_statistics(values: Sequence[Union[int, float]]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if not values:
        return None, None, None
    values = [float(v) for v in values if v is not None]
    if not values:
        return None, None, None
    avg = sum(values) / len(values)
    sorted_values = sorted(values)
    q1 = sorted_values[max(0, int(len(sorted_values) * 0.25) - 1)]
    q3 = sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * 0.75) - 1)]
    return avg, q1, q3


def estimate_recommendation_score(
    average_market: Optional[float],
    q1_price: Optional[float],
    buyout_price: Optional[int],
    count: int,
) -> Optional[float]:
    if average_market is None or average_market <= 0:
        return None
    ratio = 1.0
    if q1_price is not None and q1_price > 0:
        ratio = q1_price / average_market
    elif buyout_price is not None:
        ratio = float(buyout_price) / average_market
    return ratio * float(count)


def build_recommendations(
    items: Sequence[AltoItemRecord],
    scan_aggregates: Dict[int, Dict[str, Any]],
    excluded_ids: set[int],
    logger: logging.Logger,
) -> List[RecommendationRow]:
    recommendations: List[RecommendationRow] = []
    for item in items:
        excluded = item.item_id in excluded_ids if item.item_id is not None else False
        market_data = None
        if item.item_id is not None:
            market_data = scan_aggregates.get(item.item_id)
        best_buyout = None
        best_bid = None
        average_market = None
        q1_price = None
        q3_price = None
        notes: List[str] = []

        if market_data:
            avg_buyout, q1_buyout, q3_buyout = compute_statistics(market_data.get("buyouts", []))
            avg_bid, q1_bid, q3_bid = compute_statistics(market_data.get("bids", []))
            best_buyout = int(min(market_data.get("buyouts", []))) if market_data.get("buyouts") else None
            best_bid = int(min(market_data.get("bids", []))) if market_data.get("bids") else None
            average_market = avg_buyout or avg_bid
            q1_price = q1_buyout or q1_bid
            q3_price = q3_buyout or q3_bid
            notes.append("scan data matched")
        else:
            notes.append("no auctioneer scan data matched")

        if average_market is None and best_buyout is not None:
            average_market = float(best_buyout)
        if q1_price is None and average_market is not None:
            q1_price = average_market * 0.90
        if q3_price is None and average_market is not None:
            q3_price = average_market * 1.10

        score = estimate_recommendation_score(average_market, q1_price, best_buyout, item.count)

        recommendations.append(
            RecommendationRow(
                item_id=item.item_id,
                item_name=item.item_name,
                item_link=item.item_link,
                count=item.count,
                best_buyout=best_buyout,
                best_bid=best_bid,
                average_market=average_market,
                q1_price=q1_price,
                q3_price=q3_price,
                recommendation_score=score,
                excluded=excluded,
                source_context=item.context,
                notes=", ".join(notes),
            )
        )

    recommendations.sort(
        key=lambda row: (
            0 if row.recommendation_score is None else -row.recommendation_score,
            0 if row.average_market is None else -row.average_market,
        )
    )
    logger.info("Built %d recommendation rows", len(recommendations))
    return recommendations


def write_csv(path: Path, rows: Sequence[RecommendationRow], logger: logging.Logger) -> None:
    logger.info("Writing CSV output: %s", path)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "item_id",
                "item_name",
                "item_link",
                "count",
                "best_buyout",
                "best_bid",
                "average_market",
                "q1_price",
                "q3_price",
                "recommendation_score",
                "excluded",
                "source_context",
                "notes",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.item_id,
                    row.item_name,
                    row.item_link,
                    row.count,
                    row.best_buyout,
                    row.best_bid,
                    f"{row.average_market:.2f}" if row.average_market is not None else None,
                    f"{row.q1_price:.2f}" if row.q1_price is not None else None,
                    f"{row.q3_price:.2f}" if row.q3_price is not None else None,
                    f"{row.recommendation_score:.4f}" if row.recommendation_score is not None else None,
                    row.excluded,
                    row.source_context,
                    row.notes,
                ]
            )
    logger.info("CSV output complete (%d rows)", len(rows))


def resolve_input_paths(input_paths: Sequence[str], logger: logging.Logger) -> List[Path]:
    paths: List[Path] = []
    for text in input_paths:
        path = Path(text).expanduser().resolve()
        if path.exists():
            paths.append(path)
        else:
            logger.warning("Resolved input path does not exist: %s", text)
    return paths


def find_savedvariables_dir(root: Path, logger: logging.Logger) -> Optional[Path]:
    if root.name == "SavedVariables":
        return root if root.exists() else None
    if root.name == "Account":
        return root if root.exists() else None
    if root.name == "WTF":
        candidate = root / "Account"
        return candidate if candidate.exists() else None
    candidate = root / "WTF" / "Account"
    if candidate.exists():
        return candidate
    candidate = root / "Account"
    if candidate.exists():
        return candidate
    return None


def find_savedvariables_dirs(wow_root: Optional[Path], account: Optional[str], logger: logging.Logger) -> List[Path]:
    candidates: List[Path] = []
    roots = [wow_root] if wow_root else DEFAULT_WOW_SAVEDVARS_ROOTS
    for root in roots:
        if root is None:
            continue
        resolved = root.expanduser().resolve() if root.exists() else root.expanduser()
        savedvars_dir = find_savedvariables_dir(resolved, logger)
        if savedvars_dir and savedvars_dir.exists():
            candidates.append(savedvars_dir)
            continue
        if resolved.exists():
            for alt in [resolved / "WTF" / "Account", resolved / "Account", resolved / "WTF"]:
                if alt.exists():
                    savedvars_dir = find_savedvariables_dir(alt, logger)
                    if savedvars_dir:
                        candidates.append(savedvars_dir)
                        break
    unique_dirs: List[Path] = []
    for candidate in candidates:
        if candidate not in unique_dirs:
            unique_dirs.append(candidate)
    if account:
        filtered: List[Path] = []
        for candidate in unique_dirs:
            account_dir = candidate / account
            if account_dir.exists():
                filtered.append(account_dir)
        if filtered:
            return filtered
        logger.warning("No SavedVariables account directory found for '%s'", account)
    return unique_dirs


def resolve_savedvariable_files(
    wow_root: Optional[Path],
    account: Optional[str],
    filenames: Sequence[str],
    logger: logging.Logger,
) -> List[Path]:
    saved_dirs = find_savedvariables_dirs(wow_root, account, logger)
    resolved_files: List[Path] = []
    for saved_dir in saved_dirs:
        for filename in filenames:
            candidate = saved_dir / filename
            if candidate.exists():
                resolved_files.append(candidate)
            else:
                logger.debug("Candidate SavedVariables file missing: %s", candidate)
    return resolved_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Altoholic/Auctioneer sell recommendations from saved Lua data."
    )
    parser.add_argument(
        "--altoholic",
        nargs="*",
        help="Path to Altoholic DataStore saved-variable file(s) or directory(ies).",
    )
    parser.add_argument(
        "--auctioneer",
        nargs="*",
        help="Path to Auctioneer AucScanData saved-variable file(s) or directory(ies).",
    )
    parser.add_argument(
        "--wow-root",
        help="World of Warcraft root folder containing WTF/Account.",
    )
    parser.add_argument(
        "--wow-account",
        help="Optional WoW account folder under WTF/Account to target.",
    )
    parser.add_argument(
        "--exclude-file",
        help="Path to a newline-delimited file of excluded item IDs.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the output CSV file.",
    )
    parser.add_argument(
        "--log",
        default="sell_recommend.log",
        help="Path to the debug log file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log_path = Path(args.log)
    logger = setup_logger(log_path)

    logger.info("Starting sell recommendation script")
    logger.info("Altoholic input paths: %s", args.altoholic)
    logger.info("Auctioneer input paths: %s", args.auctioneer)
    logger.info("WoW root: %s", args.wow_root)
    logger.info("WoW account: %s", args.wow_account)

    altoholic_paths: List[Path] = []
    auctioneer_paths: List[Path] = []

    if args.altoholic:
        altoholic_paths = resolve_input_paths(args.altoholic, logger)
    else:
        altoholic_paths = resolve_savedvariable_files(
            Path(args.wow_root) if args.wow_root else None,
            args.wow_account,
            DEFAULT_ALTOHOLIC_FILES,
            logger,
        )
        if altoholic_paths:
            logger.info("Resolved Altoholic SavedVariables: %s", altoholic_paths)

    if args.auctioneer:
        auctioneer_paths = resolve_input_paths(args.auctioneer, logger)
    else:
        auctioneer_paths = resolve_savedvariable_files(
            Path(args.wow_root) if args.wow_root else None,
            args.wow_account,
            DEFAULT_AUCTIONEER_FILES,
            logger,
        )
        if auctioneer_paths:
            logger.info("Resolved Auctioneer SavedVariables: %s", auctioneer_paths)

    if not altoholic_paths:
        logger.error("No Altoholic input files were found or provided.")
        return 2
    if not auctioneer_paths:
        logger.error("No Auctioneer input files were found or provided.")
        return 2

    excluded_ids = load_exclude_list(Path(args.exclude_file), logger) if args.exclude_file else set()

    altoholic_inputs = load_lua_inputs(altoholic_paths, logger)
    auctioneer_inputs = load_lua_inputs(auctioneer_paths, logger)

    alto_items = extract_altoholic_items(altoholic_inputs, logger)
    scan_rows = extract_auctioneer_scan_data(auctioneer_inputs, logger)
    scan_aggregates = aggregate_scan_prices(scan_rows, logger)

    recommendations = build_recommendations(alto_items, scan_aggregates, excluded_ids, logger)
    write_csv(Path(args.output), recommendations, logger)

    logger.info("Sell recommendation script finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
