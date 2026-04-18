"""Fun widget content generators.

Each function either fetches live content or generates something locally,
then returns a string body the printer layer can render (via text.render).
Widgets avoid hard dependencies on external APIs — if a call fails, they
fall back to offline content so the user always gets a print.
"""

from __future__ import annotations

import calendar
import datetime as dt
import random
import textwrap
from urllib.parse import quote, urlparse

import requests

import config


# ---------- offline banks ----------

FALLBACK_ADVICE = [
    "Do the hard thing first.",
    "Sleep on it.",
    "Write it down before you lose it.",
    "Ask one question less than you think you need to.",
    "The best time to start was yesterday. The next best time is now.",
    "Close the tabs you haven't touched in a week.",
    "Drink the water sitting next to you.",
    "Say the thing out loud before you commit it.",
    "Short walk, no phone.",
    "Delete before you refactor.",
]

# ---------- dice ----------

# Unicode die faces for visual flair
_DIE_FACES = {
    1: "\u2680",
    2: "\u2681",
    3: "\u2682",
    4: "\u2683",
    5: "\u2684",
    6: "\u2685",
}


def roll_dice(count: int = 2, sides: int = 6, mode: str = "standard") -> str:
    """Roll dice with a handful of playful preset modes.

    mode = standard  -> count×dN (normal)
         = coin      -> single heads/tails flip
         = d20       -> single d20 with crit callouts
         = advantage -> 2d20, keep highest (D&D)
         = disadv    -> 2d20, keep lowest
    """
    mode = (mode or "standard").lower()

    if mode == "coin":
        result = random.choice(["HEADS", "TAILS"])
        face = "(H)" if result == "HEADS" else "(T)"
        return "\n".join([
            "# COIN FLIP",
            "===",
            "",
            f"> {face}",
            "",
            f"# {result}",
            "",
            "---",
        ])

    if mode == "d20":
        roll = random.randint(1, 20)
        tag = "NAT 20!" if roll == 20 else ("crit fail" if roll == 1 else "")
        parts = [
            "# D20",
            "===",
            "",
            "> single roll",
            "",
            f"# {roll}",
        ]
        if tag:
            parts.append("")
            parts.append(f"> {tag}")
        parts.append("---")
        return "\n".join(parts)

    if mode in ("advantage", "disadv", "disadvantage"):
        rolls = [random.randint(1, 20), random.randint(1, 20)]
        adv = mode == "advantage"
        chosen = max(rolls) if adv else min(rolls)
        dropped = min(rolls) if adv else max(rolls)
        return "\n".join([
            f"# {'ADVANTAGE' if adv else 'DISADVANTAGE'}",
            "===",
            "",
            f"> 2d20 \u2192 keep {'highest' if adv else 'lowest'}",
            "",
            f"> {rolls[0]}   {rolls[1]}",
            "",
            "## kept",
            f"# {chosen}",
            "",
            f"> (dropped {dropped})",
            "---",
        ])

    count = max(1, min(20, int(count)))
    sides = max(2, min(100, int(sides)))
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls)

    lines = ["# DICE ROLL", "===", ""]
    lines.append(f"> {count}d{sides}")
    if sides == 6:
        lines.append("> " + "  ".join(_DIE_FACES[r] for r in rolls))
    lines.append("")
    lines.append("> " + " + ".join(str(r) for r in rolls) + f" = {total}")
    lines.append("")
    lines.append("## total")
    lines.append(f"# {total}")
    lines.append("===")
    return "\n".join(lines)


# ---------- advice ----------

def advice() -> str:
    """Pull an advice slip from adviceslip.com, fall back to offline bank."""
    text = None
    try:
        r = requests.get(
            "https://api.adviceslip.com/advice",
            timeout=3,
            headers={"User-Agent": "thermal-printer-gui"},
        )
        if r.ok:
            text = ((r.json() or {}).get("slip") or {}).get("advice", "")
    except Exception:
        pass
    if not text:
        text = random.choice(FALLBACK_ADVICE)

    wrapped = textwrap.fill(f'"{text}"', width=config.RECEIPT_WIDTH)
    return "\n".join(
        [
            "# ADVICE",
            "===",
            "",
            wrapped,
            "",
            "> adviceslip.com",
            "---",
        ]
    )


# ---------- weather ----------

