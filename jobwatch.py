#!/usr/bin/env python3
"""jobwatch - watch a Google Sheet of job postings once per hour, at irregular times.

Every hour inside the active window (default 08:00-20:00) the sheet is
captured once, at a randomly chosen moment within that hour, so the visits
don't land on a predictable schedule. Each capture scrolls through the sheet,
screenshotting as it goes, until the top 50 jobs have all been shown. The
screenshots are saved in a timestamped folder under ``screenshots`` next to
this script.

If a local vision model is given with --model, the screenshots are then read
by that model and any jobs not seen before are appended to ``jobs.txt``.

Usage:
    python jobwatch.py --model qwen2.5vl
    python jobwatch.py --model qwen2.5vl --once        # one capture now, then exit
    python jobwatch.py --model qwen2.5vl --extract screenshots/2026-10-16_09-17-21
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

import extract

SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1eR2oAXOuflr8CZeGoz3JTrsgNj3KuefbdXJOmNtjEVM/edit?gid=0#gid=0"
)
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "screenshots"
DEFAULT_JOBS_FILE = SCRIPT_DIR / "jobs.txt"
CAPTURE_DIR_FORMAT = "%Y-%m-%d_%H-%M-%S"

# Wide enough to show every column of the sheet (A-U) at 100% zoom.
VIEWPORT = {"width": 2100, "height": 1400}
# How far to scroll between screenshots. Less than a screenful, so consecutive
# shots overlap and rows cut off at the bottom of one are whole in the next.
SCROLL_PX = 700
MAX_SHOTS = 15
# Mapping rows: skip the column-letter strip at the top of the grid and the
# scrollbar at the bottom, and click every few pixels in between.
COLUMN_LETTERS_PX = 25
SCROLLBAR_PX = 20
SCAN_STEP_PX = 3

# Keep each shot this many minutes away from the hour boundaries so two
# consecutive shots are never just a few seconds apart.
EDGE_MARGIN_MINUTES = 5


def log(message):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def map_rows(page, grid_box, name_box):
    """Return {row number: [top, bottom]} for the rows showing in the grid, in screenshot pixels.

    The grid is drawn on a canvas, so the row numbers aren't in the page.
    Clicking a cell makes the name box show its address (e.g. "A41"), so
    clicking down column A every few pixels shows which row is where.
    Afterwards cell A1, in the frozen top row, is clicked so the selection
    outline stays out of the next screenshot's job rows.
    """
    x = grid_box["x"] + 60
    bands = {}
    for y in range(COLUMN_LETTERS_PX, int(grid_box["height"]) - SCROLLBAR_PX, SCAN_STEP_PX):
        page.mouse.click(x, grid_box["y"] + y)
        match = re.fullmatch(r"[A-Z]+(\d+)", name_box.input_value().strip())
        if match:
            band = bands.setdefault(int(match.group(1)), [y, y])
            band[1] = y
    page.mouse.click(x, grid_box["y"] + bands.get(1, [60, 60])[0] + 5)
    page.wait_for_timeout(300)
    return bands


def capture_sheet(url, output_dir, first_row, last_row, timeout_s):
    """Screenshot the sheet, scrolling until ``last_row`` has been shown.

    Returns the capture folder and the image paths. The folder also gets a
    layout.json recording where each sheet row sits in each screenshot.
    """
    capture_dir = output_dir / datetime.now().strftime(CAPTURE_DIR_FORMAT)
    capture_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    layout = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport=VIEWPORT)
            page.goto(url, wait_until="load", timeout=timeout_s * 1000)
            grid = page.locator("#waffle-grid-container")
            grid.wait_for(timeout=timeout_s * 1000)
            page.wait_for_timeout(5000)  # let the canvas finish drawing
            grid_box = grid.bounding_box()
            name_box = page.locator("#t-name-box")

            previous_bottom = None
            for i in range(1, MAX_SHOTS + 1):
                path = capture_dir / f"{i:02d}.png"
                page.screenshot(path=str(path), clip=grid_box)
                paths.append(path)
                bands = map_rows(page, grid_box, name_box)
                layout[path.name] = bands
                bottom = max(bands, default=None)
                # Stop once the row after last_row shows, so last_row itself is fully in view.
                if bottom is None or bottom > last_row or bottom == previous_bottom:
                    break
                previous_bottom = bottom
                page.mouse.move(grid_box["x"] + grid_box["width"] / 2,
                                grid_box["y"] + grid_box["height"] / 2)
                page.mouse.wheel(0, SCROLL_PX)
                page.wait_for_timeout(1500)
            else:
                log(f"Stopped after {MAX_SHOTS} screenshots without reaching row {last_row}")
        finally:
            browser.close()
            (capture_dir / extract.LAYOUT_FILE).write_text(json.dumps(layout, indent=1))
    return capture_dir, paths


def extract_jobs(capture_dir, args, captured_at):
    rows = extract.extract_capture(capture_dir, args.model, args.ai_api, args.ai_url,
                                   args.first_row, last_row(args), args.rows_per_request, log)
    added = extract.append_new_jobs(rows, Path(args.jobs_file), captured_at)
    log(f"Read {len(rows)} rows, {added} new job(s) added to {Path(args.jobs_file).name}")


def capture_and_extract(args):
    captured_at = datetime.now()
    capture_dir, paths = capture_sheet(args.url, Path(args.output).resolve(),
                                       args.first_row, last_row(args), args.timeout)
    log(f"Saved {len(paths)} screenshot(s) to {capture_dir}")
    if args.model:
        extract_jobs(capture_dir, args, captured_at)


def last_row(args):
    return args.first_row + args.rows - 1


def random_time_in_hour(hour_start, now):
    """Pick a random moment within the hour beginning at ``hour_start``, not before ``now``."""
    earliest = max(hour_start + timedelta(minutes=EDGE_MARGIN_MINUTES), now)
    latest = hour_start + timedelta(minutes=60 - EDGE_MARGIN_MINUTES)
    if earliest >= latest:
        return None
    span = (latest - earliest).total_seconds()
    return earliest + timedelta(seconds=random.uniform(0, span))


def next_shot_time(now, start_hour, end_hour, last_slot):
    """Return (slot, when) for the next capture after ``now``.

    A slot is the start of an hour in the active window; each slot gets one
    capture. ``last_slot`` is the slot already captured, if any.
    """
    slot = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(24 * 8):  # look up to a week ahead
        if start_hour <= slot.hour < end_hour and slot != last_slot:
            when = random_time_in_hour(slot, now)
            if when is not None:
                return slot, when
        slot += timedelta(hours=1)
    raise RuntimeError("no active hours found; check --start/--end")


def sleep_until(when):
    while True:
        remaining = (when - datetime.now()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 60))


def run(args):
    log(f"Watching {args.url}")
    log(f"Active {args.start:02d}:00-{args.end:02d}:00, "
        f"top {args.rows} rows, saving to {Path(args.output).resolve()}")
    if not args.model:
        log("No --model given: saving screenshots only, no text extraction")

    last_slot = None
    while True:
        slot, when = next_shot_time(datetime.now(), args.start, args.end, last_slot)
        log(f"Next capture at {when:%Y-%m-%d %H:%M:%S}")
        sleep_until(when)
        try:
            capture_and_extract(args)
        except Exception as exc:  # keep running through network hiccups etc.
            log(f"Capture failed: {exc}")
        last_slot = slot


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("url", nargs="?", default=SHEET_URL, help="Google Sheet to watch (default: the job postings sheet)")
    parser.add_argument("--start", type=int, default=8, help="first active hour, 0-23 (default 8)")
    parser.add_argument("--end", type=int, default=20, help="hour the window closes, 1-24 (default 20)")
    parser.add_argument("--rows", type=int, default=50, help="how many job rows to capture from the top (default 50)")
    parser.add_argument("--first-row", type=int, default=3, help="sheet row number of the first job (default 3)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR),
                        help="folder for screenshots (default: screenshots/ next to this script)")
    parser.add_argument("--timeout", type=int, default=60, help="page load timeout in seconds (default 60)")
    parser.add_argument("--once", action="store_true", help="capture once now and exit")

    ai = parser.add_argument_group("text extraction")
    ai.add_argument("--model", help="local vision model to read screenshots with, e.g. qwen2.5vl")
    ai.add_argument("--ai-api", choices=["ollama", "openai"], default="ollama",
                    help="'ollama', or 'openai' for OpenAI-compatible servers like LM Studio (default ollama)")
    ai.add_argument("--ai-url", help="model server address (default: localhost:11434 for ollama, "
                                     "localhost:1234/v1 for openai)")
    ai.add_argument("--rows-per-request", type=int, default=5,
                    help="sheet rows sent to the model at a time; lower is slower but more "
                         "accurate with small models (default 5)")
    ai.add_argument("--jobs-file", default=str(DEFAULT_JOBS_FILE),
                    help="text file of jobs found (default: jobs.txt next to this script)")
    ai.add_argument("--extract", metavar="FOLDER",
                    help="read an existing capture folder with --model and exit")
    args = parser.parse_args(argv)

    if not (0 <= args.start < args.end <= 24):
        parser.error("need 0 <= --start < --end <= 24")
    if args.extract and not args.model:
        parser.error("--extract needs --model")
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.extract:
        folder = Path(args.extract)
        try:
            captured_at = datetime.strptime(folder.name, CAPTURE_DIR_FORMAT)
        except ValueError:
            captured_at = datetime.now()
        extract_jobs(folder, args, captured_at)
        return
    if args.once:
        capture_and_extract(args)
        return
    try:
        run(args)
    except KeyboardInterrupt:
        log("Stopped.")


if __name__ == "__main__":
    sys.exit(main())
