"""Paper Arcade contracts: valid puzzles, stable identities and exact print pixels."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from collections import deque
from contextlib import contextmanager

import pytest
from itsdangerous import URLSafeSerializer
from PIL import Image

import app as app_module
import config
from features import arcade


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {config.ADMIN_TOKEN}"}


def puzzle(kind, seed=0, width=576):
    return arcade.generate({"version": 1, "kind": kind, "seed": f"{seed:016x}", "width": width})


def _solution_count(grid):
    """Independent test-only search; do not rely on the generator's solution."""
    grid = [row[:] for row in grid]

    def visit():
        choice = None
        choices = None
        for r in range(9):
            for c in range(9):
                if grid[r][c]:
                    continue
                used = set(grid[r]) | {row[c] for row in grid}
                used |= {grid[y][x] for y in range(r // 3 * 3, r // 3 * 3 + 3)
                         for x in range(c // 3 * 3, c // 3 * 3 + 3)}
                available = set(range(1, 10)) - used
                if not available:
                    return 0
                if choices is None or len(available) < len(choices):
                    choice, choices = (r, c), available
        if choice is None:
            return 1
        r, c = choice
        total = 0
        for value in choices:
            grid[r][c] = value
            total += visit()
            if total >= 2:
                break
        grid[r][c] = 0
        return total

    return visit()


@pytest.mark.parametrize("seed", range(10))
def test_sudoku_has_exactly_one_valid_solution(seed):
    """Digit/axis permutations preserve the template's unique Sudoku solution."""
    item = puzzle("sudoku", seed)
    grid, solved = item["grid"], item["solution"]
    assert _solution_count(grid) == 1
    assert sum(bool(v) for row in grid for v in row) == 30
    assert all(set(row) == set(range(1, 10)) for row in solved)
    assert all(set(col) == set(range(1, 10)) for col in zip(*solved))
    for r in (0, 3, 6):
        for c in (0, 3, 6):
            assert {solved[y][x] for y in range(r, r + 3) for x in range(c, c + 3)} == set(range(1, 10))
    assert all(not grid[r][c] or grid[r][c] == solved[r][c] for r in range(9) for c in range(9))


@pytest.mark.parametrize("seed", range(10))
def test_maze_is_one_connected_tree_with_a_valid_solution(seed):
    """Every cell is reachable and every solution step passes reciprocal walls."""
    item = puzzle("maze", seed)
    grid, path = item["grid"], item["path"]
    n = len(grid)
    seen, queue = {(0, 0)}, deque([(0, 0)])
    steps = ((-1, 0, 1, 4), (0, 1, 2, 8), (1, 0, 4, 1), (0, -1, 8, 2))
    passages = 0
    while queue:
        r, c = queue.popleft()
        for dr, dc, bit, reverse in steps:
            nr, nc = r + dr, c + dc
            if 0 <= nr < n and 0 <= nc < n:
                assert bool(grid[r][c] & bit) == bool(grid[nr][nc] & reverse)
                if not grid[r][c] & bit:
                    passages += 1
                    if (nr, nc) not in seen:
                        seen.add((nr, nc))
                        queue.append((nr, nc))
            else:
                is_opening = ((r, c, bit) == (0, 0, 1) or (r, c, bit) == (n - 1, n - 1, 4))
                assert bool(grid[r][c] & bit) != is_opening
    assert len(seen) == n * n
    assert passages // 2 == n * n - 1
    assert path[0] == (0, 0) and path[-1] == (n - 1, n - 1)
    assert len(set(path)) == len(path)
    for (r, c), (nr, nc) in zip(path, path[1:]):
        bit = next(bit for dr, dc, bit, _ in steps if (nr - r, nc - c) == (dr, dc))
        assert not grid[r][c] & bit


@pytest.mark.parametrize("seed", range(10))
def test_wordsearch_contains_every_listed_word(seed):
    """Solution coordinates spell all eight words along uninterrupted straight lines."""
    item = puzzle("wordsearch", seed)
    grid = item["grid"]
    assert len(grid) == 12 and all(len(row) == 12 for row in grid)
    assert all("A" <= letter <= "Z" for row in grid for letter in row)
    assert len(set(item["words"])) == 8
    assert set(item["words"]) == {p["word"] for p in item["placements"]}
    for place in item["placements"]:
        cells = place["cells"]
        assert "".join(grid[r][c] for r, c in cells) == place["word"]
        deltas = {(b[0] - a[0], b[1] - a[1]) for a, b in zip(cells, cells[1:])}
        assert len(deltas) == 1
        assert deltas.pop() in {(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dr or dc}


def test_wordsearch_fallback_still_places_all_words(monkeypatch):
    """Exhausted candidate searches fall back once to distinct rows, never retry forever."""
    original = arcade._Random.shuffled

    def no_candidates(self, values):
        values = list(values)
        return [] if values and isinstance(values[0], tuple) else original(self, values)

    monkeypatch.setattr(arcade._Random, "shuffled", no_candidates)
    item = puzzle("wordsearch", 42)
    assert len(item["placements"]) == len(item["words"]) == 8
    assert len({place["cells"][0][0] for place in item["placements"]}) == 8
    for place in item["placements"]:
        assert "".join(item["grid"][r][c] for r, c in place["cells"]) == place["word"]


@pytest.mark.parametrize("kind", arcade.KINDS)
def test_generation_is_bounded_repeatable_and_varied(kind, monkeypatch):
    """The same seed is stable; fixed grid sizes bound the random draws/CPU."""
    calls = 0
    original = arcade._Random.below

    def counted(self, upper):
        nonlocal calls
        calls += 1
        assert calls <= 12000
        return original(self, upper)

    monkeypatch.setattr(arcade._Random, "below", counted)
    first = puzzle(kind, 123)
    calls = 0
    assert first == puzzle(kind, 123)
    calls = 0
    assert first["grid"] != puzzle(kind, 124)["grid"]


@pytest.mark.parametrize("kind,expected", [
    ("sudoku", "3534352042409c2d8ef3288c4773a905a75f808a176f21ad1c65587c99768976"),
    ("maze", "4f6bdafe688337b9b7355d636f19a0c561ff077458276715851904ab37aa0121"),
    ("wordsearch", "66b9ea43aa8666ae0170feb92de00e2fd26818524b7422854b957a28a15edc5f"),
])
def test_version_one_puzzle_identity_is_frozen(kind, expected):
    """Changing an existing seed's puzzle or solution requires a new format version."""
    item = puzzle(kind, 0x0123456789abcdef)
    encoded = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected


@pytest.mark.parametrize("kind", arcade.KINDS)
@pytest.mark.parametrize("width", (384, 576))
def test_receipts_fit_both_paper_widths_and_solution_differs(kind, width):
    """Both rendered states stay bounded, monochrome, and exactly hardware-wide."""
    item = puzzle(kind, 3, width)
    token = arcade.new_token(kind, width, "https://print.cuzeth.com", "test-key")
    receipt = arcade.render(item, arcade.solution_url("https://print.cuzeth.com", token))
    solved = arcade.render(item, solution=True)
    assert receipt.width == solved.width == width
    assert 128 < solved.height < receipt.height < 1600
    assert receipt.mode == solved.mode == "1"
    assert receipt.getextrema() == solved.getextrema() == (0, 255)
    side = (width - 40) // len(item["grid"]) * len(item["grid"])
    assert receipt.crop((0, 128, width, 128 + side)).tobytes() != solved.crop((0, 128, width, 128 + side)).tobytes()


def test_signed_identifier_is_stable_with_persistent_key_and_versioned():
    """A new signer instance reconstructs the same puzzle without process state."""
    token = arcade.new_token("maze", 384, "https://print.cuzeth.com", "persistent-secret")
    first = arcade.read_token(token, "persistent-secret")
    assert first["version"] == 1
    assert arcade.read_token(token, "persistent-secret") == first
    assert len(token) < 110
    with pytest.raises(ValueError, match="invalid puzzle link"):
        arcade.read_token(token, "rotated-secret")


@pytest.mark.parametrize("payload", [None, {}, [], [1], [True, "s", "0" * 16, 384, "0" * 8],
    [2, "s", "0" * 16, 384, "0" * 8], [1, [], "0" * 16, 384, "0" * 8],
    [1, "x", "0" * 16, 384, "0" * 8], [1, "s", "z" * 16, 384, "0" * 8],
    [1, "s", "0" * 17, 384, "0" * 8], [1, "s", "0" * 16, 99999, "0" * 8],
    [1, "s", "0" * 16, 384, []]])
def test_even_signed_payloads_are_schema_checked(payload):
    """Unknown versions, variable sizes and malformed seeds never reach generation."""
    token = URLSafeSerializer("test-key", salt=arcade.SALT).dumps(payload)
    with pytest.raises(ValueError, match="invalid puzzle link"):
        arcade.read_token(token, "test-key")


@pytest.mark.parametrize("token", [None, {}, 42, "", "a" * 181, "<script>", "unsigned.token"])
def test_malformed_tokens_are_rejected(token):
    with pytest.raises(ValueError, match="invalid puzzle link"):
        arcade.read_token(token, "test-key")


@pytest.mark.parametrize("path", ("new", "preview", "print"))
def test_arcade_mutations_and_previews_require_admin(client, path, monkeypatch):
    """Unauthenticated and friend sessions cannot generate, preview or print games."""
    monkeypatch.setattr(config, "DEV_BYPASS_ADMIN", False)
    assert client.post("/api/admin/arcade/" + path, json={}).status_code == 401
    with client.session_transaction() as session:
        session["user_id"] = 123
    assert client.post("/api/admin/arcade/" + path, json={}).status_code == 401


@pytest.mark.parametrize("kind", arcade.KINDS)
@pytest.mark.parametrize("width", (384, 576))
def test_preview_print_parity_and_public_solution(client, auth, monkeypatch, kind, width):
    """Print uses the preview's identity and exact pixels through the safe helper."""
    monkeypatch.setattr(config, "PRINTER_PIXEL_WIDTH", width)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://print.cuzeth.com")
    response = client.post("/api/admin/arcade/new", json={"kind": kind}, headers=auth)
    assert response.status_code == 200
    preview = response.get_json()
    image = Image.open(io.BytesIO(base64.b64decode(preview["data_url"].split(",")[1]))).convert("1")
    seen = []
    sentinel = object()

    @contextmanager
    def printer():
        yield sentinel

    def send(printer, printed):
        assert printer is sentinel
        assert printed.size == image.size and printed.tobytes() == image.tobytes()
        seen.append("image")

    monkeypatch.setattr(app_module, "open_printer", printer)
    monkeypatch.setattr(app_module, "_print_image", send)
    monkeypatch.setattr(app_module, "footer", lambda p: seen.append("footer"))
    monkeypatch.setattr(arcade, "new_token", lambda *a: pytest.fail("print must not choose a new puzzle"))
    repeated = client.post("/api/admin/arcade/preview", json={"token": preview["token"]}, headers=auth)
    assert repeated.get_json() == preview
    printed = client.post("/api/admin/arcade/print", json={"token": preview["token"]}, headers=auth)
    assert printed.status_code == 200 and printed.get_json()["token"] == preview["token"]
    assert seen == ["image", "footer"]
    solution = client.get("/arcade/solution/" + preview["token"])
    assert solution.status_code == 200
    html = solution.get_data(as_text=True)
    assert preview["number"] in html and "data:image/png;base64," in html
    assert "/api/admin" not in html and "friends" not in html and "<script" not in html
    assert "noindex" in solution.headers["X-Robots-Tag"]
    assert client.post("/arcade/solution/" + preview["token"]).status_code == 405


def test_invalid_public_link_does_no_generation_or_printing(client, monkeypatch):
    monkeypatch.setattr(arcade, "generate", lambda *a: pytest.fail("invalid signature reached generator"))
    monkeypatch.setattr(app_module, "open_printer", lambda: pytest.fail("public route touched printer"))
    assert client.get("/arcade/solution/bogus").status_code == 404
    assert client.get("/arcade/solution/" + "x" * 181).status_code == 404


@pytest.mark.parametrize("path", ("new", "preview", "print"))
@pytest.mark.parametrize("payload", (None, [], "bad", 5, {"kind": []}, {}))
def test_bad_admin_payloads_are_input_errors(client, auth, path, payload):
    response = client.post("/api/admin/arcade/" + path, json=payload, headers=auth)
    assert response.status_code == 400
    assert response.get_json()["ok"] is False and response.get_json()["kind"] == "input"


def test_changed_settings_reject_print_but_keep_public_solution(client, auth, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://print.cuzeth.com")
    token = client.post("/api/admin/arcade/new", json={"kind": "sudoku"}, headers=auth).get_json()["token"]
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://other.example")
    monkeypatch.setattr(app_module, "open_printer", lambda: pytest.fail("stale settings reached printer"))
    result = client.post("/api/admin/arcade/print", json={"token": token}, headers=auth)
    assert result.status_code == 400
    assert "changed" in result.get_json()["error"]
    assert client.get("/arcade/solution/" + token).status_code == 200
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://print.cuzeth.com")
    monkeypatch.setattr(config, "PRINTER_PIXEL_WIDTH", 384)
    assert client.post("/api/admin/arcade/print", json={"token": token}, headers=auth).status_code == 400


def test_public_base_defaults_and_ignores_forged_hosts(client, auth, monkeypatch):
    assert arcade.public_base_url("", local=False, port=5005) == "https://print.cuzeth.com"
    assert arcade.public_base_url("", local=True, port=5005) == "http://127.0.0.1:5005"
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://print.cuzeth.com/")
    result = client.post("/api/admin/arcade/new", json={"kind": "maze"},
                         headers={**auth, "Host": "evil.example", "X-Forwarded-Host": "evil.example"})
    assert result.get_json()["solution_url"].startswith("https://print.cuzeth.com/arcade/solution/")


@pytest.mark.parametrize("value", ["javascript:alert(1)", "https://a.example/path", "https://a.example?x=y",
    "https://a.example/#x", "https://user:pass@a.example", "https://a.example:bad", "https://a.\nexample"])
def test_invalid_public_base_is_rejected(value):
    with pytest.raises(ValueError):
        arcade.public_base_url(value, local=False, port=5005)


def test_qr_encodes_exact_solution_link_with_integer_modules_and_quiet_zone(monkeypatch):
    """The same full URL reaches qrcode; modules and four-module borders stay crisp."""
    captured = []
    original = arcade.qrcode.QRCode.add_data

    def record(self, data, *args, **kwargs):
        captured.append(data)
        return original(self, data, *args, **kwargs)

    monkeypatch.setattr(arcade.qrcode.QRCode, "add_data", record)
    base = "https://print.cuzeth.com"
    token = arcade.new_token("wordsearch", 384, base, "test-key")
    url = arcade.solution_url(base, token)
    image = arcade.make_qr(url, 384)
    assert captured == [url]
    assert image.width <= 352
    # The short token fits 4-dot modules at 58mm, with 16 white dots per edge.
    border = 16
    assert image.crop((0, 0, image.width, border)).getextrema() == (255, 255)
    assert image.crop((0, 0, border, image.height)).getextrema() == (255, 255)
    assert image.crop((0, image.height - border, image.width, image.height)).getextrema() == (255, 255)
    assert image.crop((image.width - border, 0, image.width, image.height)).getextrema() == (255, 255)
    for y in range(0, image.height, 4):
        for x in range(0, image.width, 4):
            low, high = image.crop((x, y, x + 4, y + 4)).getextrema()
            assert low == high


def test_dry_run_print_sends_raster_and_cut(client, auth):
    """An actual safe DRY_RUN request exercises ESC/POS image fragmentation."""
    preview = client.post("/api/admin/arcade/new", json={"kind": "maze"}, headers=auth).get_json()
    assert client.post("/api/admin/arcade/print", json={"token": preview["token"]}, headers=auth).status_code == 200
    raw = open(config.DRY_RUN_PATH, "rb").read()
    assert b"\x1dv0" in raw and b"\x1dV" in raw
