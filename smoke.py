"""Quick smoke test — exercises routes in DRY_RUN without touching USB."""
import config
config.DRY_RUN = True

from app import app

client = app.test_client()

print("GET /api/ping          ->", client.get("/api/ping").get_json())

r = client.post("/api/preview", json={"body": "# HI\n> centered\n- bullet\n[x] done"})
print("POST /api/preview      ->", r.status_code, "preview lines:",
      len(r.get_json().get("preview", "").splitlines()))

r = client.post("/api/print/text", json={"body": "# TEST\n> smoke test"})
print("POST /api/print/text   ->", r.status_code, r.get_json())

r = client.post("/api/print/quote", json={})
print("POST /api/print/quote  ->", r.status_code, r.get_json())

r = client.post("/api/print/dice", json={"count": 3, "sides": 20})
print("POST /api/print/dice   ->", r.status_code, r.get_json())

r = client.post("/api/print/eight_ball", json={"question": "will it print?"})
print("POST /api/print/8ball  ->", r.status_code, r.get_json())

r = client.post("/api/print/todo", json={"title": "X", "items": ["a", "b"]})
print("POST /api/print/todo   ->", r.status_code, r.get_json())

r = client.post("/api/print/receipt", json={
    "store": "S",
    "items": [{"name": "apple", "qty": 2, "price": 1.50}],
    "tax_rate": 8.0,
})
print("POST /api/print/receipt->", r.status_code, r.get_json())

r = client.post("/api/print/label", json={"text": "FRAGILE"})
print("POST /api/print/label  ->", r.status_code, r.get_json())

r = client.post("/api/print/ascii", json={"name": "cat"})
print("POST /api/print/ascii  ->", r.status_code, r.get_json())

r = client.post("/api/print/now", json={})
print("POST /api/print/now    ->", r.status_code, r.get_json())

# error paths
r = client.post("/api/print/text", json={"body": ""})
print("empty body (expect err)->", r.status_code, r.get_json())

r = client.post("/api/print/todo", json={"items": []})
print("empty todo (expect err)->", r.status_code, r.get_json())

print("done.")
