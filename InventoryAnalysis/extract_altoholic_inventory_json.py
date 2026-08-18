#!/usr/bin/env python3
"""Extract Altoholic/DataStore saved-variable inventory data to JSON.

This script parses Altoholic DataStore saved-variable Lua files and produces a
JSON export with account/realm/character and guild bank structure.

JSON schema (sensible format):
{
  "accounts": {
    "AccountName": {
      "realms": {
        "RealmName": {
          "characters": {
            "CharacterName": {
              "metadata": {...},
              "bags": [item, ...],
              "bank": [item, ...],
              "equipment": [item, ...],
              "mail": [item, ...],
              "containers": [item, ...],
              "misc": [item, ...]
            }
          },
          "guilds": {
            "GuildName": {
              "metadata": {...},
              "bank_tabs": {
                "1": {
                  "items": [...],
                  "tab_name": "Tab 1",
                  "last_update": 12345,
                },
                "2": {...}
              },
              "items": [item, ...]
            }
          }
        }
      }
    }
  }
}

Item structure:
{
  "item_link": "item:...",
  "item_id": 12345,
  "item_name": "Foo Bar",
  "count": 1,
  "source": "bag|bank|equipment|mail|guild_bank|unknown",
  "container_path": ["global", "Characters", "Account.Realm.Char", ...],
  "bag_id": 0,
  "slot_id": 5,
  "tab_id": 1,
  "module": "DataStore_Containers",
  "raw_value": "..."
}
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Tuple,
    Union,
    cast,
)

try:
    from slpp import slpp
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: slpp. Install with `pip install slpp`"
    ) from exc

LuaValue = Union[str, int, float, bool, None, Dict[str, Any], List[Any]]

ITEM_LINK_RGX = re.compile(r"\|H(?P<link>item:[^|]+)\|h")
ITEM_NAME_RGX = re.compile(r"\[(?P<name>[^\]]+)\]")
COMPOSITE_KEY_RGX = re.compile(r"^[^.]+\.[^.]+\.[^.]+$")

SENSITIVE_KEYS = {"password", "auth", "token"}
SOURCE_TO_BUCKET = {
    "bag": "bags",
    "bank": "bank",
    "equipment": "equipment",
    "mail": "mail",
    "containers": "containers",
    "unknown": "misc",
}
VALID_INPUT_GLOB = "DataStore_*.lua"


@dataclass
class ExtractedItem:
    item_link: Optional[str]
    item_id: Optional[int]
    item_name: Optional[str]
    count: int
    source: str
    module: str
    container_path: List[str]
    bag_id: Optional[int] = None
    slot_id: Optional[int] = None
    tab_id: Optional[int] = None
    tab_name: Optional[str] = None
    raw_value: Optional[Union[str, int, float, bool, dict, list]] = None


@dataclass
class CharacterInventory:
    metadata: Dict[str, Any]
    bags: List[Dict[str, Any]]
    bank: List[Dict[str, Any]]
    equipment: List[Dict[str, Any]]
    mail: List[Dict[str, Any]]
    containers: List[Dict[str, Any]]
    misc: List[Dict[str, Any]]


@dataclass
class GuildInventory:
    metadata: Dict[str, Any]
    bank_tabs: Dict[str, Dict[str, Any]]
    items: List[Dict[str, Any]]


def setup_logger(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("altoholic_extract")
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def log_error_exc(
    logger: logging.Logger, msg: str, *args: object, exc: Exception
) -> None:
    logger.error(msg, *args, exc, exc_info=True)


def decode_lua_text(text: str, path: Path, logger: logging.Logger) -> LuaValue:
    def try_decode(value: str, description: str) -> LuaValue:
        try:
            return slpp.decode(value)
        except Exception as exc:
            logger.error(
                "%s parse failed for %s: %s",
                description,
                path.name,
                exc,
                exc_info=True,
            )
            raise

    parsed = try_decode(text, "Initial Lua")
    if isinstance(parsed, str) and "=" in text:
        assignment_match = re.search(
            r"^[^=]+=[ \t\r\n]*(\{.+)$", text, re.DOTALL
        )
        if assignment_match:
            parsed = try_decode(assignment_match.group(1), "Fallback Lua")

    return parsed


def parse_lua(path: Path, logger: logging.Logger) -> LuaValue:
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        raise ValueError(f"Lua file is empty: {path}")

    if text.startswith("return"):
        text = text[len("return") :].strip()

    parsed = decode_lua_text(text, path, logger)
    logger.info("Parsed %s as %s", path.name, type(parsed).__name__)
    return parsed


def parse_item_link(
    value: str,
) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    link = None
    item_id = None
    item_name = None
    match = ITEM_LINK_RGX.search(value)
    if match:
        link = match.group("link")
    elif value.startswith("item:"):
        link = value

    if link:
        if item_name_match := ITEM_NAME_RGX.search(value):
            item_name = item_name_match.group("name")

        tokens = link.split(":")
        if len(tokens) > 1:
            try:
                item_id = int(tokens[1])
            except ValueError:
                pass
    return link, item_id, item_name


def tab_segment_id(segment: str) -> Optional[int]:
    lower = segment.lower()
    if lower.isdigit():
        return int(lower)
    if lower.startswith("tab"):
        suffix = lower[3:]
        try:
            return int(suffix)
        except ValueError:
            return None
    if lower.startswith("banktab"):
        suffix = lower[7:]
        try:
            return int(suffix)
        except ValueError:
            return None
    return None


def is_mail_path(path: List[str], module: str) -> bool:
    lower_module = module.lower()
    if lower_module.startswith("datastore_mails"):
        return True
    segments = [part.lower() for part in path]
    return any(
        seg in {"mail", "mails", "mailbox", "inbox", "outbox"}
        for seg in segments
    )


def is_guild_bank_path(path: List[str], module: str) -> bool:
    segments = [part.lower() for part in path]
    if "guild" in "/".join(segments):
        if any(
            seg in {"bank", "banktabs", "guildbank", "guildbanktab"}
            for seg in segments
        ):
            return True
        if any(
            seg.startswith("tab") for seg in segments
        ) and "guild" in "/".join(segments):
            return True
    return False


def get_count_from_context(parent: Optional[Dict[str, Any]]) -> int:
    if not isinstance(parent, dict):
        return 1
    for key in ("count", "quantity", "stack", "stackSize", "qty"):
        if key in parent:
            value = parent[key]
            if isinstance(value, (int, float)) and value >= 0:
                return int(value)
    return 1


def get_sibling_count(
    path: List[str], parent: Optional[Dict[str, Any]]
) -> Optional[int]:
    if not isinstance(parent, dict) or len(path) < 2:
        return None
    if path[-2].lower() not in {"ids", "links"}:
        return None
    try:
        index = int(path[-1])
    except ValueError:
        return None
    counts = parent.get("counts")
    if isinstance(counts, list):
        if 0 <= index < len(counts):
            value = counts[index]
            if isinstance(value, (int, float)) and value >= 0:
                return int(value)
        if 0 <= index + 1 < len(counts):
            value = counts[index + 1]
            if isinstance(value, (int, float)) and value >= 0:
                return int(value)
    elif isinstance(counts, dict):
        for key in (
            index,
            index + 1,
            str(index),
            str(index + 1),
            f"[{index}]",
            f"[{index + 1}]",
        ):
            if key in counts:
                value = counts[key]
                if isinstance(value, (int, float)) and value >= 0:
                    return int(value)
    return None


def get_item_count(path: List[str], parent: Optional[Dict[str, Any]]) -> int:
    return get_sibling_count(path, parent) or get_count_from_context(parent)


def normalize_slot_index(
    path: List[str], parent: Optional[Any]
) -> Optional[int]:
    if not path or not path[-1].isdigit():
        return None
    index = int(path[-1])
    if isinstance(parent, list):
        return index + 1
    return index


def get_item_id_from_ids(ids_obj: Any, slot_index: int) -> Optional[int]:
    if isinstance(ids_obj, list):
        if 1 <= slot_index <= len(ids_obj):
            value = ids_obj[slot_index - 1]
            if isinstance(value, int):
                return value
    elif isinstance(ids_obj, dict):
        for key in (slot_index, str(slot_index), f"[{slot_index}]"):
            if key in ids_obj:
                value = ids_obj[key]
                if isinstance(value, int):
                    return value
    return None


def get_sibling_link(links_obj: Any, slot_index: int) -> Optional[str]:
    if isinstance(links_obj, list):
        if 1 <= slot_index <= len(links_obj):
            value = links_obj[slot_index - 1]
            if isinstance(value, str):
                return value
    elif isinstance(links_obj, dict):
        for key in (slot_index, str(slot_index), f"[{slot_index}]"):
            if key in links_obj:
                value = links_obj[key]
                if isinstance(value, str):
                    return value
    return None


def has_sibling_ids_for_slot(
    path: List[str], parent: Optional[Dict[str, Any]]
) -> bool:
    if not path or len(path) < 2 or path[-2].lower() != "links":
        return False
    if not isinstance(parent, dict):
        return False
    slot_index = normalize_slot_index(path, parent)
    if slot_index is None:
        return False
    return get_item_id_from_ids(parent.get("ids"), slot_index) is not None


def is_inventory_path(path: List[str], module: str) -> bool:
    lower_module = module.lower()
    segments = [part.lower() for part in path]
    if lower_module.startswith("datastore_inventory"):
        return True
    return any(seg in {"equipment", "inventory"} for seg in segments)


def extract_item_from_ids_value(
    value: int,
    module: str,
    path: List[str],
    parent: Optional[Dict[str, Any]],
) -> Optional[ExtractedItem]:
    if len(path) < 2 or path[-2].lower() != "ids":
        return None
    if not isinstance(parent, dict):
        return None

    slot_id = normalize_slot_index(path, parent)
    if slot_id is None:
        return None

    link_value = None
    item_name = None
    if link := get_sibling_link(parent.get("links"), slot_id):
        parsed_link, parsed_id, parsed_name = parse_item_link(link)
        link_value = parsed_link or link
        if parsed_name:
            item_name = parsed_name
        if parsed_id is not None:
            value = parsed_id

    return ExtractedItem(
        item_link=link_value,
        item_id=value,
        item_name=item_name,
        count=get_item_count(path, parent),
        source=classify_source(path, module),
        module=module,
        container_path=path,
        bag_id=(
            bag_segment_id(
                next(
                    (seg for seg in path if seg.lower().startswith("bag")), ""
                )
            )
            if any(seg.lower().startswith("bag") for seg in path)
            else None
        ),
        slot_id=slot_id,
        raw_value=value,
    )


def extract_item_from_numeric_value(
    value: int,
    module: str,
    path: List[str],
    parent: Optional[Dict[str, Any]],
) -> Optional[ExtractedItem]:
    if not path or not path[-1].isdigit():
        return None

    slot_id = normalize_slot_index(path, parent)
    if slot_id is None:
        return None

    if item := extract_item_from_ids_value(value, module, path, parent):
        return item

    if not is_inventory_path(path, module) and not is_mail_path(path, module):
        return None

    link_value = None
    item_name = None
    if isinstance(parent, dict):
        if link := get_sibling_link(parent.get("links"), slot_id):
            parsed_link, parsed_id, parsed_name = parse_item_link(link)
            link_value = parsed_link or link
            if parsed_name:
                item_name = parsed_name
            if parsed_id is not None:
                value = parsed_id

    return ExtractedItem(
        item_link=link_value,
        item_id=value,
        item_name=item_name,
        count=get_item_count(path, parent),
        source=classify_source(path, module),
        module=module,
        container_path=path,
        bag_id=(
            bag_segment_id(
                next(
                    (seg for seg in path if seg.lower().startswith("bag")), ""
                )
            )
            if any(seg.lower().startswith("bag") for seg in path)
            else None
        ),
        slot_id=slot_id,
        raw_value=value,
    )


def safe_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {
            str(k): safe_json_value(v)
            for k, v in value.items()
            if str(k).lower() not in SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [safe_json_value(v) for v in value]
    return str(value)


def bag_segment_id(segment: str) -> Optional[int]:
    lower = segment.lower()
    if lower.startswith("bag"):
        try:
            return int(lower[3:])
        except ValueError:
            return None
    return None


def classify_source(path: List[str], module: str) -> str:
    segments = [part.lower() for part in path]
    if is_guild_bank_path(path, module):
        return "guild_bank"

    if is_mail_path(path, module):
        return "mail"

    bag_segment = next(
        (part for part in segments if part.startswith("bag")), None
    )
    if bag_segment is not None:
        bag_id = bag_segment_id(bag_segment)
        if path and path[-1].lower() == bag_segment:
            return "containers"
        if bag_id is not None:
            if bag_id == 100 or 5 <= bag_id <= 11 or bag_id == -2:
                return "bank"
            return "bag"
        return "bag"

    if "ids" in segments and module.lower().startswith("datastore_containers"):
        return "bag"

    if "bank" in segments and "guild" not in segments:
        return "bank"
    if "inventory" in segments or "equipment" in segments:
        return "equipment"
    return "unknown"


def locate_owner(
    path: List[str],
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    account = realm = character = guild = None
    for part in path:
        if COMPOSITE_KEY_RGX.match(part):
            account, realm, name = part.split(".")
            if "guild" in "/".join(path).lower() or "guild" in part.lower():
                guild = name
            else:
                character = name
    return account, realm, character, guild


def extract_item_records(
    obj: Any,
    module: str,
    path: Optional[List[str]] = None,
    parent: Optional[Dict[str, Any]] = None,
) -> List[ExtractedItem]:
    path = path or []
    items: List[ExtractedItem] = []

    if isinstance(obj, dict):
        if maybe := extract_item_from_dict(obj, module, path, parent):
            items.append(maybe)
        preserve_parent = path and path[-1].lower() in {"ids", "links"}
        for key, value in obj.items():
            next_parent = parent if preserve_parent else obj
            items.extend(
                extract_item_records(
                    value, module, path + [str(key)], next_parent
                )
            )
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            items.extend(
                extract_item_records(
                    value, module, path + [str(index)], parent
                )
            )
    elif isinstance(obj, int):
        if item := extract_item_from_ids_value(obj, module, path, parent):
            items.append(item)
        elif item := extract_item_from_numeric_value(
            obj, module, path, parent
        ):
            items.append(item)
    elif isinstance(obj, str):
        if link_data := parse_item_link(obj):
            link, item_id, item_name = link_data
            if link:
                if has_sibling_ids_for_slot(path, parent):
                    return items
                item = ExtractedItem(
                    item_link=link,
                    item_id=item_id,
                    item_name=item_name,
                    count=get_item_count(path, parent),
                    source=classify_source(path, module),
                    module=module,
                    container_path=path,
                    raw_value=obj,
                )
                items.append(item)
    return items


def extract_item_from_dict(
    obj: Dict[str, Any],
    module: str,
    path: List[str],
    parent: Optional[Dict[str, Any]],
) -> Optional[ExtractedItem]:
    link_value = None
    item_id = None
    item_name = None

    for key in ("itemLink", "link", "itemString"):
        value = obj.get(key)
        if isinstance(value, str):
            link_value = value
            break

    if link_value is None:
        for key in ("itemID", "itemId"):
            value = obj.get(key)
            if isinstance(value, (int, str)):
                item_id = int(value)
                break

    if link_value is None and item_id is None:
        if isinstance(obj.get("ids"), list) and path and path[-1].isdigit():
            raw_id = (
                obj.get("ids")[int(path[-1]) - 1]
                if int(path[-1]) - 1 < len(obj.get("ids"))
                else None
            )
            if isinstance(raw_id, int):
                item_id = raw_id
        elif path and path[-1].isdigit():
            item_id = None

    if link_value is None and item_id is None:
        return None

    if link_value is not None:
        link, parsed_id, parsed_name = parse_item_link(link_value)
        link_value = link or link_value
        item_id = item_id or parsed_id
        item_name = parsed_name
    else:
        link_value = None

    if (
        not item_name
        and "itemName" in obj
        and isinstance(obj["itemName"], str)
    ):
        item_name = obj["itemName"]

    return ExtractedItem(
        item_link=link_value,
        item_id=item_id,
        item_name=item_name,
        count=get_item_count(path, parent),
        source=classify_source(path, module),
        module=module,
        container_path=path,
        raw_value=safe_json_value(obj),
    )


def ensure_character(
    export: Dict[str, Any], account: str, realm: str, character: str
) -> CharacterInventory:
    export.setdefault("accounts", {})
    acct = export["accounts"].setdefault(account, {"realms": {}})
    realm_data = acct["realms"].setdefault(
        realm, {"characters": {}, "guilds": {}}
    )
    char_data = realm_data["characters"].setdefault(
        character,
        CharacterInventory(
            metadata={},
            bags=[],
            bank=[],
            equipment=[],
            mail=[],
            containers=[],
            misc=[],
        ),
    )
    return char_data


def ensure_guild(
    export: Dict[str, Any], account: str, realm: str, guild: str
) -> GuildInventory:
    export.setdefault("accounts", {})
    acct = export["accounts"].setdefault(account, {"realms": {}})
    realm_data = acct["realms"].setdefault(
        realm, {"characters": {}, "guilds": {}}
    )
    guild_data = realm_data["guilds"].setdefault(
        guild,
        GuildInventory(
            metadata={},
            bank_tabs={},
            items=[],
        ),
    )
    return guild_data


def assign_item_record(export: Dict[str, Any], item: ExtractedItem) -> None:
    account, realm, character, guild = locate_owner(item.container_path)
    record = {
        "item_link": item.item_link,
        "item_id": item.item_id,
        "item_name": item.item_name,
        "count": item.count,
        "source": item.source,
        "module": item.module,
        "container_path": item.container_path,
        "bag_id": item.bag_id,
        "slot_id": item.slot_id,
        "tab_id": item.tab_id,
        "tab_name": item.tab_name,
        "raw_value": item.raw_value,
    }

    if character and account and realm:
        target = ensure_character(export, account, realm, character)
        bucket = SOURCE_TO_BUCKET.get(item.source, "misc")
        getattr(target, bucket).append(record)
    elif guild and account and realm:
        guild_target = ensure_guild(export, account, realm, guild)
        tab_id = next(
            (int(part) for part in item.container_path if part.isdigit()), None
        )
        if item.source == "guild_bank" and tab_id is not None:
            bank_tab = guild_target.bank_tabs.setdefault(
                str(tab_id),
                {"items": [], "tab_name": item.tab_name, "last_update": None},
            )
            bank_tab["items"].append(record)
        else:
            guild_target.items.append(record)
    else:
        export.setdefault("unclassified", []).append(record)


def populate_metadata_from_section(
    export: Dict[str, Any],
    root: Dict[str, Any],
    section_name: str,
    ensure_fn: Callable[[Dict[str, Any], str, str, str], Any],
) -> None:
    section = root.get(section_name)
    if not isinstance(section, dict):
        return

    for full_key, metadata in section.items():
        if COMPOSITE_KEY_RGX.match(full_key):
            account, realm, name = full_key.split(".")
            entity_data = ensure_fn(export, account, realm, name)
            entity_data.metadata = safe_json_value(metadata)


def record_metadata(export: Dict[str, Any], root: Dict[str, Any]) -> None:
    global_root = root.get("global", root)
    if not isinstance(global_root, dict):
        return

    populate_metadata_from_section(
        export, global_root, "Characters", ensure_character
    )
    populate_metadata_from_section(export, global_root, "Guilds", ensure_guild)


def find_saved_variables_dirs(root: Path) -> List[Path]:
    if root.is_dir() and root.name.lower() == "savedvariables":
        return [root]
    return [
        candidate
        for candidate in sorted(root.rglob("SavedVariables"))
        if candidate.is_dir()
    ]


def load_inputs(
    paths: Iterable[Path], logger: logging.Logger
) -> List[Tuple[str, LuaValue]]:
    loaded: List[Tuple[str, LuaValue]] = []
    for path in paths:
        path = path.expanduser()
        if not path.exists():
            logger.warning("Input not found: %s", path)
            continue

        if path.is_dir():
            saved_dirs = find_saved_variables_dirs(path)
            if saved_dirs:
                logger.info(
                    "Scanning SavedVariables directories under %s", path
                )
                for saved_dir in saved_dirs:
                    for child in sorted(saved_dir.glob(VALID_INPUT_GLOB)):
                        try:
                            loaded.append(
                                (child.name, parse_lua(child, logger))
                            )
                        except Exception as exc:
                            log_error_exc(
                                logger,
                                "Failed to load %s: %s",
                                child,
                                exc=exc,
                            )
                continue

            for child in sorted(path.glob(VALID_INPUT_GLOB)):
                try:
                    loaded.append((child.name, parse_lua(child, logger)))
                except Exception as exc:
                    log_error_exc(
                        logger,
                        "Failed to load %s: %s",
                        child,
                        exc=exc,
                    )
            continue

        if path.is_file() and path.match(VALID_INPUT_GLOB):
            try:
                loaded.append((path.name, parse_lua(path, logger)))
            except Exception as exc:
                log_error_exc(
                    logger,
                    "Failed to load %s: %s",
                    path,
                    exc=exc,
                )
        else:
            logger.warning(
                "Skipping non-DataStore file: %s. Only DataStore_*.lua"
                " files are processed.",
                path,
            )
    return loaded


def summarize_export(export: Dict[str, Any], logger: logging.Logger) -> None:
    if not export.get("accounts"):
        logger.info("No accounts were extracted; skipping summary.")
        return

    total_characters = 0
    total_items = 0
    total_equipped = 0
    total_bag_items = 0
    total_bank_items = 0
    total_mail_items = 0
    total_containers = 0
    total_guilds = 0
    total_guild_bank_items = 0
    account_realm_counts: Counter[str] = Counter()

    for account_name, account_data in export["accounts"].items():
        for realm_name, realm_data in account_data["realms"].items():
            for character_name, character_data in realm_data[
                "characters"
            ].items():
                total_characters += 1
                bag_count = len(character_data.bags)
                bank_count = len(character_data.bank)
                equipment_count = len(character_data.equipment)
                mail_count = len(character_data.mail)
                container_count = len(character_data.containers)
                misc_count = len(character_data.misc)
                item_count = (
                    bag_count
                    + bank_count
                    + equipment_count
                    + mail_count
                    + container_count
                    + misc_count
                )
                total_items += item_count
                total_equipped += equipment_count
                total_bag_items += bag_count
                total_bank_items += bank_count
                total_mail_items += mail_count
                total_containers += container_count
                account_realm_counts[f"{account_name}/{realm_name}"] += 1

                logger.info(
                    (
                        "Character %s.%s has %d total items: %d bags, %d bank,"
                        " %d equipped, %d mail, %d containers, %d misc"
                    ),
                    account_name,
                    character_name,
                    item_count,
                    bag_count,
                    bank_count,
                    equipment_count,
                    mail_count,
                    container_count,
                    misc_count,
                )

    for account_realm, count in account_realm_counts.items():
        logger.info("Extracted %d character(s) from %s", count, account_realm)

    for account_name, account_data in export["accounts"].items():
        for realm_name, realm_data in account_data["realms"].items():
            total_guilds += len(realm_data["guilds"])
            for guild_name, guild_data in realm_data["guilds"].items():
                guild_item_count = len(guild_data.items)
                for tab_data in guild_data.bank_tabs.values():
                    guild_item_count += len(tab_data.get("items", []))
                total_guild_bank_items += guild_item_count
                logger.info(
                    "Guild %s.%s has %d bank items across %d tabs",
                    realm_name,
                    guild_name,
                    guild_item_count,
                    len(guild_data.bank_tabs),
                )

    logger.info(
        (
            "Summary: %d characters, %d accounts/realms, %d guilds,"
            " %d total items, %d equipped, %d bag,"
            " %d bank, %d mail, %d container items"
        ),
        total_characters,
        len(account_realm_counts),
        total_guilds,
        total_items,
        total_equipped,
        total_bag_items,
        total_bank_items,
        total_mail_items,
        total_containers,
    )


def build_export(
    inputs: List[Tuple[str, LuaValue]], logger: logging.Logger
) -> Dict[str, Any]:
    export: Dict[str, Any] = {}
    for module_name, lua_obj in inputs:
        root = (
            lua_obj
            if not isinstance(lua_obj, dict)
            else lua_obj.get("global", lua_obj)
        )
        if isinstance(root, dict):
            record_metadata(export, root)
        try:
            extracted = extract_item_records(
                root, module_name, path=[module_name]
            )
        except Exception as exc:
            log_error_exc(
                logger,
                "Failed to extract items from module %s: %s",
                module_name,
                exc=exc,
            )
            continue
        for item in extracted:
            assign_item_record(export, item)
    if not export.get("accounts"):
        logger.warning(
            "No account/realm/character data could be inferred from "
            "the inputs."
        )
    summarize_export(export, logger)
    return export


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Altoholic saved-variable inventory data to JSON."
    )
    parser.add_argument(
        "--input",
        "-i",
        nargs="+",
        default=["~/Documents/World of Warcraft/WTF/Account/"],
        help=(
            "Paths to DataStore Lua files or directories containing them. "
            "Only DataStore_*.lua files are extracted. If a directory "
            "contains SavedVariables subdirectories, only those will be scanned."
        ),
    )
    parser.add_argument(
        "--output",
        "-o",
        default="altoholic_inventory.json",
        help="Path to write the resulting JSON file.",
    )
    parser.add_argument(
        "--log",
        "-l",
        default="extract_altoholic_inventory.log",
        help="Path to write the log file.",
    )
    parser.add_argument(
        "--translateIds",
        action="store_true",
        help=(
            "Translate item IDs to names using infdata_item_names.json. "
            "When enabled, the export is written with a _translated suffix."
        ),
    )
    return parser.parse_args()


def load_item_name_map(path: Path, logger: logging.Logger) -> Dict[int, str]:
    if not path.exists():
        logger.warning("Item translation map not found: %s", path)
        return {}
    try:
        raw_map = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error(
            "Failed to load item translation map %s: %s",
            path,
            exc,
            exc_info=True,
        )
        return {}
    item_map: Dict[int, str] = {}
    for raw_id, name in raw_map.items():
        try:
            item_map[int(raw_id)] = str(name)
        except (ValueError, TypeError):
            continue
    return item_map


def translate_item_names_in_export(
    export: Dict[str, Any], item_map: Dict[int, str]
) -> None:
    if not item_map:
        return

    def translate_record(record: Dict[str, Any]) -> None:
        item_id = record.get("item_id")
        if isinstance(item_id, int) and item_id in item_map:
            record["item_name"] = f"{item_id} {item_map[item_id]}"

    for account_data in export.get("accounts", {}).values():
        for realm_data in account_data.get("realms", {}).values():
            for char_data in realm_data.get("characters", {}).values():
                for bucket in (
                    "bags",
                    "bank",
                    "equipment",
                    "mail",
                    "containers",
                    "misc",
                ):
                    for record in getattr(char_data, bucket, []):
                        translate_record(record)
            for guild_data in realm_data.get("guilds", {}).values():
                for record in getattr(guild_data, "items", []):
                    translate_record(record)
                for tab_data in guild_data.bank_tabs.values():
                    for record in tab_data.get("items", []):
                        translate_record(record)


def make_translated_output_path(output_path: Path) -> Path:
    if output_path.suffix:
        return output_path.with_name(
            f"{output_path.stem}_translated{output_path.suffix}"
        )
    return output_path.with_name(f"{output_path.name}_translated")


def _serialize_dataclasses(value: Any) -> Any:
    if is_dataclass(value):
        return _serialize_dataclasses(asdict(cast(Any, value)))
    if isinstance(value, dict):
        return {str(k): _serialize_dataclasses(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_dataclasses(v) for v in value]
    return value


def main() -> int:
    args = parse_args()
    logger = setup_logger(Path(args.log).expanduser())
    paths = [Path(p).expanduser() for p in args.input]
    inputs = load_inputs(paths, logger)
    export = build_export(inputs, logger)
    output_path = Path(args.output).expanduser()
    item_map: Dict[int, str] = {}
    if args.translateIds:
        translation_file = Path("infdata_item_names.json").expanduser()
        item_map = load_item_name_map(translation_file, logger)
        translate_item_names_in_export(export, item_map)
        output_path = make_translated_output_path(output_path)

    output_path.write_text(
        json.dumps(
            _serialize_dataclasses(export), indent=2, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    logger.info("Exported JSON to %s", output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
