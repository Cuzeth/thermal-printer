"""Smoke test for the username/password auth plumbing.

Exercises:
- DB CRUD via the auth.db module
- Admin Bearer-token guard
- Register happy path + duplicate-username 409 + validation 400s
- Login wrong password / unknown user / too-many-failures
- /api/m/me when signed-out + signed-in
- Approve + revoke + delete

Run from repo root (doesn't touch USB):
    DRY_RUN=true ADMIN_TOKEN=test-token DATA_DIR=/tmp/tp-test \\
        python3 scripts/test_auth_flow.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

# Force a clean test DB before importing the app.
_TMP = tempfile.mkdtemp(prefix="tp-auth-test-")
os.environ["DATA_DIR"] = _TMP
os.environ.setdefault("ADMIN_TOKEN", "test-token-please-replace")
os.environ.setdefault("DRY_RUN", "true")

# Ensure project root is importable when this script is invoked directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import app as app_module  # noqa: E402
from auth import blueprint as auth_bp_mod  # noqa: E402
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
    u = auth_db.create_pending_user("alice", "hunter2hunter")
    _ok("create_pending_user", u["status"] == "pending" and u["id"] > 0)
    user = auth_db.get_user(u["id"])
    _ok("password stored hashed", user["password_hash"] and not user["password_hash"].startswith("hunter2"))
    _ok("verify_password good", auth_db.verify_password(user, "hunter2hunter"))
    _ok("verify_password bad",  not auth_db.verify_password(user, "wrong"))

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

    print("\n[3] Register happy path")
    fresh = app_module.app.test_client()
    r = fresh.post("/api/m/auth/register", json={"username": "bob", "password": "correct-horse"})
    _ok("register/bob -> 200", r.status_code == 200)
    _ok("bob is pending + session set", r.get_json()["user"]["status"] == "pending")

    # After register, same client is logged in → /me should return bob
    r = fresh.get("/api/m/me")
    _ok("me reflects session", r.get_json()["user"]["username"] == "bob")

    print("\n[4] Register validation")
    r = client.post("/api/m/auth/register", json={"username": "bob", "password": "another-pass"})
    _ok("dup username -> 409", r.status_code == 409)

    r = client.post("/api/m/auth/register", json={"username": "no spaces!", "password": "whatever-long"})
    _ok("bad chars -> 400", r.status_code == 400)

    r = client.post("/api/m/auth/register", json={"username": "shortpw", "password": "nope"})
    _ok("short password -> 400", r.status_code == 400)

    print("\n[5] Login")
    # reset rate limiter between runs
    auth_bp_mod._failures.clear()

    r = client.post("/api/m/auth/login", json={"username": "alice", "password": "hunter2hunter"})
    _ok("alice login -> 200", r.status_code == 200)
    _ok("alice now signed in", r.get_json()["user"]["status"] == "allowed")

    r = client.post("/api/m/auth/login", json={"username": "alice", "password": "wrongpass"})
    _ok("wrong pw -> 401", r.status_code == 401)

    r = client.post("/api/m/auth/login", json={"username": "ghost", "password": "whatever-long"})
    _ok("unknown user -> 401", r.status_code == 401)

    print("\n[6] Login rate limit")
    auth_bp_mod._failures.clear()
    for _ in range(auth_bp_mod._MAX_FAILURES):
        client.post("/api/m/auth/login", json={"username": "alice", "password": "bad"})
    r = client.post("/api/m/auth/login", json={"username": "alice", "password": "bad"})
    _ok("11th attempt -> 429", r.status_code == 429)

    # A successful auth shouldn't be reachable during lockout
    r = client.post("/api/m/auth/login", json={"username": "alice", "password": "hunter2hunter"})
    _ok("correct pw during lockout -> 429", r.status_code == 429)

    auth_bp_mod._failures.clear()
    r = client.post("/api/m/auth/login", json={"username": "alice", "password": "hunter2hunter"})
    _ok("after clear, correct pw -> 200", r.status_code == 200)

    print("\n[7] Logout")
    r = client.post("/api/m/auth/logout")
    _ok("logout -> 200", r.status_code == 200)
    r = client.get("/api/m/me")
    _ok("me=null after logout", r.get_json()["user"] is None)

    print("\n[8] Approve/revoke/delete")
    bob = auth_db.get_user_by_username("bob")
    r = client.post(f"/api/admin/users/{bob['id']}/approve", headers=bearer)
    _ok("approve bob -> 200", r.status_code == 200)
    _ok("bob is allowed", auth_db.get_user_by_username("bob")["status"] == "allowed")

    r = client.post(f"/api/admin/users/{bob['id']}/revoke", headers=bearer)
    _ok("revoke bob -> 200", r.status_code == 200)
    _ok("bob is blocked", auth_db.get_user_by_username("bob")["status"] == "blocked")

    r = client.post(f"/api/admin/users/{bob['id']}/delete", headers=bearer)
    _ok("delete bob -> 200", r.status_code == 200)
    _ok("bob is gone", auth_db.get_user_by_username("bob") is None)

    print("\n[9] Cleanup")
    shutil.rmtree(_TMP, ignore_errors=True)
    print(f"  removed {_TMP}")
    print("\nALL GREEN")


if __name__ == "__main__":
    main()
