#!/usr/bin/env python3
"""Extract a contiguous combat log segment from a World of Warcraft combat log.

This script reads a WoWCombatLog.txt file, filters lines within a fixed
morning time window (default 08:55-12:03), writes the selected lines to a
new output file, archives the original log, and compresses the extracted
output into a ZIP archive.

It is intended for Windows/WSL2 environments and uses a progress bar when
reading the source log file.
"""

# Environment: Windows through WSL2, Python 3.12.3
import argparse
import datetime
import re
import zipfile
from pathlib import Path

from tqdm import tqdm


def parse_args():
    p = argparse.ArgumentParser(description="Extract WoW combat log lines from 08:55-12:03")
    p.add_argument("-b", "--before", type=int, default=0, help="minutes before 08:55")
    p.add_argument("-a", "--after", type=int, default=0, help="minutes after 12:03")
    return p.parse_args()


def main():
    args = parse_args()

    source = Path("WoWCombatLog.txt")
    if not source.exists():
        raise SystemExit(f"error: {source} not found")

    today = datetime.date.today()
    dateprefix = f"{today.month}/{today.day}"
    start = (datetime.datetime.combine(today, datetime.time(8, 55))
             + datetime.timedelta(minutes=args.before)).time()
    end = (datetime.datetime.combine(today, datetime.time(12, 3))
           + datetime.timedelta(minutes=args.after)).time()

    outname = f"logs {today.year}-{today.day:02d}-{today.month:02d}.txt"
    pattern = re.compile(rf"^{re.escape(dateprefix)}\s+(\d{{2}}:\d{{2}}:\d{{2}})\.")

    print("loading file metadata...")
    total_bytes = source.stat().st_size
    print(f"found {total_bytes} bytes, scanning for {dateprefix} {start}..{end}...")

    selected = 0
    first_idx = None
    last_idx = None

    with source.open("r", encoding="utf-8", errors="replace") as src, open(outname, "w", encoding="utf-8") as dst, \
            tqdm(total=total_bytes, unit="B", unit_scale=True, desc="reading") as pbar:
        prev_pos = src.buffer.tell()
        for idx, line in enumerate(src, start=1):
            m = pattern.match(line)
            if not m:
                current_pos = src.buffer.tell()
                pbar.update(current_pos - prev_pos)
                prev_pos = current_pos
                continue

            ts = datetime.time.fromisoformat(m.group(1))
            if ts < start or ts > end:
                current_pos = src.buffer.tell()
                pbar.update(current_pos - prev_pos)
                prev_pos = current_pos
                continue

            if first_idx is None:
                first_idx = idx
            last_idx = idx
            selected += 1
            dst.write(line)

            current_pos = src.buffer.tell()
            pbar.update(current_pos - prev_pos)
            prev_pos = current_pos

    print("finalizing output...")
    if selected == 0:
        raise SystemExit("error: no matching lines found")

    if selected != (last_idx - first_idx + 1):
        raise SystemExit("error: extracted lines are not fully contiguous in WoWCombatLog.txt")

    archive_name = (
        f"WoWCombatLogs_archive_{today.year}-{today.month:02d}-{today.day:02d}.txt"
    )
    source.rename(archive_name)

    zip_name = f"{outname}.zip"
    with zipfile.ZipFile(zip_name, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(outname, arcname=Path(outname).name)

    print(f"wrote {selected} lines to {outname}")
    print(f"created ZIP archive {zip_name}")
    print(f"renamed {source.name} to {archive_name}")


if __name__ == "__main__":
    main()