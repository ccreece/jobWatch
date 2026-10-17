# jobwatch

Watches the [Animation/VFX/Game Industry Job Postings](https://docs.google.com/spreadsheets/d/1eR2oAXOuflr8CZeGoz3JTrsgNj3KuefbdXJOmNtjEVM/edit?gid=0#gid=0)
Google Sheet. Once an hour, at a random time within each hour, during set
hours of the day (08:00–20:00 by default), it:

1. **Screenshots the top 50 jobs.** The sheet is scrolled a step at a time,
   with a screenshot after each step, until sheet rows 3–52 have all been
   shown (usually 3 screenshots). Each capture is saved in its own folder,
   e.g. `screenshots/2026-10-16_09-17-21/01.png`, `02.png`, ... After each
   screenshot the tool also notes exactly where each sheet row sits in it
   (saved as `layout.json`), by clicking down column A and reading the cell
   reference box.
2. **Reads the screenshots with a local AI model** (optional) and appends any
   jobs it hasn't seen before to `jobs.txt`, next to the script. The model is
   shown the column headers plus a few rows at a time, cut from the
   screenshot where those rows are fully visible, and told exactly how many
   rows to read. The model's raw answers are kept in `replies.txt` in the
   capture folder, for checking.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

For text extraction you need a local **vision** model (one that can read
images). With [Ollama](https://ollama.com), good choices are:

| Model | Size | Notes |
|-------|------|-------|
| `qwen2.5vl:7b` | ~6 GB | Best at reading tables and text; recommended |
| `qwen2.5vl:3b` | ~3 GB | Faster, less accurate |
| `llama3.2-vision` | ~8 GB | Works, weaker at dense tables |
| `minicpm-v`, `llava` | 4–5 GB | Often miss rows or garble URLs |

Run `ollama list` to see what you have, or `ollama pull qwen2.5vl:7b` to get one.

## Usage

```bash
# Watch hourly, extracting jobs with an Ollama model
python jobwatch.py --model qwen2.5vl:7b

# One capture right now, to check everything works
python jobwatch.py --model qwen2.5vl:7b --once

# Using LM Studio (or another OpenAI-compatible server) instead of Ollama
python jobwatch.py --ai-api openai --model <model name shown in LM Studio>

# Re-read an earlier capture (e.g. after trying a different model)
python jobwatch.py --model qwen2.5vl:7b --extract screenshots/2026-10-16_09-17-21
```

Without `--model` it only saves screenshots; you can read them later with
`--extract`. Leave it running (in a terminal, `tmux`, or as a service); it
sleeps outside the active window. Press Ctrl+C to stop.

| Flag | Default | Meaning |
|------|---------|---------|
| `--model NAME` | none | Vision model to read screenshots with |
| `--ai-api` | `ollama` | `ollama`, or `openai` for LM Studio, llama.cpp server, Jan, etc. |
| `--ai-url URL` | `http://localhost:11434` / `http://localhost:1234/v1` | Model server address |
| `--rows-per-request N` | `5` | Rows shown to the model at a time; `1` is slowest but most accurate |
| `--jobs-file FILE` | `jobs.txt` next to the script | Where found jobs are written |
| `--rows N` | `50` | How many jobs from the top to capture |
| `--first-row N` | `3` | Sheet row number of the first job |
| `--start H` / `--end H` | `8` / `20` | Active hours |
| `--output DIR` | `screenshots/` next to the script | Where to save screenshots |
| `--timeout S` | `60` | Page-load timeout in seconds |
| `--once` | off | Capture once now and exit |
| `--extract FOLDER` | | Read an existing capture folder and exit |

## jobs.txt

One job per line, columns separated by ` | `, grouped under a heading for
each capture that found something new:

```
# Studio | City | Province/State/Region | Country | Job Title | Experience Level | On-Site/Remote/Hybrid | Date | Source/Contact | Software/Programs | Notes

=== 2026-10-16 09:17 - 2 new ===
Riot Games | Sydney | New South Wales | Australia | Technical Artist, VFX | Senior | Hybrid | October 16, 2026 | https://www.riotgames.com/en/j/8287372 | Unreal, Houdini, Python, C++, HLSL | For an unpublished R&D product
...
```

A job is skipped if one with the same studio, job title, city, country,
experience level and on-site/remote setting is already in the file. Spacing,
capitals and punctuation are ignored, and each of those fields only has to be
very similar (85%), so a letter or two misread by the model (e.g. "Qvisen"
for "Qvisten") doesn't create a duplicate, while "Mid" vs "Senior" or
"Bangalore" vs "Mumbai" still count as different jobs. Date, link and notes
aren't compared, so a job that's re-posted with a new date isn't listed
again. To start fresh, delete `jobs.txt`.

## Notes

- Each hourly capture lands at a random moment between minute 5 and minute 55
  of its hour, so the times differ every day.
- Timings use your computer's local time.
- If a capture or extraction fails (network down, model server not running),
  the error is logged and the tool carries on; the screenshots are kept.
- Local models can misread text, especially long URLs. Spot-check `jobs.txt`
  against the screenshots, and use a larger model if it's often wrong. If a
  model skips rows, try `--rows-per-request 1`.
- Speed depends heavily on your hardware: with a GPU each request takes a few
  seconds; on CPU only, a 3B model takes a minute or more per request.
