"""Versioned, deterministic paper puzzles and their public solution receipts.

Only a signed, fixed-size seed reaches the generators. Version 1 is a format
contract: keep its generators and random stream unchanged when adding versions,
so a receipt in a drawer still has the same solution after a deployment.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from urllib.parse import urlsplit

import qrcode
from itsdangerous import BadData, URLSafeSerializer
from PIL import Image, ImageDraw

from features import render as render_feat


KINDS = {"sudoku": "Sudoku", "maze": "Maze", "wordsearch": "Word search"}
KIND_CODES = {"s": "sudoku", "m": "maze", "w": "wordsearch"}
MAX_TOKEN_LENGTH = 180
WIDTHS = (384, 576)
SALT = "paper-arcade-v1"

# This 30-clue puzzle has exactly one solution (independently checked in tests).
# Permuting digits, bands/stacks and rows/columns within them, then transposing,
# preserves every Sudoku constraint and the number of solutions. Generation is
# thus bounded; an anonymous solution scan never runs an exponential solver.
_SUDOKU = (
    "530070000", "600195000", "098000060", "800060003", "400803001",
    "700020006", "060000280", "000419005", "000080079",
)
_SUDOKU_SOLUTION = (
    "534678912", "672195348", "198342567", "859761423", "426853791",
    "713924856", "961537284", "287419635", "345286179",
)
_WORDS = ("FOREST", "RIVER", "CLOUD", "STONE", "TRAIL", "BLOOM", "MOSS", "FERN",
          "LEAF", "RAIN", "PINE", "BIRD", "MOON", "SUN", "SEED", "LAKE")


class _Random:
    """Small explicit stream; shuffle behavior cannot drift with Python versions."""

    def __init__(self, seed: str):
        self.seed = bytes.fromhex(seed)
        self.counter = 0

    def below(self, upper: int) -> int:
        block = hashlib.sha256(self.seed + self.counter.to_bytes(4, "big")).digest()
        self.counter += 1
        return int.from_bytes(block[:8], "big") % upper

    def shuffled(self, values):
        result = list(values)
        for i in range(len(result) - 1, 0, -1):
            j = self.below(i + 1)
            result[i], result[j] = result[j], result[i]
        return result


def public_base_url(value: str, *, local: bool, port: int) -> str:
    """Never trust request Host/forwarded headers when putting a URL on paper."""
    value = value.strip().rstrip("/") if value else (
        f"http://127.0.0.1:{port}" if local else "https://print.cuzeth.com"
    )
    parsed = urlsplit(value)
    if (len(value) > 120 or parsed.scheme not in ("http", "https")
            or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path
            or any(ord(c) <= 32 or ord(c) >= 127 for c in value)):
        raise ValueError("PUBLIC_BASE_URL must be an http(s) origin, at most 120 characters")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("PUBLIC_BASE_URL has an invalid port") from exc
    return value


def _base_tag(base: str) -> str:
    return hashlib.sha256(base.encode()).hexdigest()[:8]


def new_token(kind: str, width: int, base: str, secret: str) -> str:
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("choose Sudoku, maze or word search")
    if width not in WIDTHS:
        raise ValueError("Paper Arcade supports 384 or 576 pixel printers")
    code = next(code for code, name in KIND_CODES.items() if name == kind)
    return URLSafeSerializer(secret, salt=SALT).dumps(
        [1, code, secrets.token_hex(8), width, _base_tag(base)]
    )


def read_token(token: str, secret: str) -> dict:
    if (not isinstance(token, str) or not 1 <= len(token) <= MAX_TOKEN_LENGTH
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", token)):
        raise ValueError("invalid puzzle link")
    try:
        data = URLSafeSerializer(secret, salt=SALT).loads(token)
    except BadData as exc:
        raise ValueError("invalid puzzle link") from exc
    # Even correctly signed data is schema-checked before allocation or loops.
    if (not isinstance(data, list) or len(data) != 5
            or type(data[0]) is not int or data[0] != 1
            or not isinstance(data[1], str) or data[1] not in KIND_CODES
            or not isinstance(data[2], str) or not re.fullmatch(r"[0-9a-f]{16}", data[2])
            or type(data[3]) is not int or data[3] not in WIDTHS
            or not isinstance(data[4], str) or not re.fullmatch(r"[0-9a-f]{8}", data[4])):
        raise ValueError("invalid puzzle link")
    return {"version": 1, "kind": KIND_CODES[data[1]], "seed": data[2],
            "width": data[3], "base_tag": data[4]}


def require_current_settings(spec: dict, width: int, base: str) -> None:
    if spec["width"] != width or spec["base_tag"] != _base_tag(base):
        raise ValueError("printer width or solution address changed; make a new puzzle")


def solution_url(base: str, token: str) -> str:
    return base + "/arcade/solution/" + token


def _sudoku(rng: _Random) -> dict:
    digits = [0] + rng.shuffled(range(1, 10))
    rows = [b * 3 + r for b in rng.shuffled(range(3)) for r in rng.shuffled(range(3))]
    cols = [b * 3 + c for b in rng.shuffled(range(3)) for c in rng.shuffled(range(3))]
    transpose = rng.below(2)

    def transform(source):
        grid = [[digits[int(source[r][c])] for c in cols] for r in rows]
        return [list(row) for row in zip(*grid)] if transpose else grid

    return {"grid": transform(_SUDOKU), "solution": transform(_SUDOKU_SOLUTION)}


def _maze(rng: _Random) -> dict:
    size = 13
    # Each cell holds N/E/S/W wall bits. A spanning tree gives one path between
    # any pair of cells and bounds carving to exactly size*size-1 passages.
    walls = [[15] * size for _ in range(size)]
    seen = {(0, 0)}
    stack = [(0, 0)]
    parent = {}
    steps = ((-1, 0, 1, 4), (0, 1, 2, 8), (1, 0, 4, 1), (0, -1, 8, 2))
    while stack:
        r, c = stack[-1]
        options = [(r + dr, c + dc, bit, opposite) for dr, dc, bit, opposite in steps
                   if 0 <= r + dr < size and 0 <= c + dc < size
                   and (r + dr, c + dc) not in seen]
        if not options:
            stack.pop()
            continue
        nr, nc, bit, opposite = options[rng.below(len(options))]
        walls[r][c] &= ~bit
        walls[nr][nc] &= ~opposite
        seen.add((nr, nc))
        parent[nr, nc] = (r, c)
        stack.append((nr, nc))
    path = [(size - 1, size - 1)]
    while path[-1] != (0, 0):
        path.append(parent[path[-1]])
    walls[0][0] &= ~1
    walls[-1][-1] &= ~4
    return {"grid": walls, "path": list(reversed(path))}


def _wordsearch(rng: _Random) -> dict:
    size = 12
    words = rng.shuffled(_WORDS)[:8]
    grid = [[""] * size for _ in range(size)]
    placements = []
    directions = ((0, 1), (1, 0), (1, 1), (-1, 1), (0, -1), (-1, 0), (-1, -1), (1, -1))
    for word in words:
        candidates = [(r, c, dr, dc) for r in range(size) for c in range(size)
                      for dr, dc in directions
                      if 0 <= r + dr * (len(word) - 1) < size
                      and 0 <= c + dc * (len(word) - 1) < size]
        for r, c, dr, dc in rng.shuffled(candidates):
            cells = [(r + i * dr, c + i * dc) for i in range(len(word))]
            if all(grid[y][x] in ("", letter) for (y, x), letter in zip(cells, word)):
                for (y, x), letter in zip(cells, word):
                    grid[y][x] = letter
                placements.append({"word": word, "cells": cells})
                break
        else:
            # Bounded fallback: every selected word fits its own distinct row.
            # No retry loop or backtracking is exposed through public scans.
            grid = [[""] * size for _ in range(size)]
            placements = []
            for row, item in zip(rng.shuffled(range(size)), words):
                col = rng.below(size - len(item) + 1)
                cells = [(row, col + i) for i in range(len(item))]
                for (y, x), letter in zip(cells, item):
                    grid[y][x] = letter
                placements.append({"word": item, "cells": cells})
            break
    for row in grid:
        for c in range(size):
            if not row[c]:
                row[c] = chr(65 + rng.below(26))
    return {"grid": grid, "words": sorted(words), "placements": placements}


def generate(spec: dict) -> dict:
    # read_token is the only boundary into this function from HTTP requests.
    puzzle = {"sudoku": _sudoku, "maze": _maze, "wordsearch": _wordsearch}[spec["kind"]](
        _Random(spec["seed"])
    )
    return {**spec, **puzzle, "title": KINDS[spec["kind"]], "number": spec["seed"][:8].upper()}


def make_qr(url: str, width: int) -> Image.Image:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=1)
    qr.add_data(url)
    qr.make(fit=True)
    # A 4-dot module is ~0.5mm at 203dpi. Never interpolate module edges.
    modules = len(qr.get_matrix())
    box = min(4, (width - 32) // modules)
    if box < 3:
        raise ValueError("solution address is too long for a readable QR code")
    qr.box_size = box
    return qr.make_image(fill_color="black", back_color="white").convert("1")


def _text(draw, x: int, y: int, text: str, size: int, *, bold=False, center=False):
    font = render_feat._font("mono_bold" if bold else "mono_regular", size)
    if center:
        x -= round(draw.textlength(text, font=font) / 2)
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((x, y - box[1]), text, font=font, fill=0)


def render(puzzle: dict, url: str | None = None, *, solution: bool = False) -> Image.Image:
    """Draw crisp cells and large writing spaces without changing rich-text fonts."""
    width = puzzle["width"]
    grid = puzzle["grid"]
    n = len(grid)
    cell = (width - 40) // n
    side = cell * n
    left = (width - side) // 2
    top = 128
    qr = make_qr(url, width) if url and not solution else None
    extra = 126 if puzzle["kind"] == "wordsearch" else 34
    height = top + side + extra + (qr.height + 72 if qr else 28)
    image = Image.new("1", (width, height), 255)
    draw = ImageDraw.Draw(image)
    _text(draw, width // 2, 16, "PAPER ARCADE", 22, bold=True, center=True)
    title = puzzle["title"].upper() + (" / SOLUTION" if solution else "")
    _text(draw, width // 2, 49, title, 18, bold=True, center=True)
    _text(draw, width // 2, 77, "NO. " + puzzle["number"], 16, center=True)
    instruction = {"sudoku": "1-9 in every row, column & box.",
                   "maze": "Start at the top. Exit below.",
                   "wordsearch": "Find 8 words in all directions."}[puzzle["kind"]]
    _text(draw, width // 2, 104, instruction, 16, center=True)

    if puzzle["kind"] == "maze":
        if solution:
            points = [(left + c * cell + cell // 2, top + r * cell + cell // 2)
                      for r, c in puzzle["path"]]
            points = [(points[0][0], top)] + points + [(points[-1][0], top + side)]
            # Small dots keep the route distinguishable from solid maze walls.
            for start, end in zip(points, points[1:]):
                length = abs(end[0] - start[0]) + abs(end[1] - start[1])
                for step in range(0, length + 1, 6):
                    x = start[0] + (end[0] - start[0]) * step // max(1, length)
                    y = start[1] + (end[1] - start[1]) * step // max(1, length)
                    draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=0)
        for r, row in enumerate(grid):
            for c, walls in enumerate(row):
                x, y = left + c * cell, top + r * cell
                for bit, line in ((1, (x, y, x + cell, y)), (2, (x + cell, y, x + cell, y + cell)),
                                  (4, (x, y + cell, x + cell, y + cell)), (8, (x, y, x, y + cell))):
                    if walls & bit:
                        draw.line(line, fill=0, width=2)
    else:
        display = puzzle["solution"] if solution and puzzle["kind"] == "sudoku" else grid
        marked = {cell for place in puzzle.get("placements", []) for cell in place["cells"]}
        for r, row in enumerate(display):
            for c, value in enumerate(row):
                if not value:
                    continue
                x, y = left + c * cell + cell // 2, top + r * cell
                size = cell * 3 // 5
                _text(draw, x, y + (cell - size) // 2, str(value), size,
                      bold=puzzle["kind"] == "sudoku" and bool(grid[r][c]), center=True)
                if solution and puzzle["kind"] == "wordsearch" and (r, c) in marked:
                    draw.line((x - cell // 3, y + cell - 4, x + cell // 3, y + cell - 4), fill=0, width=2)
        if puzzle["kind"] == "sudoku":
            for i in range(n + 1):
                stroke = 3 if i % 3 == 0 else 1
                draw.line((left, top + i * cell, left + side, top + i * cell), fill=0, width=stroke)
                draw.line((left + i * cell, top, left + i * cell, top + side), fill=0, width=stroke)

    y = top + side + 24
    if puzzle["kind"] == "wordsearch":
        for i, word in enumerate(puzzle["words"]):
            _text(draw, left + (i % 2) * (side // 2), y + (i // 2) * 25, word, 18)
        y += 104
    if qr:
        _text(draw, width // 2, y + 5, "SCAN FOR THE SOLUTION", 16, center=True)
        image.paste(qr, ((width - qr.width) // 2, y + 32))
    return image
