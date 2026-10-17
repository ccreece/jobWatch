"""Read job rows out of sheet screenshots with a local vision model, and keep a
de-duplicated text file of every job seen.

Supports Ollama (default) and any OpenAI-compatible local server such as
LM Studio, llama.cpp's server or Jan.
"""

import base64
import io
import json
import re
import urllib.request
from datetime import datetime
from difflib import SequenceMatcher

# (field name used in the model's JSON, column header in the sheet)
COLUMNS = [
    ("studio", "Studio"),
    ("city", "City"),
    ("region", "Province/State/Region"),
    ("country", "Country"),
    ("title", "Job Title"),
    ("level", "Experience Level"),
    ("work_mode", "On-Site/Remote/Hybrid"),
    ("date", "Date"),
    ("source", "Source/Contact"),
    ("software", "Software/Programs"),
    ("notes", "Notes"),
]
FIELDS = [name for name, _ in COLUMNS]

# A job counts as already seen when these fields match (ignoring case,
# spacing and punctuation). Date is left out so a re-dated posting of the
# same job isn't listed twice; long fields like URLs and notes are left out
# because the model is most likely to misread them slightly between runs.
KEY_FIELDS = ("studio", "title", "city", "country", "level", "work_mode")
# How alike (0-1) each of those fields must be to count as the same job, so a
# letter or two misread by the model doesn't create a duplicate.
FUZZY_MATCH = 0.85

SEPARATOR = " | "
DEFAULT_URLS = {"ollama": "http://localhost:11434", "openai": "http://localhost:1234/v1"}

LAYOUT_FILE = "layout.json"
REPLIES_FILE = "replies.txt"
# Row edges in layout.json are accurate to within the capture's scan step.
SCAN_TOLERANCE_PX = 3

PROMPT_TEMPLATE = """This image is cut from a Google Sheets table of job postings.
The top band is the table's column header row. Below it {count_text}, separated
by grid lines. The columns, from left to right, are:
{headers}.

Transcribe {each_text}, from top to bottom. Copy the text exactly as shown.
Use "" for empty cells. When text wraps onto several lines, join the lines
with a space, except inside URLs and email addresses, which must be joined
with no space.

Reply with JSON only, in exactly this form, with {count} item(s) in "rows":
{{"rows": [{{{fields}}}]}}"""


def build_prompt(count):
    return PROMPT_TEMPLATE.format(
        count=count,
        count_text="is 1 job row" if count == 1 else f"are {count} job rows",
        each_text="the job row" if count == 1 else f"all {count} job rows",
        headers=", ".join(header for _, header in COLUMNS),
        fields=", ".join(f'"{name}": "..."' for name in FIELDS),
    )


def _post_json(url, payload, timeout):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def ask_model(png_bytes, prompt, model, api="ollama", base_url=None, timeout=900):
    """Send one image and prompt to the model and return its raw text reply."""
    base_url = (base_url or DEFAULT_URLS[api]).rstrip("/")
    image_b64 = base64.b64encode(png_bytes).decode()
    if api == "ollama":
        reply = _post_json(f"{base_url}/api/chat", {
            "model": model,
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
            "stream": False,
            "format": "json",
            # The default context is too small for a large image plus ~20 rows of output.
            "options": {"temperature": 0, "num_ctx": 16384},
        }, timeout)
        return reply["message"]["content"]
    reply = _post_json(f"{base_url}/chat/completions", {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ]}],
        "temperature": 0,
    }, timeout)
    return reply["choices"][0]["message"]["content"]


def _clean(value):
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text.replace("|", "/")).strip()


def parse_rows(text):
    """Turn the model's reply into a list of row dicts, tolerating code fences and chatter."""
    text = re.sub(r"```(?:json)?", "", text)
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start == -1:
        raise ValueError(f"no JSON in model reply: {text[:200]!r}")
    data, _ = json.JSONDecoder().raw_decode(text[start:])
    if isinstance(data, dict):
        data = data.get("rows", [])
    return [{name: _clean(item.get(name)) for name in FIELDS} for item in data if isinstance(item, dict)]


def plan_batches(layout, first_row, last_row, header_row, batch_size):
    """Group the wanted rows into (image name, [row numbers]) batches.

    Each row is taken from the screenshot where it shows tallest, i.e. the
    one where it isn't cut off. Rows in a batch are consecutive and come
    from the same screenshot, so they can be cropped as one block.
    """
    batches = []
    for number in range(first_row, last_row + 1):
        views = [(band[1] - band[0], image) for image, bands in layout.items()
                 for row, band in bands.items() if int(row) == number and str(header_row) in bands]
        if not views:
            continue
        # Heights wobble by a pixel or two between screenshots, so take the
        # earliest view that's near-full height, keeping batches together.
        tallest = max(height for height, _ in views)
        image = min(image for height, image in views if height >= tallest - 2 * SCAN_TOLERANCE_PX)
        last = batches[-1] if batches else None
        if last and last[0] == image and len(last[1]) < batch_size and last[1][-1] == number - 1:
            last[1].append(number)
        else:
            batches.append((image, [number]))
    return batches