def _fetch_wttr(location: str) -> dict | None:
    """Return parsed wttr.in json or None on failure."""
    try:
        r = requests.get(
            f"https://wttr.in/{quote(location, safe='')}",
            params={"format": "j1"},
            timeout=5,
            headers={"User-Agent": "thermal-printer-gui"},
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _weather_error_body(location: str, err: str) -> str:
    return "\n".join(
        [
            "# WEATHER",
            "===",
            "",
            "could not fetch weather for:",
            f"  {location}",
            "",
            textwrap.fill(err, width=config.RECEIPT_WIDTH),
            "",
            "---",
        ]
    )


def weather(location: str, days: int = 1) -> str:
    """Free, no-auth weather via wttr.in.

    days=1 -> current conditions card (compact)
    days=3 -> 3-day forecast card with hi/lo and afternoon summary
    """
    location = location.strip() or "Cupertino"
    days = max(1, min(3, int(days)))

    data = _fetch_wttr(location)
    if not data:
        return _weather_error_body(location, "wttr.in did not respond")

    try:
        current = data.get("current_condition", [{}])[0]
        area = data.get("nearest_area", [{}])[0]
        all_days = data.get("weather", [])

        city = (area.get("areaName") or [{}])[0].get("value", location)
        region = (area.get("region") or [{}])[0].get("value", "")
        header = [
            "# WEATHER",
            "===",
            "> " + city + (f", {region}" if region else ""),
            f"> {dt.date.today().strftime('%A, %b %-d')}",
            "",
        ]

        if days >= 2 and all_days:
            parts = header + ["---", "## forecast", ""]
            for i, day in enumerate(all_days[:days]):
                date_str = day.get("date", "")
                try:
                    d = dt.date.fromisoformat(date_str)
                    nice = d.strftime("%a %b %-d")
                except Exception:
                    nice = date_str
                hi = day.get("maxtempC", "?")
                lo = day.get("mintempC", "?")
                hourly = day.get("hourly") or []
                # Hourly entries are every 3 hours; index 4 ≈ noon.
                noon = hourly[4] if len(hourly) > 4 else (hourly[0] if hourly else {})
                desc = ((noon.get("weatherDesc") or [{}])[0]).get("value", "")
                rain = noon.get("chanceofrain", "0")
                parts.append(f"**{nice}**")
                if desc:
                    parts.append(f"  {desc}")
                parts.append(f"  hi {hi}\u00b0C / lo {lo}\u00b0C")
                parts.append(f"  rain {rain}%")
                parts.append("")
            astro = (all_days[0].get("astronomy") or [{}])[0]
            parts.extend([
                "---",
                f"sunrise  {astro.get('sunrise', '')}",
                f"sunset   {astro.get('sunset', '')}",
                "---",
            ])
            return "\n".join(parts)

        # Current-conditions (days == 1) card.
        desc = (current.get("weatherDesc") or [{}])[0].get("value", "")
        temp_c = current.get("temp_C", "?")
        feels_c = current.get("FeelsLikeC", "?")
        humidity = current.get("humidity", "?")
        wind_kph = current.get("windspeedKmph", "?")
        wind_dir = current.get("winddir16Point", "")
        today = all_days[0] if all_days else {}
        high_c = today.get("maxtempC", "?")
        low_c = today.get("mintempC", "?")
        astro = (today.get("astronomy") or [{}])[0]
        sunrise = astro.get("sunrise", "")
        sunset = astro.get("sunset", "")
        return "\n".join(header + [
            f"## {desc}",
            "",
            f"# {temp_c}\u00b0C",
            "",
            f"feels like      {feels_c}\u00b0C",
            f"humidity        {humidity}%",
            f"wind            {wind_kph} km/h {wind_dir}",
            "---",
            f"today hi/lo     {high_c}\u00b0 / {low_c}\u00b0C",
            f"sunrise         {sunrise}",
            f"sunset          {sunset}",
            "---",
        ])
    except Exception as e:
        return _weather_error_body(location, str(e))


# ---------- hacker news ----------

def hacker_news(count: int = 5) -> str:
    """Top N HN stories via the public Firebase API."""
    count = max(1, min(10, int(count)))
    try:
        r = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            timeout=5,
            headers={"User-Agent": "thermal-printer-gui"},
        )
        r.raise_for_status()
        ids = r.json()[:count]
    except Exception as e:
        return "\n".join([
            "# HACKER NEWS",
            "===",
            "",
            "(could not reach HN)",
            textwrap.fill(str(e), width=config.RECEIPT_WIDTH),
            "---",
        ])

    stories: list[dict] = []
    for sid in ids:
        try:
            sr = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                timeout=4,
                headers={"User-Agent": "thermal-printer-gui"},
            )
            if sr.ok:
                stories.append(sr.json() or {})
        except Exception:
            continue

    now = dt.datetime.now().strftime("%b %-d, %-I:%M %p").lower()
    parts = [
        "# HACKER NEWS",
        "===",
        f"> top {len(stories)} \u00b7 {now}",
        "",
    ]
    for i, s in enumerate(stories, 1):
        title = (s.get("title") or "").strip() or "(no title)"
        score = s.get("score", 0)
        comments = s.get("descendants", 0)
        by = s.get("by", "anon")
        url = s.get("url") or ""
        domain = ""
        if url:
            try:
                domain = urlparse(url).netloc.replace("www.", "")
            except Exception:
                domain = ""
        parts.append(f"**{i}.** {title}")
        meta = f"   {score} pts \u00b7 {comments} cmts \u00b7 {by}"
        parts.append(meta)
        if domain:
            parts.append(f"   {domain}")
        parts.append("")
    parts.append("---")
    return "\n".join(parts)


