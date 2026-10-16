#!/usr/bin/env python3
"""jobwatch - screenshot a website once per hour, at irregular times, during set hours.

Every hour inside the active window (default 08:00-20:00) the page is
captured once, at a randomly chosen moment within that hour, so the visits
don't land on a predictable schedule. Screenshots are saved to a
``screenshots`` folder next to this script.

Usage:
    python jobwatch.py https://example.com/careers
    python jobwatch.py https://example.com --start 8 --end 20 --full-page
    python jobwatch.py https://example.com --once   # take one shot now and exit
"""

import argparse
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "screenshots"

# Keep each shot this many minutes away from the hour boundaries so two
# consecutive shots are never just a few seconds apart.
EDGE_MARGIN_MINUTES = 5


def log(message):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def slug_for(url):
    host = urlparse(url).netloc or "site"
    return re.sub(r"[^A-Za-z0-9.-]+", "_", host)


def take_screenshot(url, output_dir, full_page, timeout_s):
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{slug_for(url)}_{datetime.now():%Y-%m-%d_%H-%M-%S}.png"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 900})
            page.goto(url, wait_until="load", timeout=timeout_s * 1000)
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightError:
                pass  # Some sites never go idle; the loaded page is good enough.
            page.screenshot(path=str(path), full_page=full_page)
        finally:
            browser.close()
    return path


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
    output_dir = Path(args.output).resolve()
    log(f"Watching {args.url}")
    log(f"Active {args.start:02d}:00-{args.end:02d}:00, saving to {output_dir}")

    last_slot = None
    while True:
        slot, when = next_shot_time(datetime.now(), args.start, args.end, last_slot)
        log(f"Next screenshot at {when:%Y-%m-%d %H:%M:%S}")
        sleep_until(when)
        try:
            path = take_screenshot(args.url, output_dir, args.full_page, args.timeout)
            log(f"Saved {path.name}")
        except Exception as exc:  # keep running through network hiccups etc.
            log(f"Screenshot failed: {exc}")
        last_slot = slot


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("url", help="website to screenshot")
    parser.add_argument("--start", type=int, default=8, help="first active hour, 0-23 (default 8)")
    parser.add_argument("--end", type=int, default=20, help="hour the window closes, 1-24 (default 20)")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR),
                        help="folder for screenshots (default: screenshots/ next to this script)")
    parser.add_argument("--full-page", action="store_true", help="capture the whole scrollable page")
    parser.add_argument("--timeout", type=int, default=60, help="page load timeout in seconds (default 60)")
    parser.add_argument("--once", action="store_true", help="take a single screenshot now and exit")
    args = parser.parse_args(argv)

    if not args.url.startswith(("http://", "https://")):
        args.url = "https://" + args.url
    if not (0 <= args.start < args.end <= 24):
        parser.error("need 0 <= --start < --end <= 24")
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.once:
        path = take_screenshot(args.url, Path(args.output).resolve(), args.full_page, args.timeout)
        log(f"Saved {path}")
        return
    try:
        run(args)
    except KeyboardInterrupt:
        log("Stopped.")


if __name__ == "__main__":
    sys.exit(main())
