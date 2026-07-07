# Plan 005: Dependency & repo hygiene (bounded pins, pip-audit in CI, drop stale artifacts)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 2b723bc..HEAD -- requirements.txt .github/workflows/ci.yml test_print.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: dx / dependencies
- **Planned at**: commit `2b723bc`, 2026-07-06

## Why this matters

Deployment is `pip install -r requirements.txt` on a Raspberry Pi, and
every pin is an open lower bound (`Flask>=3.0.0`, `python-escpos>=3.1`,
…). Whenever the Pi reinstalls, it gets whatever is newest that day — a
breaking major of `python-escpos` or `qrcode` would land silently and
surface as a broken printer at runtime, the most annoying possible place
to debug. Three smaller items ride along: CI has no dependency-CVE
visibility (relevant because Pillow parses uploads and part of the app is
on the public internet), the repo root carries a stale first-ever-print
script (`test_print.py`) with hardcoded USB IDs that bypasses `config.py`,
and nothing records the intended Python version (CI pins 3.12, the local
venv is 3.14).

Each step below is independent and mechanical; together they make "reinstall
on the Pi" boring.

## Current state

- `requirements.txt` as of `2b723bc` (comments elided here — **keep them
  all**, including the arabic-reshaper block and the dev-only note):

```
Flask>=3.0.0
python-escpos>=3.1
Pillow>=10.0.0
requests>=2.31.0
pyusb>=1.2.1
qrcode>=7.4.2
python-barcode>=0.15.1
gunicorn>=21.0
arabic-reshaper>=3.0.0
python-bidi>=0.4.2
```

- `.github/workflows/ci.yml` — single `test` job: checkout, setup-python
  3.12 (pip cache), `pip install -r requirements.txt pytest`, `pytest`,
  then the auth-flow smoke with `DRY_RUN/ADMIN_TOKEN/DATA_DIR` env.
- `test_print.py` (repo root, 13 lines) — a hardcoded
  `Usb(0x0483, 0x5720, ...)` hello-world from the project's first commit.
  Not collected by pytest (`pytest.ini` sets `testpaths = tests`), not
  referenced anywhere (`rg -l "test_print"` → only itself), superseded by
  `smoke.py` + `DRY_RUN`.
- No `.python-version` file exists.
- `README.md` "Tests" section documents `pytest` + the auth smoke; the
  project-layout listing does **not** mention `test_print.py` (nothing to
  update there when it's deleted).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Fresh-install check | `python3 -m venv /tmp/tp-pin-check && /tmp/tp-pin-check/bin/pip install -q -r requirements.txt pytest` | exit 0 |
| Suite under fresh venv | `/tmp/tp-pin-check/bin/python -m pytest -q` | all pass |
| Suite under repo venv | `source .venv/bin/activate && python -m pytest -q` | all pass |
| CI syntax sanity | `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"` | exit 0 (PyYAML is available in the venv via transitive deps; if not, `pip install pyyaml` into /tmp venv) |

## Scope

**In scope** (the only files you should modify):
- `requirements.txt`
- `.github/workflows/ci.yml`
- `test_print.py` (delete)
- `.python-version` (create)

**Out of scope** (do NOT touch):
- `smoke.py` — it is the living replacement for `test_print.py`; leave it.
- `deploy/setup.sh`, `DEPLOY.md` — the install command doesn't change.
- Adding a lockfile / `pip-compile` / `uv` — considered and rejected: the
  dev machine (macOS / Python 3.14) and the Pi (ARM Linux / other Python)
  would need separate locks; upper-bounded ranges give most of the safety
  with none of that ceremony.
- Adding ruff/linting — considered and rejected for now; see
  `plans/README.md` "considered and rejected".

## Git workflow

- Branch: `advisor/005-dependency-hygiene`
- One commit per step or one combined commit — either is fine. Style:
  `Deps: bound majors, pip-audit in CI, drop first-print relic`.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Upper-bound the requirements

Edit `requirements.txt`, changing only the version specifiers (keep every
comment line and the ordering):

```
Flask>=3.0.0,<4
python-escpos>=3.1,<4
Pillow>=10.0.0,<12
requests>=2.31.0,<3
pyusb>=1.2.1,<2
qrcode>=7.4.2,<9
python-barcode>=0.15.1,<1
gunicorn>=21.0,<24
arabic-reshaper>=3.0.0,<4
python-bidi>=0.4.2,<1
```

Add one short comment at the top of the file, in the file's existing
voice, e.g.:

```
# Upper bounds = "known-good majors". The Pi installs straight from this
# file, so a surprise major bump would land as a broken printer at
# runtime. Raise a bound deliberately, run the suite, then deploy.
```

**Verify**: the fresh-venv install + suite commands from the table both
exit 0. If pip reports an unresolvable conflict, see STOP conditions.

### Step 2: Delete `test_print.py`

`git rm test_print.py`

**Verify**: `rg -l "test_print" .` → no matches;
`python -m pytest -q` → unchanged pass count (it was never collected).

### Step 3: Pin the Python version marker

Create `.python-version` containing exactly:

```
3.12
```

(This matches CI and the README's stated stack; it's advisory for
pyenv/uv users and harmless otherwise.)

**Verify**: `cat .python-version` → `3.12`.

### Step 4: Add pip-audit to CI

In `.github/workflows/ci.yml`, after the "Install" step and before
"Pytest", add:

```yaml
      - name: Audit dependencies
        # Advisory-only: surfaces known CVEs in the log without blocking
        # the suite. Promote to blocking if it ever proves quiet enough.
        continue-on-error: true
        run: |
          pip install pip-audit
          pip-audit
```

Keep indentation consistent with the existing steps (6 spaces before `-`).

**Verify**: the YAML-parse sanity command from the table exits 0.

## Test plan

No production code changes; the gates are the fresh-venv install + full
suite (Step 1) and the unchanged pass count (Step 2). CI behavior change
(Step 4) can only be fully verified on the next push — note that in your
report.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `grep -c "<" requirements.txt` ≥ 10 (every dep upper-bounded)
- [ ] Fresh-venv install + `pytest -q` pass (commands above)
- [ ] `test -f test_print.py` → exits 1 (deleted)
- [ ] `cat .python-version` → `3.12`
- [ ] `rg -n "pip-audit" .github/workflows/ci.yml` → 2 matches (install + run)
- [ ] `git status --porcelain` shows only the four in-scope paths
      (and `plans/README.md`)
- [ ] `plans/README.md` status row updated

## STOP conditions

Stop and report back (do not improvise) if:

- The fresh-venv install fails to resolve with the new bounds — report
  the conflicting package and pip's message; do not loosen bounds to make
  it pass.
- The suite fails under the freshly resolved versions but passes under
  `.venv` — that's a live incompatibility worth its own report (name the
  package and version).
- `test_print.py` has gained references since `2b723bc`
  (`rg -l "test_print" .` returns more than itself).

## Maintenance notes

- When a bound needs raising (e.g. Pillow 12): raise it locally, run the
  suite, print something real via `DRY_RUN` diff if the dep touches
  rendering, then deploy — one dep at a time.
- `pip-audit` is advisory (`continue-on-error`); check its section in the
  CI log occasionally. If it stays quiet for months, consider making it
  blocking.
- If the Pi's OS Python ever diverges from 3.12, update `.python-version`,
  CI, and the README stack line together.
