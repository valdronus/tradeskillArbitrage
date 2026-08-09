#!/usr/bin/env python3
"""Convert Auctioneer LUA "rope" data into SQLite.

This script reads a World of Warcraft Auctioneer SavedVariables Lua file or a raw
Auctioneer rope string and stores the decoded result in a SQLite database.

Usage examples:
  python auctioneer_rope_to_sqlite.py --input rope.lua --output auctioneer.db
  python auctioneer_rope_to_sqlite.py --input raw_rope.txt --output auctioneer.db

The generated database contains one table:
  auctioneer_rope(entry_id INTEGER PRIMARY KEY, entry_key TEXT, raw_rope TEXT, decoded_json TEXT)

If the input is a Lua table, the script searches for string values and stores them
as raw rope entries. If the string can be parsed as Auctioneer rope metadata, the
script also stores a JSON representation under decoded_json.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple, TypedDict, Union

from slpp import slpp

class ParsedItemObject(TypedDict, total=False):
    """Parsed Auctioneer item link fields from a WoW item string."""

    # Link/value fields
    item_link: str
    item_raw: str

    # Item identifiers
    item_id: int
    enchant_id: Optional[int]
    gem1_id: Optional[int]
    gem2_id: Optional[int]
    gem3_id: Optional[int]
    suffix_id: Optional[int]
    unique_id: Optional[int]

    # Optional modifiers
    level: Optional[int]
    specialization_id: Optional[int]
    upgrade_type: Optional[int]
    instance_difficulty: Optional[int]

    # Bonus fields
    num_bonus_ids: Optional[int]
    bonus_ids: List[int]
    upgrade_value: Optional[int]

    # Extra data
    item_name: Optional[str]

LuaValue = Union[str, int, float, bool, None, Dict[str, Any], List[Any]]
ItemObject = ParsedItemObject

ITEM_LINK_RGX = re.compile(r"\|Hitem:([^|]+)\|h")
ITEM_NAME_RGX = re.compile(r"\[([^\]]+)\]")

SCAN_FIELD_NAMES = [
    "id",         # 1: internal auction row id
    "link",       # 2: item hyperlink string
    "useLevel",   # 3: required/use level for the item
    "itemLevel",  # 4: item level
    "itemType",   # 5: item class/type code
    "subType",    # 6: item subclass code
    "equipPos",   # 7: equipment slot code
    "price",      # 8: next bid / Auctioneer price
    "timeLeft",   # 9: remaining auction time bucket
    "seenTime",   # 10: scan timestamp
    "itemName",   # 11: display name
    "texture",    # 12: icon/texture identifier
    "stackSize",  # 13: quantity in stack
    "quality",    # 14: item quality
    "canUse",     # 15: whether player can use item
    "minBid",     # 16: minimum bid
    "curBid",     # 17: current bid amount
    "increment",  # 18: bid increment
    "sellerName", # 19: seller character name
    "buyoutPrice",# 20: buyout price
    "amBidder",   # 21: is player current high bidder
    "dataFlag",   # 22: internal Auctioneer data flag
    "itemId",     # 23: WoW item ID
    "itemSuffix", # 24: random suffix code
    "itemFactor", # 25: random factor code
    "itemEnchant",# 26: enchantment ID
    "itemSeed",   # 27: random seed from item link
]


def int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def bool_or_none(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


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

FIELD_CASTS = {
    "id": int_or_none,
    "link": str_or_none,
    "useLevel": int_or_none,
    "itemLevel": int_or_none,
    "itemType": int_or_none,
    "subType": int_or_none,
    "equipPos": int_or_none,
    "price": int_or_none,
    "timeLeft": int_or_none,
    "seenTime": int_or_none,
    "itemName": str_or_none,
    "texture": str_or_none,
    "stackSize": int_or_none,
    "quality": int_or_none,
    "canUse": bool_or_none,
    "minBid": int_or_none,
    "curBid": int_or_none,
    "increment": int_or_none,
    "sellerName": str_or_none,
    "buyoutPrice": int_or_none,
    "amBidder": bool_or_none,
    "dataFlag": int_or_none,
    "itemId": int_or_none,
    "itemSuffix": int_or_none,
    "itemFactor": int_or_none,
    "itemEnchant": int_or_none,
    "itemSeed": int_or_none,
}


def cast_scan_value(name: str, value: Any) -> Any:
    return FIELD_CASTS.get(name, lambda x: x)(value)


@dataclass
class AucScanEntry:
    server: Optional[str]
    faction: Optional[str]
    rope_id: Optional[int]
    id: Optional[int] = None  # internal auction row id
    link: Optional[str] = None  # item hyperlink string
    useLevel: Optional[int] = None  # required/usable character level
    itemLevel: Optional[int] = None  # item level
    itemType: Optional[int] = None  # item class/type code
    subType: Optional[int] = None  # item subclass code
    equipPos: Optional[int] = None  # equipment slot code
    price: Optional[int] = None  # next bid or current bid price used by Auctioneer
    timeLeft: Optional[int] = None  # auction time left bucket (1-4)
    seenTime: Optional[int] = None  # scan timestamp
    itemName: Optional[str] = None  # 11: item display name
    texture: Optional[str] = None  # 12: icon/texture identifier or path
    stackSize: Optional[int] = None  # 13: number of items in stack
    quality: Optional[int] = None  # 14: item quality (0=poor,1=common,2=uncommon,3=rare,4=epic)
    canUse: Optional[bool] = None  # 15: whether the player can use this item
    minBid: Optional[int] = None  # 16: minimum bid
    curBid: Optional[int] = None  # 17: current high bid
    increment: Optional[int] = None  # 18: minimum bid increment
    sellerName: Optional[str] = None  # 19: seller character name
    buyoutPrice: Optional[int] = None  # 20: buyout price
    amBidder: Optional[bool] = None  # 21: whether the player is the current high bidder
    dataFlag: Optional[int] = None  # 22: internal Auctioneer data flag
    itemId: Optional[int] = None  # 23: actual WoW item ID
    itemSuffix: Optional[int] = None  # 24: item random suffix
    itemFactor: Optional[int] = None  # 25: item random factor
    itemEnchant: Optional[int] = None  # 26: item enchantment ID
    itemSeed: Optional[int] = None  # 27: item random seed from the link

    @classmethod
    def from_row(cls, row: Union[Dict[Any, Any], List[Any]], server: Optional[str] = None, faction: Optional[str] = None, rope_id: Optional[int] = None) -> "AucScanEntry":
        values = {
            name: cast_scan_value(name, get_scan_field(row, idx + 1))
            for idx, name in enumerate(SCAN_FIELD_NAMES)
        }
        return cls(server=server, faction=faction, rope_id=rope_id, **values)

    def as_tuple(self) -> Tuple[Any, ...]:
        return (
            self.server,
            self.faction,
            self.rope_id,
            self.id,
            self.link,
            self.useLevel,
            self.itemLevel,
            self.itemType,
            self.subType,
            self.equipPos,
            self.price,
            self.timeLeft,
            self.seenTime,
            self.itemName,
            self.texture,
            self.stackSize,
            self.quality,
            self.canUse,
            self.minBid,
            self.curBid,
            self.increment,
            self.sellerName,
            self.buyoutPrice,
            self.amBidder,
            self.dataFlag,
            self.itemId,
            self.itemSuffix,
            self.itemFactor,
            self.itemEnchant,
            self.itemSeed,
        )


def strip_lua_comments(text: str) -> str:
    text = re.sub(r"--\[\[.*?\]\]", "", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", "", text)
    return text
    text = re.sub(r"--\[\[.*?\]\]", "", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", "", text)
    return text


def normalize_lua_input(text: str) -> str:
    normalized = text.strip()
    if normalized.startswith("return"):
        normalized = normalized[len("return") :].strip()
    return strip_lua_comments(normalized)


def parse_lua(text: str) -> LuaValue:
    normalized = normalize_lua_input(text)
    return slpp.decode(normalized)


def parse_item_string(value: str) -> Optional[ItemObject]:
    """Parse an Auctioneer item link into structured item fields.

    The parsed item object uses snake_case field names consistent with
    the rest of the code and the SQL schema.
    """

    raw = value.strip()
    link_match = ITEM_LINK_RGX.search(raw)
    if link_match:
        raw = link_match.group(1)

    if raw.startswith("item:"):
        raw = raw[len("item:"):]

    # Item link token positions, as seen in Auctioneer / WoW item links:
    # 1: item_id
    # 2: enchant_id
    # 3: gem1_id
    # 4: gem2_id
    # 5: gem3_id
    # 6: suffix_id
    # 7: unique_id
    # 8: level
    # 9: specialization_id
    # 10: upgrade_type
    # 11: instance_difficulty
    # 12: num_bonus_ids
    # 13+: bonus_ids and optional upgrade_value
    # Ensure it begins with a numeric item ID
    tokens = raw.split(":")
    if not tokens or not tokens[0].isdigit():
        return None

    numbers: List[Optional[int]] = []
    for token in tokens:
        if token.isdigit():
            numbers.append(int(token))
        elif token == "":
            numbers.append(0)
        else:
            try:
                numbers.append(int(token))
            except ValueError:
                numbers.append(None)

    item: ItemObject = {
        "item_link": value,
        "item_raw": value,
        "item_id": numbers[0],
        "enchant_id": numbers[1] if len(numbers) > 1 else None,
        "gem1_id": numbers[2] if len(numbers) > 2 else None,
        "gem2_id": numbers[3] if len(numbers) > 3 else None,
        "gem3_id": numbers[4] if len(numbers) > 4 else None,
        "suffix_id": numbers[5] if len(numbers) > 5 else None,
        "unique_id": numbers[6] if len(numbers) > 6 else None,
        "level": numbers[7] if len(numbers) > 7 else None,
        "specialization_id": numbers[8] if len(numbers) > 8 else None,
        "upgrade_type": numbers[9] if len(numbers) > 9 else None,
        "instance_difficulty": numbers[10] if len(numbers) > 10 else None,
        "num_bonus_ids": numbers[11] if len(numbers) > 11 else None,
        "bonus_ids": [],
        "upgrade_value": None,
    }

    if len(numbers) > 12:
        num_bonus = item.get("num_bonus_ids") or 0
        bonus_ids: List[int] = []
        offset = 12
        for idx in range(num_bonus):
            if offset + idx < len(numbers) and numbers[offset + idx] is not None:
                bonus_ids.append(numbers[offset + idx])
        item["bonus_ids"] = bonus_ids
        tail_index = offset + num_bonus
        if tail_index < len(numbers):
            item["upgrade_value"] = numbers[tail_index]
        elif len(numbers) > offset:
            item["bonus_ids"] = [n for n in numbers[offset:] if n is not None]

    name_match = ITEM_NAME_RGX.search(value)
    if name_match:
        item["item_name"] = name_match.group(1)

    return item


def flatten_lua_table(value: LuaValue, prefix: str = "") -> List[Tuple[str, LuaValue]]:
    rows: List[Tuple[str, LuaValue]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(item, dict):
                rows.extend(flatten_lua_table(item, path))
            else:
                rows.append((path, item))
    elif isinstance(value, list):
        for index, item in enumerate(value, 1):
            path = f"{prefix}.{index}" if prefix else str(index)
            if isinstance(item, (dict, list)):
                rows.extend(flatten_lua_table(item, path))
            else:
                rows.append((path, item))
    else:
        rows.append((prefix or "value", value))
    return rows


def decode_auctioneer_rope(rope: str) -> Optional[Dict[str, Any]]:
    if not rope or not isinstance(rope, str):
        return None

    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
    if any(ch not in alphabet for ch in rope):
        return None

    def char_val(ch: str) -> int:
        return alphabet.index(ch)

    bitbuffer = 0
    bitcount = 0
    values: List[int] = []
    for ch in rope:
        bitbuffer = (bitbuffer << 6) | char_val(ch)
        bitcount += 6
        while bitcount >= 8:
            bitcount -= 8
            values.append((bitbuffer >> bitcount) & 0xFF)
            bitbuffer &= (1 << bitcount) - 1

    if not values:
        return None

    return {
        "rope_length": len(rope),
        "byte_length": len(values),
        "bytes": values,
        "bytes_preview": values[:64],
    }


def create_database(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS auctioneer_rope (entry_id INTEGER PRIMARY KEY, entry_key TEXT, raw_rope TEXT, decoded_json TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS auctioneer_item (entry_id INTEGER PRIMARY KEY, entry_key TEXT, raw_value TEXT, item_link TEXT, item_id INTEGER, enchant_id INTEGER, gem1_id INTEGER, gem2_id INTEGER, gem3_id INTEGER, suffix_id INTEGER, unique_id INTEGER, level INTEGER, specialization_id INTEGER, upgrade_type INTEGER, instance_difficulty INTEGER, num_bonus_ids INTEGER, bonus_ids TEXT, upgrade_value INTEGER, item_name TEXT, parsed_json TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS auctionListings (server TEXT, faction TEXT, ropeId INTEGER, id INTEGER, link TEXT, useLevel INTEGER, itemLevel INTEGER, itemType INTEGER, subType INTEGER, equipPos INTEGER, price INTEGER, timeLeft INTEGER, seenTime INTEGER, itemName TEXT, texture TEXT, stackSize INTEGER, quality INTEGER, canUse INTEGER, minBid INTEGER, curBid INTEGER, increment INTEGER, sellerName TEXT, buyoutPrice INTEGER, amBidder INTEGER, dataFlag INTEGER, itemId INTEGER, itemSuffix INTEGER, itemFactor INTEGER, itemEnchant INTEGER, itemSeed INTEGER)"
    )
    conn.commit()
    return conn


def insert_rows(conn: sqlite3.Connection, rows: Iterable[Tuple[str, str, Optional[str]]]) -> None:
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO auctioneer_rope (entry_key, raw_rope, decoded_json) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()


def insert_item_rows(conn: sqlite3.Connection, rows: Iterable[Tuple[Any, ...]]) -> None:
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO auctioneer_item (entry_key, raw_value, item_link, item_id, enchant_id, gem1_id, gem2_id, gem3_id, suffix_id, unique_id, level, specialization_id, upgrade_type, instance_difficulty, num_bonus_ids, bonus_ids, upgrade_value, item_name, parsed_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def insert_listing_rows(conn: sqlite3.Connection, rows: Iterable[Tuple[Any, ...]]) -> None:
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT INTO auctionListings (server, faction, ropeId, itemLevel, category, subcategory, armorCategory, currentBid, unknownField7, timestamp, name, quantity, rarity, requiredLevel, originalBid, buyPrice, username, unknownField22, itemId) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def extract_strings(value: LuaValue) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                rows.append((str(key), item))
            elif isinstance(item, (dict, list)):
                rows.extend(flatten_lua_table(item, str(key)))
    elif isinstance(value, str):
        rows.append(("raw", value))
    return rows


def parse_input_file(path: str) -> LuaValue:
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    stripped = content.strip()
    if not stripped:
        raise ValueError("Input file is empty")
    try:
        return parse_lua(content)
    except Exception:
        return stripped


def build_rows(parsed: LuaValue) -> Tuple[List[Tuple[str, str, Optional[str]]], List[Tuple[Any, ...]], List[Tuple[Any, ...]]]:
    rope_rows: List[Tuple[str, str, Optional[str]]] = []
    item_rows: List[Tuple[Any, ...]] = []
    listing_rows: List[Tuple[Any, ...]] = []

    def add_string_row(key: str, value: str) -> None:
        decoded = decode_auctioneer_rope(value)
        rope_rows.append((key, value, json.dumps(decoded) if decoded is not None else None))
        item = parse_item_string(value)
        if item is not None:
            item_rows.append(
                (
                    key,
                    value,
                    item.get("item_link"),
                    item.get("item_id"),
                    item.get("enchant_id"),
                    item.get("gem1_id"),
                    item.get("gem2_id"),
                    item.get("gem3_id"),
                    item.get("suffix_id"),
                    item.get("unique_id"),
                    item.get("level"),
                    item.get("specialization_id"),
                    item.get("upgrade_type"),
                    item.get("instance_difficulty"),
                    item.get("num_bonus_ids"),
                    json.dumps(item.get("bonus_ids", [])),
                    item.get("upgrade_value"),
                    item.get("item_name"),
                    json.dumps(item),
                )
            )

    def add_listing_row(server: Optional[str], faction: Optional[str], rope_id: Optional[int], row: Union[Dict[Any, Any], List[Any]]) -> None:
        entry = AucScanEntry.from_row(row, server, faction, rope_id)
        listing_rows.append(entry.as_tuple())

    if isinstance(parsed, str):
        add_string_row("raw", parsed)
        return rope_rows, item_rows, listing_rows

    string_rows = extract_strings(parsed)
    if not string_rows:
        rope_rows.append(("parsed_value", json.dumps(parsed), None))
        return rope_rows, item_rows, listing_rows

    for key, rope in string_rows:
        if isinstance(rope, str):
            add_string_row(key, rope)

    # Extract scan row dictionaries if present
    if isinstance(parsed, dict):
        server = parsed.get("server") if isinstance(parsed.get("server"), str) else None
        faction = parsed.get("faction") if isinstance(parsed.get("faction"), str) else None
        rope_id = None
        if isinstance(parsed.get("ropeId"), int):
            rope_id = parsed.get("ropeId")
        elif isinstance(parsed.get("ropeId"), str) and parsed.get("ropeId").isdigit():
            rope_id = int(parsed.get("ropeId"))

        for key, value in parsed.items():
            if key in {"server", "faction", "ropeId"}:
                continue
            if isinstance(value, dict) and all(str(k).isdigit() for k in value.keys()):
                add_listing_row(server, faction, rope_id, value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, (dict, list)):
                        add_listing_row(server, faction, rope_id, item)

    return rope_rows, item_rows, listing_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert Auctioneer Lua rope data into SQLite")
    parser.add_argument("--input", required=True, help="Lua file or raw rope input file")
    parser.add_argument("--output", required=True, help="SQLite database path to write")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    parsed = parse_input_file(args.input)
    rope_rows, item_rows, listing_rows = build_rows(parsed)
    if not rope_rows and not item_rows and not listing_rows:
        print("No rope, item, or listing entries found in input.")
        return 1
    conn = create_database(args.output)
    if rope_rows:
        insert_rows(conn, rope_rows)
    if item_rows:
        insert_item_rows(conn, item_rows)
    if listing_rows:
        insert_listing_rows(conn, listing_rows)
    print(
        f"Wrote {len(rope_rows)} rope row(s), {len(item_rows)} item row(s), and {len(listing_rows)} auction listing row(s) to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