# ---------- on this day (wikipedia) ----------

def on_this_day(count: int = 4) -> str:
    """Historical events that happened on today's date, via Wikipedia."""
    count = max(1, min(8, int(count)))
    today = dt.date.today()
    url = ("https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/"
           f"{today.month:02d}/{today.day:02d}")
    try:
        r = requests.get(
            url,
            timeout=5,
            headers={"User-Agent": "thermal-printer-gui"},
        )
        r.raise_for_status()
        data = r.json() or {}
    except Exception as e:
        return "\n".join([
            "# ON THIS DAY",
            "===",
            f"> {today.strftime('%B %-d')}",
            "",
            "(could not reach wikipedia)",
            textwrap.fill(str(e), width=config.RECEIPT_WIDTH),
            "---",
        ])

    events = list(data.get("events") or [])
    if not events:
        return "\n".join([
            "# ON THIS DAY",
            "===",
            f"> {today.strftime('%B %-d')}",
            "",
            "(no events found)",
            "---",
        ])

    random.shuffle(events)
    picked = sorted(events[:count], key=lambda e: e.get("year", 0))

    parts = [
        "# ON THIS DAY",
        "===",
        f"> {today.strftime('%B %-d')}",
        "",
    ]
    for ev in picked:
        year = ev.get("year", "?")
        text = (ev.get("text") or "").strip() or "(untitled event)"
        parts.append(f"**{year}**")
        parts.append(textwrap.fill(text, width=config.RECEIPT_WIDTH))
        parts.append("")
    parts.append("---")
    return "\n".join(parts)


# ---------- calendar ----------

def calendar_month(year: int | None = None, month: int | None = None) -> str:
    """A printable month grid. Today is bracketed for easy spotting."""
    today = dt.date.today()
    y = int(year) if year else today.year
    m = int(month) if month else today.month
    if not (1 <= m <= 12):
        raise ValueError("month must be 1-12")

    cal = calendar.Calendar(firstweekday=6)  # Sunday first
    weeks = cal.monthdayscalendar(y, m)
    is_today_month = (y == today.year and m == today.month)

    header = f"{calendar.month_name[m]} {y}"

    lines = [
        f"# {header}",
        "===",
        "",
        "```",
        " Su   Mo   Tu   We   Th   Fr   Sa",
        " --   --   --   --   --   --   --",
    ]
    for week in weeks:
        cells = []
        for d in week:
            if d == 0:
                cells.append("    ")
            elif is_today_month and d == today.day:
                cells.append(f"[{d:2d}]")
            else:
                cells.append(f" {d:2d} ")
        lines.append(" " + " ".join(cells))
    lines.append("```")
    lines.append("")
    if is_today_month:
        lines.append(f"> today is {today.strftime('%A, %b %-d')}")
    lines.append("---")
    return "\n".join(lines)


# ---------- countdown ----------

def countdown(label: str, target_iso: str) -> str:
    """Days until (or since) a date. Input must be YYYY-MM-DD."""
    target_iso = (target_iso or "").strip()
    try:
        target = dt.date.fromisoformat(target_iso)
    except Exception:
        raise ValueError(f"invalid date: {target_iso!r} — use YYYY-MM-DD")

    today = dt.date.today()
    delta = (target - today).days
    label = (label or "the big day").strip() or "the big day"

    if delta > 0:
        headline = f"{delta} DAY" + ("S" if delta != 1 else "")
        sub = "until"
    elif delta == 0:
        headline = "TODAY"
        sub = "is"
    else:
        headline = f"{-delta} DAY" + ("S" if -delta != 1 else "")
        sub = "since"

    return "\n".join([
        f"# {headline}",
        "===",
        "",
        f"> {sub}",
        "",
        f"## {label}",
        "",
        f"> {target.strftime('%A, %B %-d, %Y')}",
        "",
        "---",
    ])


