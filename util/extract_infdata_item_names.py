#!/usr/bin/env python3
"""Extract item ID to name mappings from InfData.lua into JSON.

This script parses Lua table entries from InfData.lua and emits a JSON mapping
of item IDs to display names for use by downstream AuctionAnalysis tools.
"""

import argparse
import json
import re
from pathlib import Path

ENTRY_RE = re.compile(r"^\s*\[(\d+)\]\s*=\s*\"[^\"]*\"\s*,?\s*--\s*(.+?)\s*$")


def parse_infdata(path: Path) -> dict:
    data: dict[str, str] = {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        match = ENTRY_RE.match(line)
        if not match:
            continue
        item_id = match.group(1)
        item_name = match.group(2).strip()
        if item_name:
            data[item_id] = item_name
    return data


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract item ID -> name mappings from InfData.lua into JSON."
        )
    )
    parser.add_argument(
        "--input",
        "-i",
        default="Documentation/References/informant/Data/InfData.lua",
        help="Path to InfData.lua",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="infdata_item_names.json",
        help="Path to write the JSON map.",
    )
    args = parser.parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    item_map = parse_infdata(input_path)
    output_path = Path(args.output)
    output_path.write_text(
        json.dumps(item_map, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote {len(item_map)} item mappings to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
