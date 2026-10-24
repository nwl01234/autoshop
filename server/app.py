# -*- coding: utf-8 -*-
"""LUMÉ backend — REST API + статика, только stdlib (порт 8300).

Запуск:  python server/app.py      (или start.bat из корня)
API:
  GET  /api/products            — каталог (с живыми qty)
  GET  /api/status              — статус консультанта + здоровье
  POST /api/orders              — оформить заказ: {"items": {pid: qty}}
                                  валидация остатков, списание, orders.json
"""
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOCK = threading.RLock()

MIME = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".svg": "image/svg+xml", ".ico": "image/x-icon", ".webp": "image/webp",
}


def load(name: str):
    try:
        return json.loads((DATA / name).read_text(encoding="utf-8"))
    except Exception:
        return None


def save(name: str, doc) -> None:
    with LOCK:
        tmp = DATA / (name + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(DATA / name)


def api_products():
    doc = load("products.json") or {}
    return {"brand": doc.get("brand", "LUMÉ"), "products": doc.get("products", []),
            "updated": doc.get("updated")}


def api_status():
    st = load("bot_status.json") or {}
    return {"shop": "online", "consultant": "online" if st.get("online") else "away",
            "time": time.strftime("%Y-%m-%dT%H:%M:%S")}


def api_order(body: dict) -> tuple[int, dict]:
    """Списание склада с валидацией: атомарно по файлу."""
    items = (body or {}).get("items")
    if not isinstance(items, dict) or not items:
        return 400, {"ok": False, "error": "items: {pid: qty} обязательны"}
    doc = load("products.json") or {"products": []}
    by_id = {p["id"]: p for p in doc.get("products", [])}

    clean = {}
    for pid, q in items.items():
        p = by_id.get(pid)
        try:
            q = int(q)
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": f"qty не число: {pid}"}
        if q < 1 or not p:
            return 400, {"ok": False, "error": f"товар не найден: {pid}"}
        clean[pid] = q

    # сток-валидация
    lacking = {pid: by_id[pid]["qty"] for pid, q in clean.items() if q > by_id[pid]["qty"]}
    if lacking:
        return 409, {"ok": False, "error": "изменились остатки", "stock": lacking}

    oid = time.strftime("%y%m%d") + "-" + uuid.uuid4().hex[:6].upper()
    total = 0
    with LOCK:
        doc = load("products.json") or {"products": []}
        by_id = {p["id"]: p for p in doc.get("products", [])}
        for pid, q in clean.items():           # повторная проверка под локом
            if q > by_id[pid]["qty"]:
                return 409, {"ok": False, "error": "изменились остатки"}
        for pid, q in clean.items():
            by_id[pid]["qty"] = max(0, by_id[pid]["qty"] - q)
            total += by_id[pid]["price"] * q
        doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save("products.json", doc)

        orders = load("orders.json") or {"orders": []}
        orders["orders"].insert(0, {
            "id": oid, "items": clean, "total": total, "status": "paid",
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "site"})
        orders["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save("orders.json", orders)

    return 200, {"ok": True, "id": oid, "total": total, "items": clean}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # тихий лог

    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store" if ctype.startswith("application/json") else "max-age=300")
        self.end_headers()
        self.wfile.write(raw)

    def _static(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        f = (ROOT / path.lstrip("/")).resolve()
        if not str(f).startswith(str(ROOT.resolve())) or not f.is_file():
            return self._send(404, {"ok": False, "error": "not found"})
        return self._send(200, f.read_bytes(), MIME.get(f.suffix, "application/octet-stream"))

    def do_GET(self):
        p = urlparse(self.path)
        route = p.path
        if route == "/api/products":
            return self._send(200, api_products())
        if route == "/api/status":
            return self._send(200, api_status())
        if route == "/api/health":
            return self._send(200, {"ok": True})
        return self._static(route)

    def do_POST(self):
        if urlparse(self.path).path != "/api/orders":
            return self._send(404, {"ok": False, "error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return self._send(400, {"ok": False, "error": "bad json"})
        code, resp = api_order(body)
        return self._send(code, resp)


def run():
    port = 8300
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"LUMÉ backend: http://127.0.0.1:{port}  ·  Ctrl+C — стоп")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
