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
from contextlib import redirect_stdout
import json
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import re
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple, TypedDict, Union

from slpp import slpp

debug = 0

DEFAULT_WOW_ROOT = Path.home() / "World of Warcraft 3.3.5a (no install)" / "WTF"
DEFAULT_ACCOUNT_DIR = DEFAULT_WOW_ROOT / "Account"


def default_input_path(account: Optional[str] = None) -> Path:
    account_root = DEFAULT_ACCOUNT_DIR
    if not account_root.exists():
        alt_root = DEFAULT_WOW_ROOT / "Accounts"
        if alt_root.exists():
            account_root = alt_root
    if account and account.strip():
        return account_root / account.strip()
    return account_root / "redacted"


def sanitize_account_name(account: Optional[str]) -> str:
    if not account:
        return "redacted"
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", account.strip())
    return cleaned or "redacted"


def infer_account_name(input_path: Path, account: str) -> str:
    candidate = sanitize_account_name(account)
    try:
        resolved = input_path.resolve()
    except OSError:
        resolved = input_path

    for root in (DEFAULT_ACCOUNT_DIR, DEFAULT_WOW_ROOT / "Accounts"):
        try:
            rel = resolved.relative_to(root)
        except Exception:
            continue
        if rel.parts:
            return sanitize_account_name(rel.parts[0])

    parts = resolved.parts
    for index, part in enumerate(parts):
        if part in {"Account", "Accounts"} and index + 1 < len(parts):
            return sanitize_account_name(parts[index + 1])

    return candidate


def debugLog1(message: str) -> None:
    if debug >= 1:
        print(f"[debug 1] {message}", file=sys.stderr)


def debugLog2(message: str) -> None:
    if debug >= 2:
        print(f"[debug 2] {message}", file=sys.stderr)


def debugLog3(message: str) -> None:
    if debug >= 3:
        print(f"[debug 3] {message}", file=sys.stderr)


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

