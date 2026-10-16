# jobwatch

Takes a screenshot of a website once an hour, at a random time within each
hour, during set hours of the day (08:00–20:00 by default). Screenshots go into
a `screenshots/` folder next to `jobwatch.py`, named like
`example.com_2026-10-16_09-17-21.png`.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

## Usage

```bash
python jobwatch.py https://example.com/careers
```

Leave it running (in a terminal, `tmux`, or as a service). It sleeps outside
the active window and starts again the next morning. Press Ctrl+C to stop.

Options:

| Flag | Default | Meaning |
|------|---------|---------|
| `--start H` | `8` | First active hour (0–23) |
| `--end H` | `20` | Hour the window closes (1–24) |
| `--output DIR` | `screenshots/` next to the script | Where to save images |
| `--full-page` | off | Capture the whole scrollable page |
| `--timeout S` | `60` | Page-load timeout in seconds |
| `--once` | off | Take one screenshot now and exit, to test |

Each hourly shot lands at a random moment between minute 5 and minute 55 of
its hour, so two shots in a row are always at least 10 minutes apart and
the times are never the same from one day to the next. If a capture fails
(e.g. the network is down), the error is logged and the tool keeps going.
