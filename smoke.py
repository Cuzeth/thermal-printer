"""Quick smoke test — exercises routes in DRY_RUN without touching USB."""
import config
config.DRY_RUN = True

from app import app

client = app.test_client()
AUTH = {"Authorization": f"Bearer {config.ADMIN_TOKEN}"}

print("GET /api/ping          ->", client.get("/api/ping").get_json())

r = client.post("/api/admin/preview", headers=AUTH, json={"body": "# HI\n> centered\n- bullet\n[x] done"})
print("POST admin/preview     ->", r.status_code, "preview lines:",
      len(r.get_json().get("preview", "").splitlines()))

r = client.post("/api/admin/print/text", headers=AUTH, json={"body": "# TEST\n> smoke test"})
print("POST admin/print/text   ->", r.status_code, r.get_json())

r = client.post("/api/admin/print/dice", headers=AUTH, json={"count": 3, "sides": 20})
print("POST admin/print/dice   ->", r.status_code, r.get_json())

r = client.post("/api/admin/print/todo", headers=AUTH, json={"title": "X", "items": ["a", "b"]})
print("POST admin/print/todo   ->", r.status_code, r.get_json())

r = client.post("/api/admin/print/receipt", headers=AUTH, json={
    "store": "S",
    "items": [{"name": "apple", "qty": 2, "price": 1.50}],
    "tax_rate": 8.0,
})
print("POST admin/print/receipt->", r.status_code, r.get_json())

r = client.post("/api/admin/print/label", headers=AUTH, json={"text": "FRAGILE"})
print("POST admin/print/label  ->", r.status_code, r.get_json())

r = client.post("/api/admin/print/ascii", headers=AUTH, json={"name": "cat"})
print("POST admin/print/ascii  ->", r.status_code, r.get_json())

r = client.post("/api/admin/print/now", headers=AUTH, json={})
print("POST admin/print/now    ->", r.status_code, r.get_json())

# auth path
r = client.post("/api/admin/print/text", json={"body": "x"})
print("no bearer (expect 401) ->", r.status_code, r.get_json())

# error paths
r = client.post("/api/admin/print/text", headers=AUTH, json={"body": ""})
print("empty body (expect err)->", r.status_code, r.get_json())

r = client.post("/api/admin/print/todo", headers=AUTH, json={"items": []})
print("empty todo (expect err)->", r.status_code, r.get_json())

print("done.")