SCAN_FIELD_POSITIONS = {
    "link": 1,  # item hyperlink string
    "itemLevel": 2,  # item level
    "itemType": 3,  # item class/type code
    "subType": 4,  # item subclass code
    "equipPos": 5,  # equipment slot code
    "price": 6,  # next bid / Auctioneer price
    "timeLeft": 7,  # remaining auction time bucket
    "seenTime": 8,  # scan timestamp
    "itemName": 9,  # display name
    # Position 10 is DEP2, an Auctioneer internal/deprecated field.
    "stackSize": 11,  # quantity in stack
    "quality": 12,  # item quality
    "canUse": 13,  # whether player can use item
    "useLevel": 14,  # required/use level for the item
    "minBid": 15,  # minimum bid
    "increment": 16,  # bid increment
    "buyoutPrice": 17,  # buyout price
    "curBid": 18,  # current bid amount
    "amBidder": 19,  # is player current high bidder
    "sellerName": 20,  # seller character name
    "dataFlag": 21,  # internal Auctioneer data flag
    # Position 22 is BONUSES, retained by Auctioneer but not modeled here.
    "itemId": 23,  # WoW item ID
    "itemSuffix": 24,  # random suffix code
    "itemFactor": 25,  # random factor code
    "itemEnchant": 26,  # enchantment ID
    "itemSeed": 27,  # random seed from item link
}


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
    def from_row(
        cls,
        row: Union[Dict[Any, Any], List[Any]],
        server: Optional[str] = None,
        faction: Optional[str] = None,
        rope_id: Optional[int] = None,
    ) -> "AucScanEntry":
        values = {
            name: cast_scan_value(name, get_scan_field(row, position))
            for name, position in SCAN_FIELD_POSITIONS.items()
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


@dataclass
class BuildStats:
    string_rows: int = 0
    scan_servers: int = 0
    scan_factions: int = 0
    scan_data_tables: int = 0
    image_rows: int = 0
    rope_strings: int = 0
    rope_rows: int = 0
    top_level_listing_dicts: int = 0
    top_level_listing_list_rows: int = 0


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
    normalized = strip_lua_comments(normalized).strip()
    normalized = re.sub(
        r"^[A-Za-z_][A-Za-z0-9_]*\s*=\s*",
        "",
        normalized,
        count=1,
    )
    return normalized.strip()


def log_suspicious_number_tokens(text: str, debug: int, source: str) -> None:
    if debug < 3:
        return
    normalized = normalize_lua_input(text)
    matches: List[Tuple[int, int]] = []
    quote: Optional[str] = None
    escaped = False
    index = 0
    while index < len(normalized):
        character = normalized[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character in {"'", '"'}:
            quote = character
        elif character == "-" and (
            index + 1 >= len(normalized) or not normalized[index + 1].isdigit()
        ):
            matches.append((index, index + 1))
        index += 1
    debugLog3(f"Found {len(matches)} suspicious minus token(s) in {source}")
    for start_index, end_index in matches:
        start = max(0, start_index - 24)
        end = min(len(normalized), end_index + 24)
        context = normalized[start:end].replace("\n", "\\n")
        debugLog3(f"Suspicious minus at offset {start_index} in {source}: {context!r}")


def parse_lua(text: str, debug: int = 0, source: str = "input") -> LuaValue:
    normalized = normalize_lua_input(text)
    log_suspicious_number_tokens(normalized, debug, source)
    parser_output = StringIO()
    with redirect_stdout(parser_output):
        parsed = slpp.decode(normalized)
    warnings = [line for line in parser_output.getvalue().splitlines() if line]
    if warnings:
        debugLog3(f"SLPP emitted {len(warnings)} warning(s) while parsing {source}: {warnings}")
    return parsed


def normalize_packed_rope(rope: str, debug: int = 0, source: str = "rope") -> str:
    escaped_quote = r'\\"'
    normalized = rope.replace(escaped_quote, r"\"")
    replacements = rope.count(escaped_quote)
    if replacements:
        debugLog3(f"Normalized {replacements} doubled quote escape(s) in {source}")
    return normalized


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
        raw = raw[len("item:") :]

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auctioneer_rope (
            entry_id INTEGER PRIMARY KEY,
            entry_key TEXT,
            raw_rope TEXT,
            decoded_json TEXT
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auctioneer_item (
            entry_id INTEGER PRIMARY KEY,
            entry_key TEXT,
            raw_value TEXT,
            item_link TEXT,
            item_id INTEGER,
            enchant_id INTEGER,
            gem1_id INTEGER,
            gem2_id INTEGER,
            gem3_id INTEGER,
            suffix_id INTEGER,
            unique_id INTEGER,
            level INTEGER,
            specialization_id INTEGER,
            upgrade_type INTEGER,
            instance_difficulty INTEGER,
            num_bonus_ids INTEGER,
            bonus_ids TEXT,
            upgrade_value INTEGER,
            item_name TEXT,
            parsed_json TEXT
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auctionListings (
            server TEXT,
            faction TEXT,
            ropeId INTEGER,
            id INTEGER,
            link TEXT,
            useLevel INTEGER,
            itemLevel INTEGER,
            itemType INTEGER,
            subType INTEGER,
            equipPos INTEGER,
            price INTEGER,
            timeLeft INTEGER,
            seenTime INTEGER,
            itemName TEXT,
            texture TEXT,
            stackSize INTEGER,
            quality INTEGER,
            canUse INTEGER,
            minBid INTEGER,
            curBid INTEGER,
            increment INTEGER,
            sellerName TEXT,
            buyoutPrice INTEGER,
            amBidder INTEGER,
            dataFlag INTEGER,
            itemId INTEGER,
            itemSuffix INTEGER,
            itemFactor INTEGER,
            itemEnchant INTEGER,
            itemSeed INTEGER
        )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auctioneer_compile_metadata (
            entry_id INTEGER PRIMARY KEY,
            input_path TEXT,
            input_size INTEGER,
            input_mtime INTEGER,
            parsed_type TEXT,
            string_rows INTEGER,
            scan_servers INTEGER,
            scan_factions INTEGER,
            scan_data_tables INTEGER,
            image_rows INTEGER,
            rope_strings INTEGER,
            rope_rows INTEGER,
            top_level_listing_dicts INTEGER,
            top_level_listing_list_rows INTEGER,
            compiled_at TEXT,
            generator_script TEXT,
            python_version TEXT
        )
        """)
    conn.commit()
    return conn


def insert_rows(conn: sqlite3.Connection, rows: Iterable[Tuple[str, str, Optional[str]]]) -> None:
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT INTO auctioneer_rope (
            entry_key,
            raw_rope,
            decoded_json
        ) VALUES (?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def insert_item_rows(conn: sqlite3.Connection, rows: Iterable[Tuple[Any, ...]]) -> None:
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT INTO auctioneer_item (
            entry_key,
            raw_value,
            item_link,
            item_id,
            enchant_id,
            gem1_id,
            gem2_id,
            gem3_id,
            suffix_id,
            unique_id,
            level,
            specialization_id,
            upgrade_type,
            instance_difficulty,
            num_bonus_ids,
            bonus_ids,
            upgrade_value,
            item_name,
            parsed_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def insert_listing_rows(conn: sqlite3.Connection, rows: Iterable[Tuple[Any, ...]]) -> None:
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT INTO auctionListings (
            server,
            faction,
            ropeId,
            id,
            link,
            useLevel,
            itemLevel,
            itemType,
            subType,
            equipPos,
            price,
            timeLeft,
            seenTime,
            itemName,
            texture,
            stackSize,
            quality,
            canUse,
            minBid,
            curBid,
            increment,
            sellerName,
            buyoutPrice,
            amBidder,
            dataFlag,
            itemId,
            itemSuffix,
            itemFactor,
            itemEnchant,
            itemSeed
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        rows,
    )
    conn.commit()


def insert_metadata_row(conn: sqlite3.Connection, metadata: Tuple[Any, ...]) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO auctioneer_compile_metadata (
            input_path,
            input_size,
            input_mtime,
            parsed_type,
            string_rows,
            scan_servers,
            scan_factions,
            scan_data_tables,
            image_rows,
            rope_strings,
            rope_rows,
            top_level_listing_dicts,
            top_level_listing_list_rows,
            compiled_at,
            generator_script,
            python_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        metadata,
    )
    conn.commit()


def create_temp_database(
    rope_rows: List[Tuple[str, str, Optional[str]]],
    item_rows: List[Tuple[Any, ...]],
    listing_rows: List[Tuple[Any, ...]],
) -> sqlite3.Connection:
    temp_conn = sqlite3.connect(":memory:")
    temp_conn.execute("""
        CREATE TABLE auctioneer_rope (
            entry_id INTEGER PRIMARY KEY,
            entry_key TEXT,
            raw_rope TEXT,
            decoded_json TEXT
        )
        """)
    temp_conn.execute("""
        CREATE TABLE auctioneer_item (
            entry_id INTEGER PRIMARY KEY,
            entry_key TEXT,
            raw_value TEXT,
            item_link TEXT,
            item_id INTEGER,
            enchant_id INTEGER,
            gem1_id INTEGER,
            gem2_id INTEGER,
            gem3_id INTEGER,
            suffix_id INTEGER,
            unique_id INTEGER,
            level INTEGER,
            specialization_id INTEGER,
            upgrade_type INTEGER,
            instance_difficulty INTEGER,
            num_bonus_ids INTEGER,
            bonus_ids TEXT,
            upgrade_value INTEGER,
            item_name TEXT,
            parsed_json TEXT
        )
        """)
    temp_conn.execute("""
        CREATE TABLE auctionListings (
            server TEXT,
            faction TEXT,
            ropeId INTEGER,
            id INTEGER,
            link TEXT,
            useLevel INTEGER,
            itemLevel INTEGER,
            itemType INTEGER,
            subType INTEGER,
            equipPos INTEGER,
            price INTEGER,
            timeLeft INTEGER,
            seenTime INTEGER,
            itemName TEXT,
            texture TEXT,
            stackSize INTEGER,
            quality INTEGER,
            canUse INTEGER,
            minBid INTEGER,
            curBid INTEGER,
            increment INTEGER,
            sellerName TEXT,
            buyoutPrice INTEGER,
            amBidder INTEGER,
            dataFlag INTEGER,
            itemId INTEGER,
            itemSuffix INTEGER,
            itemFactor INTEGER,
            itemEnchant INTEGER,
            itemSeed INTEGER
        )
        """)
    if rope_rows:
        insert_rows(temp_conn, rope_rows)
    if item_rows:
        insert_item_rows(temp_conn, item_rows)
    if listing_rows:
        insert_listing_rows(temp_conn, listing_rows)
    return temp_conn


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def fetch_sorted_all(conn: sqlite3.Connection, table: str) -> List[Tuple[Any, ...]]:
    cursor = conn.cursor()
    rows = cursor.execute(f"SELECT * FROM {quote_ident(table)}").fetchall()
    return sorted(rows)


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    cursor = conn.cursor()
    row = cursor.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}").fetchone()
    return int(row[0]) if row else 0


def database_matches_existing(
    existing_path: Path,
    rope_rows: List[Tuple[str, str, Optional[str]]],
    item_rows: List[Tuple[Any, ...]],
    listing_rows: List[Tuple[Any, ...]],
) -> bool:
    try:
        existing_conn = sqlite3.connect(str(existing_path))
    except sqlite3.DatabaseError:
        return False
    try:
        temp_conn = create_temp_database(rope_rows, item_rows, listing_rows)
        try:
            if count_rows(existing_conn, "auctioneer_rope") != len(rope_rows):
                return False
            if count_rows(existing_conn, "auctioneer_item") != len(item_rows):
                return False
            if count_rows(existing_conn, "auctionListings") != len(listing_rows):
                return False
            if fetch_sorted_all(existing_conn, "auctioneer_rope") != fetch_sorted_all(
                temp_conn, "auctioneer_rope"
            ):
                return False
            if fetch_sorted_all(existing_conn, "auctioneer_item") != fetch_sorted_all(
                temp_conn, "auctioneer_item"
            ):
                return False
            if fetch_sorted_all(existing_conn, "auctionListings") != fetch_sorted_all(
                temp_conn, "auctionListings"
            ):
                return False
            return True
        finally:
            temp_conn.close()
    finally:
        existing_conn.close()


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


def resolve_input_path(path: str, debug: int = 0) -> Path:
    input_path = Path(path)
    if not input_path.is_dir():
        debugLog1(f"Using input file: {input_path}")
        return input_path

    search_dirs = [input_path, input_path / "SavedVariables"]
    candidates = []
    for search_dir in search_dirs:
        if not search_dir.exists() or not search_dir.is_dir():
            continue
        for candidate in search_dir.iterdir():
            if candidate.is_file() and candidate.name.lower() == "auc-scandata.lua":
                candidates.append(candidate)
    debugLog2(
        f"Found {len(candidates)} Auc-ScanData.lua file(s) under {input_path} "
        f"and its SavedVariables folder"
    )

    if not candidates:
        raise FileNotFoundError(
            f"No Auc-ScanData.lua file found under input directory or SavedVariables: {input_path}"
        )
    if len(candidates) > 1:
        debugLog2("Multiple scan-data files found; using the first sorted path")
    debugLog1(f"Using resolved input file: {candidates[0]}")
    return candidates[0]


def parse_input_file(path: str, debug: int = 0) -> Tuple[LuaValue, Path, int, int]:
    input_path = resolve_input_path(path, debug)
    if debug >= 3:
        mtime = input_path.stat().st_mtime
        debugLog3(f"File last modified: {mtime} ({datetime.fromtimestamp(mtime)})")
    with input_path.open("r", encoding="utf-8") as handle:
        content = handle.read()
    debugLog2(f"Read {len(content)} character(s) from {input_path}")
    stripped = content.strip()
    if not stripped:
        raise ValueError("Input file is empty")
    try:
        parsed = parse_lua(content, debug, "input file")
        debugLog2(f"Parsed Lua input as {type(parsed).__name__}")
        if debug >= 3 and isinstance(parsed, dict):
            debugLog3(f"Top-level keys: {list(parsed.keys())}")
        return parsed, input_path, input_path.stat().st_size, int(input_path.stat().st_mtime)
    except Exception as error:
        debugLog3(f"Lua parsing failed; treating input as raw text: {error}")
        return stripped, input_path, input_path.stat().st_size, int(input_path.stat().st_mtime)


def build_rows(
    parsed: LuaValue,
    debug: int = 0,
) -> Tuple[
    List[Tuple[str, str, Optional[str]]],
    List[Tuple[Any, ...]],
    List[Tuple[Any, ...]],
    BuildStats,
]:
    rope_rows: List[Tuple[str, str, Optional[str]]] = []
    item_rows: List[Tuple[Any, ...]] = []
    listing_rows: List[Tuple[Any, ...]] = []
    stats = BuildStats()

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

    def add_listing_row(
        server: Optional[str],
        faction: Optional[str],
        rope_id: Optional[int],
        row: Union[Dict[Any, Any], List[Any]],
    ) -> None:
        entry = AucScanEntry.from_row(row, server, faction, rope_id)
        listing_rows.append(entry.as_tuple())

    def add_listing_container(
        server: Optional[str],
        faction: Optional[str],
        value: Any,
        source: str,
    ) -> int:
        if not isinstance(value, (dict, list)):
            debugLog3(f"Skipping non-container at {source}: {type(value).__name__}")
            return 0
        rows = value.values() if isinstance(value, dict) else value
        added = 0
        start_index = len(listing_rows)
        for row in rows:
            if isinstance(row, (dict, list)):
                add_listing_row(server, faction, None, row)
                added += 1
        debugLog2(f"Added {added} listing row(s) from {source}")
        if debug >= 3:
            seen_times = [row[12] for row in listing_rows[start_index:] if isinstance(row[12], int)]
            if seen_times:
                debugLog3(
                    f"{source}: seenTime range {min(seen_times)}-{max(seen_times)}"
                    f" ({datetime.fromtimestamp(max(seen_times))} latest)"
                )
            else:
                debugLog3(f"{source}: no valid seenTime values found among {added} row(s)")
        return added

    def process_scan_data(server: Optional[str], faction: Optional[str], scan_data: Any) -> None:
        debugLog2(f"Inspecting scan data for server={server!r}, faction={faction!r}")
        if not isinstance(scan_data, dict):
            debugLog3(f"Scan data is not a table: {type(scan_data).__name__}")
            return

        stats.scan_data_tables += 1
        image = scan_data.get("image")
        debugLog3(f"image field type: {type(image).__name__}")
        if isinstance(image, list):
            stats.image_rows += add_listing_container(server, faction, image, "image")

        ropes = scan_data.get("ropes")
        debugLog3(f"ropes field type: {type(ropes).__name__}")
        if isinstance(ropes, list):
            stats.rope_strings += len(ropes)
            for index, rope in enumerate(ropes):
                if not isinstance(rope, str):
                    debugLog3(f"Skipping non-string rope at index {index}")
                    continue
                rope = normalize_packed_rope(rope, debug, f"rope {index}")
                try:
                    unpacked = parse_lua(rope, debug, f"rope {index}")
                except Exception as error:
                    debugLog3(f"Could not parse rope {index}: {error}")
                    continue
                stats.rope_rows += add_listing_container(
                    server, faction, unpacked, f"ropes[{index}]"
                )

    if isinstance(parsed, str):
        debugLog3("Input is raw text; creating one raw rope row")
        add_string_row("raw", parsed)
        return rope_rows, item_rows, listing_rows

    debugLog2(f"Building rows from parsed {type(parsed).__name__}")
    string_rows = extract_strings(parsed)
    debugLog2(f"Extracted {len(string_rows)} string value(s) from Lua input")
    if not string_rows:
        rope_rows.append(("parsed_value", json.dumps(parsed), None))
        return rope_rows, item_rows, listing_rows

    for key, rope in string_rows:
        if isinstance(rope, str):
            debugLog3(f"Processing rope value at key {key!r} ({len(rope)} characters)")
            add_string_row(key, rope)

    # Extract scan row dictionaries if present
    if isinstance(parsed, dict):
        scans = parsed.get("scans")
        debugLog2(f"scans field type: {type(scans).__name__}")
        if isinstance(scans, dict):
            for server, server_data in scans.items():
                if not isinstance(server_data, dict):
                    debugLog3(f"Skipping non-table server branch {server!r}")
                    continue
                stats.scan_servers += 1
                for faction, scan_data in server_data.items():
                    stats.scan_factions += 1
                    process_scan_data(str(server), str(faction), scan_data)

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
                debugLog3(f"Processing listing dictionary at key {key!r}")
                stats.top_level_listing_dicts += 1
                add_listing_row(server, faction, rope_id, value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, (dict, list)):
                        debugLog3(f"Processing listing row at key {key!r}")
                        stats.top_level_listing_list_rows += 1
                        add_listing_row(server, faction, rope_id, item)

    stats.string_rows = len(string_rows)
    debugLog2(
        f"Row totals: ropes={len(rope_rows)}, items={len(item_rows)}, listings={len(listing_rows)}"
    )
    return rope_rows, item_rows, listing_rows, stats


def default_output_path(
    listing_rows: Iterable[Tuple[Any, ...]], account: str, debug: int = 0
) -> str:
    seen_times = [row[12] for row in listing_rows if len(row) > 12 and isinstance(row[12], int)]
    account_name = sanitize_account_name(account)
    if not seen_times:
        debugLog2("No seenTime values found; using auctioneer.db")
        return f"auctioneer_{account_name}.db"
    latest_seen_time = max(seen_times)
    debugLog2(f"Using latest seenTime {latest_seen_time} for the default output filename")
    if debug >= 3:
        debugLog3(
            f"seenTime candidates: count={len(seen_times)}, "
            f"min={min(seen_times)}, max={latest_seen_time} "
            f"({datetime.fromtimestamp(latest_seen_time)})"
        )
    return f"auctioneer_{account_name}_{latest_seen_time}.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=("Convert Auctioneer Lua rope data into SQLite"))
    parser.add_argument(
        "--account",
        default="redacted",
        help=("WoW account name under WTF/Account or WTF/Accounts " "(default: redacted)"),
    )
    parser.add_argument(
        "--input",
        default=None,
        help=(
            "Lua file or raw rope input file "
            "(default: <WTF>/Account/<account> or <WTF>/Accounts/<account>)"
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "SQLite database path to write "
            "(default: ./auctioneer_<account>_<latest_seenTime>.db)"
        ),
    )
    parser.add_argument(
        "--debug",
        type=int,
        choices=range(4),
        default=0,
        help="Enable verbose logging from 0 (off) through 3 (details)",
    )
    args = parser.parse_args()
    if args.input is None:
        args.input = str(default_input_path(args.account))
    return args


def prompt_replace_existing(existing_path: Path) -> bool:
    response = (
        input(f"Existing database at {existing_path} already exists. Replace it? [y/N]: ")
        .strip()
        .lower()
    )
    return response in {"y", "yes"}


def main() -> int:
    global debug
    args = parse_args()
    debug = args.debug
    account_name = infer_account_name(Path(args.input), args.account)
    debugLog1(f"Starting conversion for input: {args.input}")
    debugLog1(f"Using account: {account_name}")
    parsed, input_path, input_size, input_mtime = parse_input_file(args.input, args.debug)
    rope_rows, item_rows, listing_rows, stats = build_rows(parsed, args.debug)
    output_path = args.output or default_output_path(listing_rows, account_name, args.debug)
    debugLog1(
        f"Built {len(rope_rows)} rope row(s), {len(item_rows)} item row(s), "
        f"and {len(listing_rows)} listing row(s)"
    )
    debugLog1(f"Using output: {output_path}")
    if not rope_rows and not item_rows and not listing_rows:
        print("No rope, item, or listing entries found in input.")
        return 1

    if output_path and Path(output_path).exists():
        existing_path = Path(output_path)
        if database_matches_existing(existing_path, rope_rows, item_rows, listing_rows):
            print(
                "Existing database at {existing_path} already contains "
                "identical data. No changes made."
            )
            return 0
        if not prompt_replace_existing(existing_path):
            print("Aborting without modifying existing database.")
            return 0
        existing_path.unlink()

    conn = create_database(output_path)
    if rope_rows:
        insert_rows(conn, rope_rows)
    if item_rows:
        insert_item_rows(conn, item_rows)
    if listing_rows:
        insert_listing_rows(conn, listing_rows)

    insert_metadata_row(
        conn,
        (
            str(input_path),
            input_size,
            input_mtime,
            type(parsed).__name__,
            stats.string_rows,
            stats.scan_servers,
            stats.scan_factions,
            stats.scan_data_tables,
            stats.image_rows,
            stats.rope_strings,
            stats.rope_rows,
            stats.top_level_listing_dicts,
            stats.top_level_listing_list_rows,
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "auctioneer_rope_to_sqlite.py",
            sys.version.split()[0],
        ),
    )
    print(
        "Wrote {rope_count} rope row(s), {item_count} item row(s), and "
        "{listing_count} auction listing row(s) to {output_path}".format(
            rope_count=len(rope_rows),
            item_count=len(item_rows),
            listing_count=len(listing_rows),
            output_path=output_path,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
