#!/usr/bin/env python3
from __future__ import annotations
"""Extract TradeSkillInfo recipe data from Lua into JSON.

This tool parses TradeSkillInfo's TradeskillIdData.lua tables, converts recipe
entries into structured JSON, and enriches component and source metadata using
InfData item name mappings.
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from slpp import slpp

LuaValue = Union[str, int, float, bool, None, Dict[Any, Any], List[Any]]

DEFAULT_INPUT = Path("Documentation/References/TradeSkillInfo/TradeskillIdData.lua")
DEFAULT_OUTPUT = Path("tradeskillinfo_recipes.json")
DEFAULT_INFDATA = Path("infdata_item_names.json")
TABLE_NAMES = ["combines", "recipes", "components", "specialcases"]

PROFESSION_NAMES = {
    "A": "Alchemy",
    "B": "Blacksmithing",
    "Ba": "Armorsmithing",
    "Bw": "Weaponsmithing",
    "Bws": "Master Swordsmithing",
    "Bwh": "Master Hammersmithing",
    "Bwx": "Master Axesmithing",
    "N": "Enchanting",
    "E": "Engineering",
    "Eg": "Gnomish Engineering",
    "Eb": "Goblin Engineering",
    "J": "Jewelcrafting",
    "L": "Leatherworking",
    "Ld": "Dragonscale Leatherworking",
    "Le": "Elemental Leatherworking",
    "Lt": "Tribal Leatherworking",
    "T": "Tailoring",
    "C": "Cooking",
    "X": "First Aid",
    "P": "Poisons",
    "M": "Mining",
    "I": "Inscription",
    "Y": "Smelting",
}

SOURCE_NAMES = {
    "V": "Vendor",
    "Va": "Alliance Vendor",
    "Vh": "Horde Vendor",
    "D": "Dropped",
    "Da": "Dropped for Alliance",
    "Dh": "Dropped for Horde",
    "C": "Crafted",
    "Ca": "Alchemy",
    "Cb": "Blacksmithing",
    "Cn": "Enchanting",
    "Ce": "Engineering",
    "Cj": "Jewelcrafting",
    "Cl": "Leatherworking",
    "Ct": "Tailoring",
    "Cc": "Cooking",
    "Cf": "First Aid",
    "Cp": "Poisons",
    "Cs": "Smelting",
    "M": "Mined",
    "H": "Herbalism",
    "S": "Skinned",
    "F": "Fished",
    "E": "Disenchanted",
    "G": "Gathered",
    "P": "Pickpocketed",
    "Q": "Quest",
    "Qa": "Alliance Quest",
    "Qh": "Horde Quest",
    "T": "Trainer",
    "Ts": "Specialist Trainer",
    "X": "Not Currently Obtainable",
    "R": "Prospecting",
    "U": "Unknown",
    "I": "Milling",
    "Ci": "Inscription",
}

SOURCE_TOKEN_RE = re.compile(r"[A-Z][a-z]*\d*")

TABLE_ASSIGN_RE = re.compile(
    r"TradeskillInfo\.vars\.(?P<name>\w+)\s*=\s*\{",
)


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("tsi_extract")
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(levelname)s: %(message)s")
    )
    logger.handlers = []
    logger.addHandler(handler)
    return logger


def read_file_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def load_infdata(path: Path, logger: logging.Logger) -> Dict[str, str]:
    if not path.exists():
        logger.warning("InfData mapping not found: %s", path)
        return {}

    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception as exc:
        logger.warning("Failed to load infdata JSON: %s", exc)
        return {}


def extract_table_text(source: str, table_name: str) -> Optional[str]:
    start_match = re.search(
        rf"TradeskillInfo\.vars\.{re.escape(table_name)}\s*=\s*\{{",
        source,
    )
    if not start_match:
        return None

    start = source.index("{", start_match.end() - 1)
    depth = 0
    in_string = False
    escape = False
    quote_char = ""
    end = start

    for idx, char in enumerate(source[start:], start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote_char:
                in_string = False
            continue

        if char in {'"', "'"}:
            in_string = True
            quote_char = char
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = idx
                break

    if depth != 0:
        return None

    return source[start : end + 1]


def remove_lua_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if "--" not in line:
            lines.append(line)
            continue
        parts = line.split("--", 1)
        lines.append(parts[0])
    return "\n".join(lines)


def parse_lua_table(text: str, logger: logging.Logger) -> LuaValue:
    cleaned = remove_lua_comments(text).strip()
    if not cleaned:
        raise ValueError("Lua table text is empty after comment removal")
    try:
        return slpp.decode(cleaned)
    except Exception as exc:
        logger.error("slpp.decode failed for extracted Lua table: %s", exc)
        raise


def load_lua_tables(path: Path, logger: logging.Logger) -> Dict[str, Any]:
    source = read_file_text(path)
    tables: Dict[str, Any] = {}

    for name in TABLE_NAMES:
        block = extract_table_text(source, name)
        if block is None:
            logger.warning("Could not find table %s in %s", name, path)
            tables[name] = {}
            continue
        parsed = parse_lua_table(block, logger)
        tables[name] = parsed

    return tables


def parse_skill_spec_level(value: str) -> Dict[str, Any]:
    profession = ""
    specialization = ""
    skill_level = None
    difficulty_tiers: List[int] = []

    if not value:
        return {
            "profession": profession,
            "specialization": specialization,
            "skill_level": skill_level,
            "difficulty_tiers": difficulty_tiers,
        }

    profession_match = re.match(r"(?P<prof>[A-Z]+)(?P<rest>.*)", value)
    if profession_match:
        profession = profession_match.group("prof")
        rest = profession_match.group("rest")
        tier_strings = rest.split("/") if rest else []
    else:
        tier_strings = value.split("/")

    if tier_strings:
        first = tier_strings[0]
        if first.isdigit():
            skill_level = int(first)
            difficulty_tiers = [int(item) for item in tier_strings]
        elif first.startswith("A") and first[1:].isdigit():
            profession = first[0]
            skill_level = int(first[1:])
            difficulty_tiers = [int(item) for item in tier_strings[1:] if item.isdigit()]
        else:
            difficulty_tiers = [int(item) for item in tier_strings if item.isdigit()]

    if profession and len(profession) > 1 and not skill_level:
        specialization = profession[1:]
        profession = profession[0]

    return {
        "profession": profession,
        "specialization": specialization,
        "skill_level": skill_level,
        "difficulty_tiers": difficulty_tiers,
    }


def parse_components(value: str) -> List[Dict[str, Any]]:
    components: List[Dict[str, Any]] = []
    if not value:
        return components

    tokens = value.split()
    for token in tokens:
        if ":" in token:
            item_id_str, qty_str = token.split(":", 1)
            try:
                item_id = int(item_id_str)
            except ValueError:
                continue
            quantity = int(qty_str) if qty_str.isdigit() else 1
        else:
            try:
                item_id = int(token)
            except ValueError:
                continue
            quantity = 1
        components.append({"item_id": item_id, "quantity": quantity})
    return components


def parse_yield_range(value: str, logger: logging.Logger) -> Tuple[int, int]:
    if not value:
        return 1, 1

    value = value.strip()
    if value.isdigit():
        yield_count = int(value)
        return yield_count, yield_count

    range_match = re.match(r"^(?P<min>\d+)-(?P<max>\d+)$", value)
    if range_match:
        min_yield = int(range_match.group("min"))
        max_yield = int(range_match.group("max"))
        if min_yield <= max_yield:
            return min_yield, max_yield

    logger.debug("Unrecognized yield format for key %s: %s", value, value)
    return 1, 1


def parse_combine_entry(key: Any, value: Any, logger: logging.Logger) -> Optional[Dict[str, Any]]:
    try:
        item_key = int(key)
    except (TypeError, ValueError):
        logger.debug("Skipping non-integer combine key: %s", key)
        return None

    if not isinstance(value, str):
        logger.debug("Skipping non-string combine value for key %s", item_key)
        return None

    parts = value.split("|")
    spell_id = None
    skill_spec_level = ""
    component_string = ""
    recipe_id = None
    yield_min = 1
    yield_max = 1
    output_override = None

    if len(parts) >= 1 and parts[0]:
        spell_id = int(parts[0]) if parts[0].isdigit() else None
    if len(parts) >= 2:
        skill_spec_level = parts[1]
    if len(parts) >= 3:
        component_string = parts[2]
    if len(parts) >= 4 and parts[3]:
        recipe_id = int(parts[3]) if parts[3].isdigit() else None
    if len(parts) >= 5 and parts[4]:
        yield_min, yield_max = parse_yield_range(parts[4], logger)
    if len(parts) >= 6 and parts[5]:
        output_override = int(parts[5]) if parts[5].isdigit() else None

    skill_data = parse_skill_spec_level(skill_spec_level)
    components = parse_components(component_string)
    output_item_id = output_override if output_override is not None else item_key

    if output_item_id is None:
        return None

    return {
        "output_item_id": output_item_id,
        "spell_id": spell_id,
        "profession": skill_data["profession"],
        "specialization": skill_data["specialization"],
        "skill_level": skill_data["skill_level"],
        "difficulty_tiers": skill_data["difficulty_tiers"],
        "yield_min": yield_min,
        "yield_max": yield_max,
        "recipe_id": recipe_id,
        "components": components,
    }


def split_source_tokens(source: str) -> List[str]:
    return SOURCE_TOKEN_RE.findall(source)


def enrich_recipe_object(
    recipe: Dict[str, Any],
    recipes: Dict[Any, Any],
    components: Dict[Any, Any],
    infdata: Dict[str, str],
    include_source_labels: bool = True,
    include_recipe_source: bool = True,
) -> None:
    if recipe["profession"]:
        recipe["profession_label"] = PROFESSION_NAMES.get(
            recipe["profession"], recipe["profession"]
        )
    output_name = infdata.get(str(recipe["output_item_id"]))
    if output_name:
        recipe["output_item_name"] = output_name

    if include_recipe_source and recipe["recipe_id"] is not None:
        recipe_source = recipes.get(recipe["recipe_id"])
        if recipe_source is not None:
            recipe["recipe_source"] = recipe_source
            if include_source_labels:
                tokens = split_source_tokens(recipe_source)
                recipe["recipe_source_label"] = ", ".join(
                    SOURCE_NAMES.get(token, token) for token in tokens
                )

    for component in recipe["components"]:
        component_name = infdata.get(str(component["item_id"]))
        if component_name:
            component["item_name"] = component_name
        source = components.get(component["item_id"])
        if source is not None:
            component["source"] = source
            if include_source_labels:
                tokens = split_source_tokens(source)
                labels = [SOURCE_NAMES.get(token, token) for token in tokens]
                component["source_label"] = ", ".join(labels)


def build_recipe_json(
    combines: Dict[Any, Any], include_enchantments: bool = False
) -> List[Dict[str, Any]]:
    recipes: List[Dict[str, Any]] = []
    for key, value in combines.items():
        if isinstance(key, str) and key.startswith("-"):
            numeric_key = key[1:]
        else:
            numeric_key = key
        try:
            item_key = int(numeric_key)
        except (TypeError, ValueError):
            continue

        if item_key < 0 and not include_enchantments:
            continue

        result = parse_combine_entry(key, value, logger)
        if result is not None:
            recipes.append(result)
    return recipes


def enrich_recipes(
    recipe_list: List[Dict[str, Any]],
    recipes: Dict[Any, Any],
    components: Dict[Any, Any],
    specialcases: Dict[Any, Any],
    infdata: Dict[str, str],
    include_source_labels: bool = True,
    include_recipe_source: bool = True,
) -> None:
    for recipe in recipe_list:
        enrich_recipe_object(
            recipe,
            recipes,
            components,
            infdata,
            include_source_labels=include_source_labels,
            include_recipe_source=include_recipe_source,
        )

    if specialcases:
        for recipe in recipe_list:
            recipe["specialcase_alias"] = None
        # Specialcases are alias mappings and are not directly applied by default.


def write_json(path: Path, data: List[Dict[str, Any]]) -> None:
    path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract TradeSkillInfo recipe formulas from TradeskillIdData.lua "
            "and emit basic JSON." 
        )
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=DEFAULT_INPUT,
        help="Path to TradeskillIdData.lua",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to write extracted recipe JSON",
    )
    parser.add_argument(
        "--include-enchantments",
        action="store_true",
        help="Include negative-key enchantment recipes in output",
    )
    parser.add_argument(
        "--no-enrich",
        dest="enrich",
        action="store_false",
        help="Do not enrich components with source metadata",
    )
    parser.add_argument(
        "--infdata",
        type=Path,
        default=DEFAULT_INFDATA,
        help="Path to infdata_item_names.json for name lookup",
    )
    return parser.parse_args()


def main() -> int:
    global logger
    logger = setup_logger()
    args = parse_arguments()

    if not args.input.exists():
        logger.error("Input file not found: %s", args.input)
        return 1

    tables = load_lua_tables(args.input, logger)
    combines = tables.get("combines", {})
    recipes_meta = tables.get("recipes", {})
    components_meta = tables.get("components", {})
    specialcases_meta = tables.get("specialcases", {})

    recipe_list = build_recipe_json(
        combines,
        include_enchantments=args.include_enchantments,
    )

    infdata = load_infdata(args.infdata, logger)

    enrich_recipes(
        recipe_list,
        recipes_meta,
        components_meta,
        specialcases_meta,
        infdata,
        include_source_labels=args.enrich,
        include_recipe_source=args.enrich,
    )

    write_json(args.output, recipe_list)
    logger.info(
        "Wrote %d recipes to %s", len(recipe_list), args.output
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