# ---------- habit tracker ----------

_DAY_LABELS = ["M", "T", "W", "T", "F", "S", "S"]


def habit_tracker(habits: list[str], days: int = 7) -> str:
    """A weekly grid: each habit gets a row of checkboxes, one per day."""
    cleaned = [h.strip()[:24] for h in habits if (h or "").strip()]
    if not cleaned:
        raise ValueError("at least one habit is required")
    days = max(1, min(7, int(days)))
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())

    # Right-side width: 7 days × 3 chars each = ~21
    name_w = config.RECEIPT_WIDTH - (days * 3 + 2)
    name_w = max(10, name_w)

    # Weekday header row
    day_labels_used = _DAY_LABELS[:days]
    header_right = "  ".join(day_labels_used)
    header = f"{'':{name_w}}  {header_right}"

    lines = [
        "# HABITS",
        "===",
        f"> week of {monday.strftime('%b %-d')}",
        "",
        "```",
        header,
    ]
    for h in cleaned:
        name = h[:name_w].ljust(name_w)
        row = "  ".join(["." for _ in range(days)])
        lines.append(f"{name}  {row}")
    lines.append("```")
    lines.append("")
    lines.append("> fill a dot each day")
    lines.append("---")
    return "\n".join(lines)


# ---------- morning briefing ----------

def morning_briefing_sections(location: str = "Cupertino") -> list[str]:
    """Combo widget split into rendering-friendly sections.

    Returned as a list, not a single joined string, so the print path can
    rasterize each section as its own image. A single giant image for the
    whole briefing overruns the printer's raster buffer on some units —
    paper comes out legible for the first chunk and then turns into noise.
    Chunking gives each section its own USB transfer and keeps output
    clean.

    Each subsection handles its own API errors internally — a flaky
    service just prints a "couldn't fetch" stub instead of aborting the
    whole briefing.
    """
    today = dt.date.today()
    now = dt.datetime.now()
    weekday = today.strftime("%A").upper()
    when = f"{today.strftime('%B %-d, %Y')} \u00b7 {now.strftime('%-I:%M %p').lower()}"

    intro = "\n".join([
        f"# {weekday}",
        "===",
        "> good morning",
        f"> {when}",
        "---",
    ])

    return [
        intro,
        weather(location, days=1),
        hacker_news(count=3),
        on_this_day(count=2),
    ]


def morning_briefing(location: str = "Cupertino") -> str:
    """Joined-string version of the briefing. Useful for previews and
    tests; the live print path should prefer morning_briefing_sections()
    so each block goes over USB as its own image."""
    return "\n\n".join(morning_briefing_sections(location))


# ---------- todo / receipt / label ----------

def todo(title: str, items: list[str]) -> str:
    parts = ["# " + (title.strip() or "TO DO"), "==="]
    date_str = dt.date.today().strftime("%A, %b %-d %Y")
    parts.append(f"> {date_str}")
    parts.append("")
    for it in items:
        it = it.strip()
        if not it:
            continue
        parts.append("[ ] " + it)
    parts.append("")
    parts.append("---")
    return "\n".join(parts)


def receipt(store: str, items: list[dict], tax_rate: float = 0.0, note: str = "") -> str:
    """Build a silly fake receipt. items = [{name, qty, price}]."""
    width = config.RECEIPT_WIDTH
    parts = ["# " + (store.strip() or "THE CORNER STORE")]
    parts.append("> 123 Anywhere Lane")
    parts.append("> (555) 867-5309")
    parts.append("===")
    parts.append("")

    subtotal = 0.0
    for it in items:
        name = str(it.get("name", "ITEM")).strip()[:width - 10]
        qty = int(it.get("qty", 1) or 1)
        price = float(it.get("price", 0.0) or 0.0)
        line_total = qty * price
        subtotal += line_total
        left = f"{qty} x {name}"
        right = f"${line_total:.2f}"
        pad = max(1, width - len(left) - len(right))
        parts.append(left + " " * pad + right)

    tax = subtotal * max(0.0, float(tax_rate or 0.0)) / 100.0
    total = subtotal + tax
    parts.append("-" * width)
    parts.append(_row("SUBTOTAL", f"${subtotal:.2f}", width))
    if tax_rate:
        parts.append(_row(f"TAX ({tax_rate:.2f}%)", f"${tax:.2f}", width))
    parts.append(_row("TOTAL", f"${total:.2f}", width))
    parts.append("===")
    parts.append("")
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts.append(f"> {stamp}")
    if note.strip():
        parts.append("")
        parts.append(textwrap.fill(note.strip(), width=width))
    parts.append("")
    parts.append("> thank you!")
    parts.append("---")
    return "\n".join(parts)


