#!/usr/bin/env python3
"""Combine Informant item names with Auctioneer market-price statistics.

Auctioneer's displayed market price is calculated from enabled statistic
modules at runtime. This extractor uses Histogram's persisted median when it
is available, then falls back to Simple's persisted combined mean.

Example:
  python3 extract_item_market_data.py \
        --account "MyAccount" \
    --account-dir "/path/to/WTF/Accounts/MyAccount" \
    --server-key "My Realm Alliance"
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable, Optional

try:
    from tqdm import tqdm
except ImportError:

    def tqdm(iterable: Any, **_: Any) -> Any:
        return iterable


from AuctionAnalysis.auctioneer_rope_to_sqlite import default_input_path, parse_lua

debug = 0
generated_output_path: Optional[Path] = None


def debug_path(path: Path, account_dir: Optional[Path] = None) -> str:
    """Return a debug-safe path with the account name replaced by a marker."""
    del account_dir
    resolved = path.resolve()
    parts = list(resolved.parts)
    wow_root_index = next(
        (
            index
            for index, part in enumerate(parts)
            if part.lower() == "wow" or part.lower().startswith("world of warcraft")
        ),
        None,
    )
    if wow_root_index is not None:
        parts = ["<root>"] + parts[wow_root_index:]
    else:
        return path.name

    for index, part in enumerate(parts[:-1]):
        if part.lower() in {"account", "accounts"}:
            if index + 1 < len(parts):
                parts[index + 1] = "<redacted>"
            break

    return str(Path(*parts))


def debugLog1(message: str) -> None:
    if debug >= 1:
        print(f"[debug 1] {message}", file=sys.stderr)


def debugLog2(message: str) -> None:
    if debug >= 2:
        print(f"[debug 2] {message}", file=sys.stderr)


def debugLog3(message: str) -> None:
    if debug >= 3:
        print(f"[debug 3] {message}", file=sys.stderr)


def debug_server_key(server_key: str) -> str:
    """Return a debug-safe server key while preserving its faction suffix."""
    _, separator, faction = server_key.partition("-")
    if separator:
        return f"<redacted>-{faction}"
    return "<redacted>"


def debug_server_keys(server_keys: Iterable[str]) -> list[str]:
    return [debug_server_key(server_key) for server_key in server_keys]


def debug_key_sample(keys: Iterable[Any], limit: int = 10) -> list[str]:
    return [str(key) for key in sorted(keys, key=str)[:limit]]


def debug_value_sample(value: Any, limit: int = 160) -> str:
    text = repr(value)
    return text if len(text) <= limit else f"{text[:limit]}..."


def parse_saved_variable(path: Path, account_dir: Optional[Path] = None) -> Any:
    debugLog1(f"Parsing SavedVariables file: {debug_path(path, account_dir)}")
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        raise ValueError(f"Lua file is empty: {path}")
    debugLog2(f"Read {len(text)} character(s) from {debug_path(path, account_dir)}")
    parsed = parse_lua(text, debug=0, source=str(path))
    debugLog3(f"Parsed {debug_path(path, account_dir)} as {type(parsed).__name__}")
    return parsed


def find_saved_variable(account_dir: Path, names: Iterable[str]) -> Optional[Path]:
    wanted = {name.lower() for name in names}
    debug_location = debug_path(account_dir)
    debugLog2(f"Searching for {sorted(wanted)} under {debug_location}")
    if not account_dir.exists():
        debugLog1(f"Search directory does not exist: {debug_location}")
        return None
    if not account_dir.is_dir():
        debugLog1(f"Search path is not a directory: {debug_location}")
        return None

    saved_variables_dir = account_dir / "SavedVariables"
    if not saved_variables_dir.is_dir():
        debugLog1(f"SavedVariables directory does not exist under {debug_location}")
        return None

    lua_files = sorted(saved_variables_dir.glob("*.lua"))
    debugLog2(f"Found {len(lua_files)} Lua file(s) under " f"{debug_path(saved_variables_dir)}")
    if debug >= 3:
        for path in lua_files:
            debugLog3(f"Lua candidate: {debug_path(path, account_dir)}")

    candidates = [path for path in lua_files if path.name.lower() in wanted]
    debugLog2(f"Found {len(candidates)} matching SavedVariables file(s) under {debug_location}")
    if not candidates:
        debugLog3(f"No matching files for names: {sorted(wanted)}")
        return None

    selected = candidates[0]
    debugLog1(f"Using SavedVariables file: {debug_path(selected, account_dir)}")
    if len(candidates) > 1:
        debugLog2("Multiple matching files found; using the first sorted SavedVariables file")
    return selected


def as_map(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): nested for key, nested in value.items()}


def realm_data(saved: Any) -> dict[str, Any]:
    saved_map = as_map(saved)
    debugLog3(f"SavedVariables top-level keys: {debug_server_keys(sorted(saved_map))}")
    realms = as_map(saved_map.get("RealmData"))
    if not realms:
        direct_realms = {
            key: value
            for key, value in saved_map.items()
            if key.lower() not in {"version", "realmdata"} and isinstance(value, dict)
        }
        if direct_realms:
            debugLog2("RealmData wrapper missing; using direct top-level realm keys")
            realms = direct_realms
    debugLog2(f"Found {len(realms)} RealmData key(s): {debug_server_keys(sorted(realms))}")
    return realms


def select_server_data(realms: dict[str, Any], server_key: str) -> tuple[str, Any]:
    if server_key in realms:
        return server_key, realms[server_key]

    candidates = sorted(key for key in realms if key.startswith(f"{server_key}-"))
    if len(candidates) == 1:
        debugLog1(
            f"Resolved server key {debug_server_key(server_key)!r} "
            f"to {debug_server_key(candidates[0])!r}"
        )
        return candidates[0], realms[candidates[0]]
    if len(candidates) > 1:
        debugLog1(
            f"Server key {debug_server_key(server_key)!r} is ambiguous; "
            f"matching keys: {debug_server_keys(candidates)}"
        )
    else:
        debugLog1(f"Server key {debug_server_key(server_key)!r} was not found")
    return server_key, {}


def split_properties(item_string: Any) -> dict[str, str]:
    if not isinstance(item_string, str):
        return {}
    result: dict[str, str] = {}
    for entry in item_string.split("_"):
        property_name, separator, data = entry.partition("@")
        if not separator:
            property_name, separator, data = entry.partition(":")
        if separator:
            result[property_name] = data
    return result


def numbers(data: str) -> list[float]:
    values: list[float] = []
    for value in data.split(";"):
        try:
            values.append(float(value))
        except ValueError:
            values.append(0.0)
    return values


def simple_mean(daily_string: Any, means_string: Any) -> Optional[float]:
    daily = numbers(split_properties(daily_string).get("0", ""))
    means = numbers(split_properties(means_string).get("0", ""))
    if len(means) < 6:
        return None

    seen_days, _, avg3, avg7, avg14 = means[:5]
    day_average = daily[0] / daily[1] if len(daily) > 1 and daily[1] else None
    if seen_days <= 3:
        mean = avg3
        if day_average is not None:
            mean = (mean * seen_days + day_average) / (seen_days + 1)
        return mean or None

    values: list[tuple[float, float]] = []
    if day_average is not None:
        values.append((day_average, 1))
    weight = 3 - len(values)
    if seen_days < 6:
        weight = seen_days - 3
        if weight > 1:
            weight -= len(values)
    values.append((avg3, weight))
    if seen_days > 6:
        values.append((avg7, seen_days - 6 if seen_days < 10 else 4))
    if seen_days > 10:
        values.append((avg14, seen_days - 10 if seen_days < 17 else 7))

    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in values) / total_weight


def simple_statistics(daily_string: Any, means_string: Any) -> Optional[tuple[float, float, int]]:
    daily = numbers(split_properties(daily_string).get("0", ""))
    means = numbers(split_properties(means_string).get("0", ""))
    if len(means) < 6:
        return None

    seen_days, _, avg3, avg7, avg14 = means[:5]
    day_average = daily[0] / daily[1] if len(daily) > 1 and daily[1] else None
    values: list[tuple[float, float]] = []
    if day_average is not None:
        values.append((day_average, 1))
    weight = 3 - len(values)
    if seen_days < 6:
        weight = seen_days - 3
        if weight > 1:
            weight -= len(values)
    if seen_days <= 3:
        values = [(avg3, seen_days)]
    else:
        values.append((avg3, weight))
        if seen_days > 6:
            values.append((avg7, seen_days - 6 if seen_days < 10 else 4))
        if seen_days > 10:
            values.append((avg14, seen_days - 10 if seen_days < 17 else 7))

    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        return None
    mean = sum(value * weight for value, weight in values) / total_weight
    variance = sum(weight * (value - mean) ** 2 for value, weight in values) / total_weight
    return mean, math.sqrt(variance), int(seen_days)


def histogram_distribution(
    item_string: Any,
) -> Optional[tuple[int, int, float, float, list[float]]]:
    if isinstance(item_string, list) and len(item_string) == 1:
        item_string = item_string[0]
    if not isinstance(item_string, str):
        return None

    encoded = split_properties(item_string).get("0")
    if encoded is None:
        encoded = item_string
    if not encoded:
        return None
    sections = encoded.split("!")
    if len(sections) == 5:
        control_values = []
        for section in sections[:4]:
            parsed = numbers(section)
            if len(parsed) != 1:
                return None
            control_values.append(parsed[0])
        bucket_values = numbers(sections[4])
    elif len(sections) == 4:
        bounds = numbers(sections[0])
        control_values = bounds + numbers(sections[1]) + numbers(sections[2])
        bucket_values = numbers(sections[3])
    else:
        # Support the compact legacy representation used by older Auctioneer data.
        fields = encoded.split(";")
        if len(fields) < 5:
            return None
        try:
            control_values = [float(value) for value in fields[:4]]
        except ValueError:
            return None
        bucket_values = []
        for value in fields[4:]:
            bucket_values.extend(float(bucket) for bucket in value.split(",") if bucket)

    if len(control_values) < 4:
        return None
    minimum, maximum, step, count = control_values[:4]
    if not step or not count:
        return None
    return int(minimum), int(maximum), step, count, bucket_values


def histogram_median(item_string: Any) -> Optional[float]:
    distribution = histogram_distribution(item_string)
    if distribution is None:
        return None
    minimum, maximum, step, count, bucket_values = distribution
    target = count * 0.5
    cumulative = 0.0
    for index in range(minimum, maximum + 1):
        offset = index - minimum
        cumulative += bucket_values[offset] if offset < len(bucket_values) else 0
        if cumulative >= target:
            return index * step
    return None


def bell_pdf(mean: float, stddev: float, area: float = 1.0):
    stddev = max(stddev, 0.1)
    coefficient = area / (stddev * math.sqrt(2 * math.pi))
    denominator = 2 * stddev**2
    return lambda value: coefficient * math.exp(-((value - mean) ** 2) / denominator)


def histogram_pdf(item_string: Any):
    distribution = histogram_distribution(item_string)
    if distribution is None:
        return None
    minimum, maximum, step, count, bucket_values = distribution
    curve: dict[int, float] = {}
    cumulative = 0.0
    area = 0.0
    for index in range(minimum, maximum + 1):
        bucket_count = bucket_values[index - minimum] if index - minimum < len(bucket_values) else 0
        cumulative += bucket_count
        curve[index] = 1.0 if bucket_count == count else 1.0 - abs(2 * cumulative - count) / count
        area += step * curve[index]
    target_area = min(1.0, count / 30.0)
    multiplier = target_area / area if area > 0 else 0.0
    curve = {index: value * multiplier for index, value in curve.items()}
    lower = (minimum - 1) * step
    upper = (maximum + 1) * step
    return (
        lambda value: curve.get(math.floor(value / step), 0.0),
        lower,
        upper,
        target_area,
    )


def combined_market_price(
    simple_daily_data: Any,
    simple_means_data: Any,
    histogram_data: Any,
    tolerance: float = 0.08,
) -> Optional[tuple[float, dict[str, float]]]:
    pdfs = []
    module_prices: dict[str, float] = {}
    simple_stats = simple_statistics(simple_daily_data, simple_means_data)
    if simple_stats is not None:
        mean, stddev, seen_days = simple_stats
        if mean > 0 and seen_days >= 0:
            pdfs.append(
                (
                    bell_pdf(mean, max(stddev, mean * 0.01), 1.0),
                    mean - 3 * max(stddev, mean * 0.01),
                    mean + 3 * max(stddev, mean * 0.01),
                    1.0,
                )
            )
            module_prices["simple_mean"] = mean
    histogram = histogram_pdf(histogram_data)
    histogram_price = histogram_median(histogram_data)
    if histogram is not None and histogram_price is not None:
        pdfs.append(histogram)
        module_prices["histogram_median"] = histogram_price
    if not pdfs:
        return None

    lower = min(pdf[1] for pdf in pdfs)
    upper = max(pdf[2] for pdf in pdfs)
    delta = (upper - lower) * 0.01
    if delta <= 0:
        return None
    target = sum(pdf[3] for pdf in pdfs) * 0.5
    midpoint = last_midpoint = 0.0
    while True:
        total = 0.0
        midpoint = 0.0
        for value in [lower + delta * index for index in range(int((upper - lower) / delta) + 1)]:
            total += sum(pdf[0](value) for pdf in pdfs) * delta
            if total > target:
                midpoint = value
                break
        if midpoint <= 0:
            return None
        if last_midpoint and abs(midpoint - last_midpoint) / midpoint < tolerance:
            return midpoint, module_prices
        last_midpoint = midpoint
        delta *= 0.8


def copper_text(value: Optional[float]) -> Optional[str]:
    if value is None or value <= 0:
        return None
    copper = int(value + 0.5)
    gold, remainder = divmod(copper, 10000)
    silver, copper = divmod(remainder, 100)
    return f"{gold}g {silver:02d}s {copper:02d}c"


def load_names(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(item_id): str(name) for item_id, name in data.items()}


def run_self_test(output_path: Path) -> None:
    """Check Arcane Dust against the known in-game market-price values."""
    expected_prices = {
        "simple_mean": 33_450,
        "histogram_median": 34_037,
        "market_price": 32_207,
    }
    if not output_path.exists():
        raise SystemExit(f"Generated output not found: {output_path}")

    data = json.loads(output_path.read_text(encoding="utf-8"))
    record = data.get("22445")
    if record is None:
        raise SystemExit("Arcane Dust (22445) is missing from generated output")
    if "module_prices" not in record:
        raise SystemExit(
            "Generated output uses the old schema without module_prices; "
            "rerun the extractor before running --self-test"
        )

    actual_prices = {
        "simple_mean": record["module_prices"]["simple_mean"]["price_copper"],
        "histogram_median": record["module_prices"]["histogram_median"]["price_copper"],
        "market_price": record["market_price_copper"],
    }
    for price_name, expected in expected_prices.items():
        lower_bound = expected * 0.99
        upper_bound = expected * 1.01
        actual = actual_prices[price_name]
        if not lower_bound <= actual <= upper_bound:
            raise SystemExit(
                f"Arcane Dust {price_name} was {actual} copper; "
                f"expected about {expected} copper"
            )
    print("Arcane Dust self-test passed")


def extract(
    names: dict[str, str],
    account_dir: Path,
    server_key: str,
    faction: str,
    show_progress: bool = True,
) -> dict[str, dict[str, Any]]:
    qualified_server_key = (
        server_key if server_key.endswith(f"-{faction}") else f"{server_key}-{faction}"
    )
    debugLog1(
        f"Extracting market data for server={debug_server_key(qualified_server_key)!r} "
        f"(faction={faction!r})"
    )
    simple_path = find_saved_variable(
        account_dir,
        (
            "Auc-Stat-Simple.lua",
            "AucAdvancedStatSimple.lua",
            "AucAdvancedStatSimpleData.lua",
        ),
    )
    histogram_path = find_saved_variable(
        account_dir,
        (
            "Auc-Stat-Histogram.lua",
            "AucAdvancedStatHistogram.lua",
            "AucAdvancedStatHistogramData.lua",
        ),
    )
    simple_realms = (
        realm_data(parse_saved_variable(simple_path, account_dir)) if simple_path else {}
    )
    histogram_realms = (
        realm_data(parse_saved_variable(histogram_path, account_dir)) if histogram_path else {}
    )
    debugLog2(
        f"Loaded Simple realms={len(simple_realms)}, Histogram realms={len(histogram_realms)}"
    )
    debugLog2(f"Available Simple server keys: {debug_server_keys(sorted(simple_realms))}")
    debugLog2(f"Available Histogram server keys: {debug_server_keys(sorted(histogram_realms))}")
    selected_server_key, simple_server = select_server_data(simple_realms, qualified_server_key)
    _, histogram_server = select_server_data(histogram_realms, selected_server_key)

    simple_table = as_map(simple_server)
    histogram_table = as_map(histogram_server)
    simple_daily = as_map(simple_table.get("daily"))
    simple_means = as_map(simple_table.get("means"))
    requested_item_ids = list(names)
    debugLog1(
        f"Using server data for {debug_server_key(selected_server_key)!r}; "
        f"looking for {len(requested_item_ids)} item ID(s)"
    )
    debugLog2(
        "Simple data lookup: expected keys=['daily', 'means']; "
        f"available top-level keys={debug_key_sample(simple_table)}"
    )
    debugLog2(
        "Simple item lookup: "
        f"daily items={len(simple_daily)}, means items={len(simple_means)}; "
        f"requested item IDs={debug_key_sample(requested_item_ids)}"
    )
    debugLog2(
        "Histogram item lookup: "
        f"available items={len(histogram_table)}; "
        f"available item IDs={debug_key_sample(histogram_table)}"
    )
    if debug >= 3:
        available_simple_ids = set(simple_daily) | set(simple_means)
        missing_simple_ids = set(requested_item_ids) - available_simple_ids
        missing_histogram_ids = set(requested_item_ids) - set(histogram_table)
        simple_overlap = set(requested_item_ids) & available_simple_ids
        histogram_overlap = set(requested_item_ids) & set(histogram_table)
        debugLog3(
            f"Simple missing requested item IDs: {len(missing_simple_ids)}; "
            f"sample={debug_key_sample(missing_simple_ids)}"
        )
        debugLog3(
            f"Histogram missing requested item IDs: {len(missing_histogram_ids)}; "
            f"sample={debug_key_sample(missing_histogram_ids)}"
        )
        debugLog3(
            f"Requested ID overlap: Simple={len(simple_overlap)}, "
            f"Histogram={len(histogram_overlap)}"
        )
        if simple_overlap:
            sample_id = sorted(simple_overlap)[0]
            debugLog3(
                f"Simple sample item {sample_id}: "
                f"daily_type={type(simple_daily.get(sample_id)).__name__}, "
                f"means_type={type(simple_means.get(sample_id)).__name__}, "
                f"daily={debug_value_sample(simple_daily.get(sample_id))}, "
                f"means={debug_value_sample(simple_means.get(sample_id))}"
            )
        if histogram_overlap:
            sample_id = sorted(histogram_overlap)[0]
            debugLog3(
                f"Histogram sample item {sample_id}: "
                f"value_type={type(histogram_table.get(sample_id)).__name__}, "
                f"value={debug_value_sample(histogram_table.get(sample_id))}"
            )

    result: dict[str, dict[str, Any]] = {}
    histogram_available = 0
    simple_available = 0
    histogram_prices = 0
    simple_prices = 0
    item_records = tqdm(
        names.items(),
        desc="Extracting market data",
        unit="item",
        disable=not show_progress or not sys.stderr.isatty(),
    )
    for item_id, item_name in item_records:
        if item_id in histogram_table:
            histogram_available += 1
        if item_id in simple_daily or item_id in simple_means:
            simple_available += 1
        histogram_price = histogram_median(histogram_table.get(item_id))
        simple_price = simple_mean(
            simple_daily.get(item_id),
            simple_means.get(item_id),
        )
        if histogram_price is not None:
            histogram_prices += 1
        if simple_price is not None:
            simple_prices += 1
        combined = combined_market_price(
            simple_daily.get(item_id),
            simple_means.get(item_id),
            histogram_table.get(item_id),
        )
        if combined is None:
            continue
        price, module_prices = combined
        result[item_id] = {
            "item_name": item_name,
            "module_prices": {
                name: {
                    "price": copper_text(module_price),
                    "price_copper": int(module_price + 0.5),
                }
                for name, module_price in module_prices.items()
            },
            "market_price": copper_text(price),
            "market_price_copper": int(price + 0.5),
            "market_price_source": "combined_pdf",
        }
        debugLog3(
            f"Item {item_id} {item_name!r}: "
            f"{result[item_id]['market_price']} from {result[item_id]['market_price_source']}"
        )
    debugLog2(
        f"Price lookup totals: Histogram records={histogram_available}, "
        f"decoded={histogram_prices}; Simple records={simple_available}, "
        f"decoded={simple_prices}"
    )
    debugLog1(f"Extracted {len(result)} market record(s)")
    return result


def main() -> int:
    global debug

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-a",
        "--account",
        required=False,
        help="WoW account name under WTF/Account or WTF/Accounts",
    )
    parser.add_argument(
        "--account-dir",
        type=Path,
        default=None,
        help="Explicit account directory override (default: derived from --account)",
    )
    parser.add_argument(
        "-s",
        "--server-key",
        required=True,
        help="Auctioneer realm name, for example 'Lordaeron'",
    )
    parser.add_argument(
        "-f",
        "--faction",
        default="Horde",
        help="Auctioneer faction (default: Horde)",
    )
    parser.add_argument(
        "--debug",
        type=int,
        choices=range(4),
        default=0,
        help="Enable verbose logging from 0 (off) through 3 (details)",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress bars",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Check generated output against the Arcane Dust reference prices",
    )
    parser.add_argument("--names", type=Path, default=Path("infdata_item_names.json"))
    parser.add_argument("--output", type=Path, default=Path("item_market_data.json"))
    args = parser.parse_args()
    if not args.account:
        parser.error("the following arguments are required: -a/--account")
    if args.account_dir is None:
        args.account_dir = default_input_path(args.account)

    debug = args.debug
    debugLog1(f"Current working directory: {debug_path(Path.cwd())}")
    debugLog1(f"Resolved account directory: {debug_path(args.account_dir)}")
    debugLog2(f"Account directory exists: {args.account_dir.exists()}")
    debugLog2(f"Account directory is directory: {args.account_dir.is_dir()}")
    debugLog2(f"Names file: {debug_path(args.names)}")
    debugLog2(f"Names file exists: {args.names.exists()}")
    debugLog2(f"Output file: {debug_path(args.output)}")
    debugLog1("Starting market data extraction")
    if not args.account_dir.is_dir():
        raise SystemExit(f"Account directory not found: {args.account_dir}")
    data = extract(
        load_names(args.names),
        args.account_dir,
        args.server_key,
        args.faction,
        show_progress=not args.no_progress,
    )
    args.output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    global generated_output_path
    generated_output_path = args.output
    print(f"Wrote {len(data)} item market records to {args.output}")
    return 0


if __name__ == "__main__":
    exit_code = main()
    if exit_code == 0 and "--self-test" in sys.argv:
        run_self_test(generated_output_path or Path("item_market_data.json"))
    raise SystemExit(exit_code)
