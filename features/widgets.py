"""Fun widget content generators.

Each function either fetches live content or generates something locally,
then returns a string body the printer layer can render (via text.render).
Widgets avoid hard dependencies on external APIs — if a call fails, they
fall back to offline content so the user always gets a print.
"""

from __future__ import annotations

import datetime as dt
import random
import textwrap
from urllib.parse import quote

import requests

import config


# ---------- offline banks ----------

QUOTES = [
    ("The only way to do great work is to love what you do.", "Steve Jobs"),
    ("In the middle of every difficulty lies opportunity.", "Albert Einstein"),
    ("Simplicity is the ultimate sophistication.", "Leonardo da Vinci"),
    ("Make it work, make it right, make it fast.", "Kent Beck"),
    ("The best way out is always through.", "Robert Frost"),
    ("You miss 100% of the shots you don't take.", "Wayne Gretzky"),
    ("Perfection is achieved when there is nothing left to take away.",
     "Antoine de Saint-Exupery"),
    ("What we think, we become.", "Buddha"),
    ("Stay hungry, stay foolish.", "Stewart Brand"),
    ("First, solve the problem. Then, write the code.", "John Johnson"),
]

JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I told my computer I needed a break. It said 'No problem, I'll go to sleep.'",
    "Why do Java developers wear glasses? Because they don't C#.",
    "There are 10 kinds of people: those who know binary and those who don't.",
    "A SQL query walks into a bar, walks up to two tables and asks: 'Can I join you?'",
    "Why did the developer go broke? Because he used up all his cache.",
    "How many programmers does it take to change a light bulb? None. It's a hardware problem.",
    "Debugging: being the detective in a crime movie where you are also the murderer.",
    "I would tell you a UDP joke but you might not get it.",
    "Real programmers count from 0.",
]

HAIKUS = [
    "morning fog lifting\ncoffee steam curls to the light\nthe keyboard clatters",
    "small gear turns inside\nthermal paper hums its song\nwarm words on the roll",
    "autumn leaf falling\nprinter whirs and hums along\nink-less magic scroll",
    "midnight terminal\ncursor blinks an empty prayer\nsomewhere a bug sleeps",
    "on thermal paper\nhaiku fades in summer heat\nimpermanent art",
]

EIGHT_BALL = [
    "It is certain.",
    "It is decidedly so.",
    "Without a doubt.",
    "Yes, definitely.",
    "You may rely on it.",
    "As I see it, yes.",
    "Most likely.",
    "Outlook good.",
    "Yes.",
    "Signs point to yes.",
    "Reply hazy, try again.",
    "Ask again later.",
    "Better not tell you now.",
    "Cannot predict now.",
    "Concentrate and ask again.",
    "Don't count on it.",
    "My reply is no.",
    "My sources say no.",
    "Outlook not so good.",
    "Very doubtful.",
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


def roll_dice(count: int = 2, sides: int = 6) -> str:
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


# ---------- quotes / jokes ----------

def random_quote() -> str:
    quote, author = random.choice(QUOTES)
    wrapped = textwrap.fill(f'"{quote}"', width=config.RECEIPT_WIDTH)
    return "\n".join(
        [
            "# QUOTE",
            "===",
            "",
            wrapped,
            "",
            f"> - {author}",
            "",
            "---",
        ]
    )


def dad_joke() -> str:
    """Try icanhazdadjoke.com first, fall back to offline bank."""
    text = None
    try:
        r = requests.get(
            "https://icanhazdadjoke.com/",
            headers={"Accept": "text/plain", "User-Agent": "thermal-printer-gui"},
            timeout=3,
        )
        if r.ok and r.text.strip():
            text = r.text.strip()
    except Exception:
        pass
    if not text:
        text = random.choice(JOKES)

    wrapped = textwrap.fill(text, width=config.RECEIPT_WIDTH)
    return "\n".join(
        [
            "# DAD JOKE",
            "===",
            "",
            wrapped,
            "",
            "> (groan)",
            "---",
        ]
    )


def haiku() -> str:
    poem = random.choice(HAIKUS)
    parts = ["# HAIKU", "===", ""]
    for line in poem.splitlines():
        parts.append("> " + line)
    parts.extend(["", "---"])
    return "\n".join(parts)


def magic_eight_ball(question: str = "") -> str:
    answer = random.choice(EIGHT_BALL)
    parts = ["# MAGIC 8 BALL", "==="]
    if question.strip():
        parts.extend(["", textwrap.fill("Q: " + question.strip(), width=config.RECEIPT_WIDTH)])
    parts.extend(
        [
            "",
            "## the answer is...",
            "",
            f"# {answer}",
            "",
            "===",
        ]
    )
    return "\n".join(parts)


# ---------- weather ----------

def weather(location: str) -> str:
    """Free, no-auth weather via wttr.in. Falls back to a friendly error body."""
    location = location.strip() or "Cupertino"
    try:
        r = requests.get(
            f"https://wttr.in/{quote(location, safe='')}",
            params={"format": "j1"},
            timeout=5,
            headers={"User-Agent": "thermal-printer-gui"},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return "\n".join(
            [
                "# WEATHER",
                "===",
                "",
                f"could not fetch weather for:",
                f"  {location}",
                "",
                textwrap.fill(str(e), width=config.RECEIPT_WIDTH),
                "",
                "---",
            ]
        )

    current = data.get("current_condition", [{}])[0]
    area = data.get("nearest_area", [{}])[0]
    today = data.get("weather", [{}])[0]

    city = (area.get("areaName") or [{}])[0].get("value", location)
    region = (area.get("region") or [{}])[0].get("value", "")
    desc = (current.get("weatherDesc") or [{}])[0].get("value", "")
    temp_c = current.get("temp_C", "?")
    feels_c = current.get("FeelsLikeC", "?")
    humidity = current.get("humidity", "?")
    wind_kph = current.get("windspeedKmph", "?")
    wind_dir = current.get("winddir16Point", "")
    high_c = today.get("maxtempC", "?")
    low_c = today.get("mintempC", "?")
    sunrise = (today.get("astronomy") or [{}])[0].get("sunrise", "")
    sunset = (today.get("astronomy") or [{}])[0].get("sunset", "")

    parts = [
        "# WEATHER",
        "===",
        f"> {city}" + (f", {region}" if region else ""),
        f"> {dt.date.today().strftime('%A, %b %-d')}",
        "",
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
    ]
    return "\n".join(parts)


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
}


def ascii_art(name: str) -> str:
    art = ASCII_ART.get(name.lower())
    if not art:
        art = random.choice(list(ASCII_ART.values()))
    return "## " + name.upper() + "\n---\n" + art + "\n---"


# ---------- agenda-style "now" card ----------

def now_card() -> str:
    now = dt.datetime.now()
    return "\n".join(
        [
            "# " + now.strftime("%a %b %-d"),
            "===",
            f"> {now.strftime('%I:%M %p')}".lower(),
            "",
            f"week #{now.isocalendar().week} \u00b7 day {now.timetuple().tm_yday}/365",
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
