#!/usr/bin/env python3
"""Analyze a sales.db produced by AuctionScanDiff and summarize sales metrics.

This script reads an AuctionScanDiff-generated sales database, computes
per-item sold listing counts, average prices, repost price drops, expired
listing counts, and daily sales summaries.
"""

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime

parser = argparse.ArgumentParser(description="Analyze sales.db")
parser.add_argument("db", nargs="?", default="sales.db")
args = parser.parse_args()
connection = sqlite3.connect(args.db)

sold = Counter()
repost = Counter()
expired = Counter()
sold_price = {}
repost_drop = {}
sold_stack_sizes = Counter()
sold_stack_sizes_by_item: dict[str, Counter[int]] = {}
sold_quantity_by_item: dict[str, int] = {}
sold_value_by_item: dict[str, float] = {}

daily_units_by_day: dict[str, int] = {}
daily_gold_by_day: dict[str, float] = {}

def parse_seen_time(listing: dict[str, object]) -> datetime | None:
    seen_time = listing.get("seenTime")
    if isinstance(seen_time, (int, float)):
        return datetime.fromtimestamp(seen_time)
    if isinstance(seen_time, str) and seen_time.isdigit():
        return datetime.fromtimestamp(int(seen_time))
    return None


def format_money(value: float | None) -> str:
    if value is None:
        return ""
    copper = int(round(value))
    sign = "" if copper >= 0 else "-"
    copper = abs(copper)
    gold = copper // 10000
    silver = (copper % 10000) // 100
    copper_remainder = copper % 100
    parts = []
    if gold:
        parts.append(f"{gold}g")
    if silver or gold:
        parts.append(f"{silver}s")
    parts.append(f"{copper_remainder}c")
    return sign + " ".join(parts)


def format_stack_breakdown(stack_counter: Counter[int], top_n: int = 3) -> str:
    if not stack_counter:
        return ""
    parts = []
    for stack_size, count in stack_counter.most_common(top_n):
        parts.append(f"{stack_size}×{count}")
    return ", ".join(parts)


def print_section(
    label,
    counter,
    values,
    value_label: str = "",
    extra_values: dict[str, float] | None = None,
    extra_label: str = "",
) -> None:
    if not counter:
        return
    print(f"\n{label}")
    print("-" * len(label))
    if value_label and extra_label:
        print(f"{'Count':>5}  {'Item':<28}  {value_label:<15}  {extra_label:<15}  {'Top stacks'}")
    elif value_label:
        print(f"{'Count':>5}  {'Item':<32}  {value_label:<15}  {'Top stacks'}")
    elif extra_label:
        print(f"{'Count':>5}  {'Item':<32}  {extra_label:<15}  {'Top stacks'}")
    else:
        print(f"{'Count':>5}  {'Item':<32}  {'Top stacks'}")

    for name, count in counter.most_common(10):
        avg_value = None
        if values.get(name):
            avg_value = sum(values[name]) / len(values[name])
        avg = format_money(avg_value)
        extra_value = extra_values.get(name) if extra_values else None
        extra = format_money(extra_value)
        stack_info = format_stack_breakdown(sold_stack_sizes_by_item.get(name, Counter()))
        if value_label and extra_label:
            print(f"{count:5d}  {name:<28}  {avg:<15}  {extra:<15}  {stack_info}")
        elif value_label:
            print(f"{count:5d}  {name:<32}  {avg:<15}  {stack_info}")
        elif extra_label:
            print(f"{count:5d}  {name:<32}  {extra:<15}  {stack_info}")
        else:
            print(f"{count:5d}  {name:<32}  {stack_info}")