def _row(left: str, right: str, width: int) -> str:
    pad = max(1, width - len(left) - len(right))
    return left + " " * pad + right


def label(text: str, big: bool = True) -> str:
    """A chunky label — big centered text with dividers."""
    lines = ["==="]
    for chunk in textwrap.wrap(text.strip() or "LABEL", width=config.RECEIPT_WIDTH // 2):
        if big:
            lines.append(f"# {chunk}")
        else:
            lines.append(f"## {chunk}")
    lines.append("===")
    return "\n".join(lines)


# ---------- ASCII art ----------

ASCII_ART = {
    "cat": r"""
 /\_/\
( o.o )
 > ^ <
""",
    "dog": r"""
  / \__
 (    @\___
 /         O
/   (_____/
/_____/   U
""",
    "heart": r"""
  ** **
 *****:*
 *******
  *****
   ***
    *
""",
    "skull": r"""
   _____
  /     \
 | () () |
  \  ^  /
   |||||
   |||||
""",
    "coffee": r"""
( (
 ) )
.....
|   |]
\   /
 `-'
""",
    "ghost": r"""
  .-.
 (o o)
 | O \
  \   \
   `~~~'
""",
    "rocket": r"""
    /\
   /  \
  |    |
  |    |
  |    |
 /| /\ |\
/_|/  \|_\
   |||
   |||
   '-'
""",
    "fish": r"""
   ___
  /   \_
 /      \_______
|   o       __/
 \________/
""",
    "sun": r"""
    \ | /
   .-   -.
 -(  ^_^  )-
   '-___-'
    / | \
""",
    "star": r"""
      *
     ***
    *****
   *******
  *********
   *******
    *****
     ***
      *
""",
    "flower": r"""
    .-.
   (   )
  ( ( ) )
   (   )
    '-'
     |
    \|/
""",
    "snail": r"""
    ____
   /    \___
  |      O  |
  |         |
   \_______/_
          \ /
""",
}


def ascii_art(name: str) -> str:
    art = ASCII_ART.get(name.lower())
    if not art:
        art = random.choice(list(ASCII_ART.values()))
    return "## " + name.upper() + "\n---\n```\n" + art.strip("\n") + "\n```\n---"


# ---------- agenda-style "now" card ----------

def now_card() -> str:
    now = dt.datetime.now()
    iso = now.isocalendar()
    doy = now.timetuple().tm_yday
    year_frac = doy / 365
    # Progress bar through the year, 20 chars wide.
    filled = int(round(year_frac * 20))
    bar = "[" + "#" * filled + "." * (20 - filled) + "]"

    # Countdown to weekend
    wd = now.weekday()  # Mon=0 .. Sun=6
    if wd < 5:
        days_to_sat = 5 - wd
        weekend_note = f"{days_to_sat} day{'s' if days_to_sat != 1 else ''} to saturday"
    elif wd == 5:
        weekend_note = "it's saturday"
    else:
        weekend_note = "it's sunday"

    return "\n".join(
        [
            "# " + now.strftime("%a %b %-d"),
            "===",
            f"> {now.strftime('%I:%M %p').lower()}",
            "",
            f"week #{iso.week} \u00b7 day {doy}/365",
            f"{bar}  {int(year_frac * 100)}%",
            "",
            f"> {weekend_note}",
            "",
            "---",
        ]
    )


# ---------- friend message ----------

def friend_message(username: str, body: str) -> str:
    """Format an incoming message from an approved friend for printing.

    Uses the same markup vocabulary as the rest of the widgets so the
    rich renderer in features/render.py picks up the heading + dividers.
    """
    body = (body or "").strip()
    timestamp = dt.datetime.now().strftime("%a %b %-d \u00b7 %I:%M %p").lower()
    lines = [
        "## from " + (username or "anon"),
        "===",
        "",
    ]
    for paragraph in body.split("\n"):
        if paragraph.strip():
            lines.append(textwrap.fill(paragraph, width=config.RECEIPT_WIDTH))
        else:
            lines.append("")
    lines.extend(["", "---", f"> {timestamp}", "---"])
    return "\n".join(lines)
