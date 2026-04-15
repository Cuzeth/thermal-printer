"""Smoke test for Phase 2 auth plumbing.

Exercises the parts that don't need a real authenticator:
- DB CRUD via the auth.db module
- Admin Bearer-token guard
- /api/m/auth/register/begin happy path + duplicate-username 409
- /api/m/auth/login/begin missing-user 404
- /api/m/me when signed-out

The cryptographic finish flows are validated end-to-end in Phase 3 via
Playwright's virtual-authenticator (Chrome supports software passkeys
for testing). Running this script doesn't talk to USB.

Run from repo root:
    DRY_RUN=true ADMIN_TOKEN=test-token DATA_DIR=/tmp/tp-test \\
        python3 scripts/test_auth_flow.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

# Force a clean test DB before importing the app.
_TMP = tempfile.mkdtemp(prefix="tp-auth-test-")
os.environ["DATA_DIR"] = _TMP
os.environ.setdefault("ADMIN_TOKEN", "test-token-please-replace")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("RP_ID", "localhost")
os.environ.setdefault("ORIGIN", "http://localhost:5005")

# Ensure project root is importable when this script is invoked directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import app as app_module  # noqa: E402
from auth import db as auth_db  # noqa: E402


def _ok(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        raise SystemExit(1)


def main() -> None:
    print(f"using DATA_DIR={config.DATA_DIR}")
    print(f"using ADMIN_TOKEN={config.ADMIN_TOKEN}")

    client = app_module.app.test_client()
    bearer = {"Authorization": f"Bearer {config.ADMIN_TOKEN}"}

    print("\n[1] DB layer")
    u = auth_db.create_pending_user("alice")
    _ok("create_pending_user", u["status"] == "pending" and len(u["user_handle"]) == 16)

    auth_db.add_credential(u["id"], b"\x01" * 32, b"\x02" * 32, 0, ["internal"])
    creds = auth_db.get_credentials_for_user(u["id"])
    _ok("get_credentials_for_user", len(creds) == 1 and creds[0]["sign_count"] == 0)

    auth_db.update_sign_count(b"\x01" * 32, 5)
    creds = auth_db.get_credentials_for_user(u["id"])
    _ok("update_sign_count", creds[0]["sign_count"] == 5)

    auth_db.set_status(u["id"], "allowed")
    user = auth_db.get_user(u["id"])
    _ok("set_status approved", user["status"] == "allowed" and user["approved_at"] is not None)

    msg_id = auth_db.log_message(u["id"], "hi from alice")
    msgs = auth_db.list_messages(limit=5)
    _ok("log_message + list_messages", any(m["id"] == msg_id and m["username"] == "alice" for m in msgs))

    print("\n[2] Admin endpoint guard")
    r = client.get("/api/admin/users")
    _ok("no token -> 401", r.status_code == 401)

    r = client.get("/api/admin/users", headers={"Authorization": "Bearer wrong"})
    _ok("bad token -> 401", r.status_code == 401)

    r = client.get("/api/admin/users", headers=bearer)
    _ok("valid token -> 200", r.status_code == 200)
    _ok("alice in users", any(u["username"] == "alice" for u in r.get_json()["users"]))

    print("\n[3] Register-begin (no real authenticator)")
    r = client.post("/api/m/auth/register/begin", json={"username": "bob"})
    _ok("register/begin returns options", r.status_code == 200)
    body = r.get_json()
    _ok("options has challenge", "challenge" in body["options"])
    _ok("options has user.id", "id" in body["options"]["user"])

    print("\n[4] Duplicate username")
    r = client.post("/api/m/auth/register/begin", json={"username": "bob"})
    _ok("dup username -> 409", r.status_code == 409)

    print("\n[5] Username validation")
    r = client.post("/api/m/auth/register/begin", json={"username": "no spaces!"})
    _ok("bad chars -> 400", r.status_code == 400)
    r = client.post("/api/m/auth/register/begin", json={"username": "x" * 30})
    _ok("too long -> 400", r.status_code == 400)

    print("\n[6] Login-begin")
    r = client.post("/api/m/auth/login/begin", json={"username": "ghost"})
    _ok("missing user -> 404", r.status_code == 404)

    r = client.post("/api/m/auth/login/begin", json={"username": "alice"})
    _ok("no passkey... wait, alice DOES have one", r.status_code == 200)

    print("\n[7] /api/m/me when signed-out")
    fresh = app_module.app.test_client()
    r = fresh.get("/api/m/me")
    _ok("me returns user=null", r.status_code == 200 and r.get_json()["user"] is None)

    print("\n[8] Approve/revoke endpoints")
    bob = auth_db.get_user_by_username("bob")
    r = client.post(f"/api/admin/users/{bob['id']}/approve", headers=bearer)
    _ok("approve bob -> 200", r.status_code == 200)
    bob = auth_db.get_user_by_username("bob")
    _ok("bob is allowed", bob["status"] == "allowed")

    r = client.post(f"/api/admin/users/{bob['id']}/revoke", headers=bearer)
    _ok("revoke bob -> 200", r.status_code == 200)
    bob = auth_db.get_user_by_username("bob")
    _ok("bob is blocked", bob["status"] == "blocked")

    r = client.post(f"/api/admin/users/{bob['id']}/delete", headers=bearer)
    _ok("delete bob -> 200", r.status_code == 200)
    _ok("bob is gone", auth_db.get_user_by_username("bob") is None)

    print("\n[9] Cleanup")
    shutil.rmtree(_TMP, ignore_errors=True)
    print(f"  removed {_TMP}")
    print("\nALL GREEN")


if __name__ == "__main__":
    main()