def crop_rows(image, bands, header_row, numbers):
    """Return PNG bytes of the header row stacked above the given rows."""
    from PIL import Image  # imported here so capturing works without Pillow

    pad = SCAN_TOLERANCE_PX

    def band_box(top, bottom):
        return (0, max(top - pad, 0), image.width, min(bottom + pad, image.height))

    header = image.crop(band_box(*bands[str(header_row)]))
    body = image.crop(band_box(bands[str(numbers[0])][0], bands[str(numbers[-1])][1]))
    combined = Image.new("RGB", (image.width, header.height + body.height), "white")
    combined.paste(header, (0, 0))
    combined.paste(body, (0, header.height))
    buffer = io.BytesIO()
    combined.save(buffer, format="PNG")
    return buffer.getvalue()


def extract_capture(capture_dir, model, api, base_url, first_row, last_row, batch_size, log):
    """Read the rows of one capture folder with the model and return them, in sheet order."""
    from PIL import Image

    layout = json.loads((capture_dir / LAYOUT_FILE).read_text())
    header_row = first_row - 1
    batches = plan_batches(layout, first_row, last_row, header_row, batch_size)
    images = {name: Image.open(capture_dir / name).convert("RGB") for name in {b[0] for b in batches}}
    replies_log = (capture_dir / REPLIES_FILE).open("a", encoding="utf-8")

    def read(image_name, numbers):
        prompt = build_prompt(len(numbers))
        png = crop_rows(images[image_name], layout[image_name], header_row, numbers)
        reply = ask_model(png, prompt, model, api, base_url)
        replies_log.write(f"--- {image_name} rows {numbers[0]}-{numbers[-1]} ({model})\n{reply}\n")
        replies_log.flush()
        return parse_rows(reply)

    by_number = {}
    with replies_log:
        for image_name, numbers in batches:
            log(f"Reading rows {numbers[0]}-{numbers[-1]} from {image_name} with {model}...")
            try:
                rows = read(image_name, numbers)
                if len(rows) != len(numbers) and len(numbers) > 1:
                    log(f"  got {len(rows)} rows instead of {len(numbers)}; retrying one at a time")
                    rows = []
                    for number in numbers:
                        single = read(image_name, [number])
                        rows.append(single[0] if single else None)
            except Exception as exc:
                log(f"  could not read rows {numbers[0]}-{numbers[-1]}: {exc}")
                continue
            for number, row in zip(numbers, rows):
                if row:
                    by_number[number] = row

    missing = [n for n in range(first_row, last_row + 1) if n not in by_number]
    if missing:
        log(f"  rows not read: {', '.join(map(str, missing))}")
    return [by_number[n] for n in sorted(by_number)]


def job_key(fields):
    return tuple(re.sub(r"[^a-z0-9]", "", fields[name].lower()) for name in KEY_FIELDS)


def is_known(key, seen):
    """True if ``key`` matches a seen key, allowing small misreadings in each field.

    Fields are compared one by one, so e.g. "Qvisen" still matches "Qvisten",
    but "Mid" never matches "Senior" and "Toronto" never matches "Montreal".
    """
    if key in seen:
        return True
    for other in seen:
        if all(a == b or (a and b and SequenceMatcher(None, a, b).ratio() >= FUZZY_MATCH)
               for a, b in zip(key, other)):
            return True
    return False


def known_keys(jobs_file):
    keys = set()
    if not jobs_file.exists():
        return keys
    for line in jobs_file.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith(("#", "===")):
            continue
        values = line.split(SEPARATOR)
        values += [""] * (len(FIELDS) - len(values))
        keys.add(job_key(dict(zip(FIELDS, values))))
    return keys


def append_new_jobs(rows, jobs_file, captured_at=None):
    """Append rows not already in ``jobs_file``; return how many were added."""
    seen = known_keys(jobs_file)
    new_lines = []
    for row in rows:
        key = job_key(row)
        if is_known(key, seen):
            continue
        seen.add(key)
        new_lines.append(SEPARATOR.join(row[name] for name in FIELDS))
    if not new_lines:
        return 0
    captured_at = captured_at or datetime.now()
    with jobs_file.open("a", encoding="utf-8") as f:
        if f.tell() == 0:
            f.write("# " + SEPARATOR.join(header for _, header in COLUMNS) + "\n")
        f.write(f"\n=== {captured_at:%Y-%m-%d %H:%M} - {len(new_lines)} new ===\n")
        f.write("\n".join(new_lines) + "\n")
    return len(new_lines)