def print_sold_section() -> None:
    if not sold:
        return
    print("\nSOLD")
    print("----")
    print(f"{'Listings':>8}  {'Units':>7}  {'Item':<26}  {'Avg Sale Price':<15}  {'Market Cap':<15}  {'Top stacks'}")
    for name, count in sold.most_common(10):
        units = sold_quantity_by_item.get(name, 0)
        avg_value = None
        if sold_price.get(name):
            avg_value = sum(sold_price[name]) / len(sold_price[name])
        avg = format_money(avg_value)
        market_cap = sold_value_by_item.get(name, 0.0)
        market = format_money(market_cap)
        stack_info = format_stack_breakdown(sold_stack_sizes_by_item.get(name, Counter()))
        print(f"{count:8d}  {units:7d}  {name:<26}  {avg:<15}  {market:<15}  {stack_info}")


def print_daily_summary() -> None:
    if not daily_units_by_day:
        return
    print("\nDAILY SALES SUMMARY")
    print("-------------------")
    print(f"{'Date':<10}  {'Units':>7}  {'Total Gold':>12}")
    total_units = 0
    total_gold = 0.0
    for date in sorted(daily_units_by_day):
        units = daily_units_by_day[date]
        gold = daily_gold_by_day.get(date, 0.0)
        total_units += units
        total_gold += gold
        print(f"{date:<10}  {units:7d}  {format_money(gold):>12}")

    days = len(daily_units_by_day)
    avg_units_per_day = total_units / days
    avg_gold_per_day = total_gold / days
    print("\nAVERAGE PER DAY")
    print(f"{'Days':<10}  {days:7d}")
    print(f"{'Units/day':<10}  {avg_units_per_day:7.2f}")
    print(f"{'Gold/day':<10}  {format_money(avg_gold_per_day):>12}")


def print_top_stack_sizes() -> None:
    if not sold_stack_sizes:
        return
    print("\nTOP STACK SIZES SOLD")
    print("--------------------")
    print(f"{'Stack':>5}  {'Count':>7}  {'Percent':>7}")
    total_sold = sum(sold_stack_sizes.values())
    for stack_size, count in sold_stack_sizes.most_common(10):
        percent = (count / total_sold) * 100 if total_sold else 0.0
        print(f"{stack_size:5d}  {count:7d}  {percent:6.1f}%")


for status, listing_json, repost_avg in connection.execute(
    "SELECT status, listing_json, repost_average_unit_price FROM sales"
):
    item = json.loads(listing_json)
    name = item.get("itemName") or str(item.get("itemId", ""))
    unit_price = next(
        (
            float(value) / max(1, int(item.get("stackSize") or 1))
            for field in ("buyoutPrice", "minBid", "curBid", "price")
            if (value := item.get(field)) and isinstance(value, (int, float)) and value > 0
        ),
        None,
    )

    if status == "likely_sold":
        sold[name] += 1
        quantity = max(1, int(item.get("stackSize") or 1))
        if unit_price is not None:
            sold_price.setdefault(name, []).append(unit_price)
            total_price = unit_price * quantity
        else:
            total_price = 0.0

        sold_quantity_by_item[name] = sold_quantity_by_item.get(name, 0) + quantity
        sold_value_by_item[name] = sold_value_by_item.get(name, 0.0) + total_price
        sold_stack_sizes[quantity] += 1
        sold_stack_sizes_by_item.setdefault(name, Counter())[quantity] += 1
        seen_dt = parse_seen_time(item)
        if seen_dt is not None:
            day_key = seen_dt.date().isoformat()
            daily_units_by_day[day_key] = daily_units_by_day.get(day_key, 0) + quantity
            daily_gold_by_day[day_key] = daily_gold_by_day.get(day_key, 0.0) + total_price
    elif status == "repost":
        repost[name] += 1
        if unit_price is not None and isinstance(repost_avg, (int, float)):
            repost_drop.setdefault(name, []).append(unit_price - repost_avg)
    elif status == "likely_expired":
        expired[name] += 1

print_sold_section()
print_section("REPOST", repost, repost_drop, "Avg Drop")
print_section("EXPIRED", expired, {}, "")
print_top_stack_sizes()
print_daily_summary()
