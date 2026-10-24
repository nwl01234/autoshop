# -*- coding: utf-8 -*-
"""LUMÉ backend — REST API + статика, только stdlib (порт 8300).

Запуск:  python server/app.py   (или start.bat из корня)

API:
  GET  /api/products            — каталог; available = qty − активные резервы
  GET  /api/status              — здоровье площадки + консультант
  POST /api/orders              — ЗАКАЗ = РЕЗЕРВ на 15 мин (не списывает склад!)
                                  {"items": {pid: qty}} → {"ok":true,"id":...,"ttl":900}
  POST /api/orders/<id>/confirm — оплата: списание остатков, статус paid
  POST /api/orders/<id>/cancel  — отказ: резерв снимается
  GET  /api/orders              — журнал заказов
  GET  /api/stock.csv           — выгрузка состояния склада (Excel-совместимо)

Источник правды по количествам — data/stock.csv (столбцы: id,qty).
Если в конфиге/окружении задан SHEET_CSV_URL (публикация Google Sheets "csv"),
таблица тянется оттуда и кладётся в stock.csv — правишь таблицу, сайт живёт.

Сеньёр-логика резервов: неплательщик не запирает склад — TTL 15 минут,
резервы живут в памяти (process-restart = честный сброс).
"""
import csv
import io
import json
import os
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOCK = threading.RLock()

RESERVE_TTL = 15 * 60           # секунд
RESERVES: dict[str, dict] = {}  # oid -> {"items": {pid: qty}, "deadline": float}

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
    tmp = DATA / (name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA / name)


# ---------------- stock source: таблица (CSV / Google Sheets) ----------------
def sheet_url() -> str | None:
    """Google Sheets → Файл → «Опубликовать в интернете» → CSV. URL можно держать
    в окружении SHEET_CSV_URL или data/sheet_url.txt."""
    u = os.environ.get("SHEET_CSV_URL")
    if not u:
        f = DATA / "sheet_url.txt"
        if f.exists():
            u = f.read_text(encoding="utf-8").strip()
    return u or None


def read_stock_csv(text: str) -> dict[str, int]:
    """CSV со столбцами id,qty ( ';' или ',' ), строки-комментарии # — пропуск."""
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    out = {}
    try:
        for row in csv.DictReader(io.StringIO("\n".join(lines))):
            pid = (row.get("id") or "").strip().lower()
            q = (row.get("qty") or "").strip()
            if pid and q.isdigit():
                out[pid] = int(q)
    except Exception:
        pass
    return out


def sync_sheet() -> dict[str, int] | None:
    """Тянем опубликованный Google Sheets (CSV) и обновляем data/stock.csv."""
    u = sheet_url()
    if not u:
        return None
    try:
        txt = urllib.request.urlopen(
            urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15
        ).read().decode("utf-8", "replace")
        stock = read_stock_csv(txt)
        if stock:
            save_stock_csv(stock)
            return stock
    except Exception:
        pass
    return None


def stock_path() -> Path:
    return DATA / "stock.csv"


def load_stock() -> dict[str, int]:
    """data/stock.csv (Excel открывает как есть). Пусто/нет файла — создаём из
    текущих qty products.json."""
    f = stock_path()
    if f.exists():
        s = read_stock_csv(f.read_text(encoding="utf-8"))
        if s:
            return s
    doc = load("products.json") or {"products": []}
    init = {p["id"]: int(p["qty"]) for p in doc.get("products", [])}
    save_stock_csv(init)
    return init


def save_stock_csv(stock: dict[str, int]) -> None:
    buf = io.StringIO()
    buf.write("# LUMÉ stock · источник правды; правьте руками в Excel/Sheets\n")
    buf.write("id,qty\n")
    for pid, q in sorted(stock.items()):
        buf.write(f"{pid},{q}\n")
    stock_path().write_text(buf.getvalue(), encoding="utf-8")


# ---------------- reserves ----------------
def purge_expired():
    now = time.time()
    for oid in [k for k, v in RESERVES.items() if v["deadline"] <= now]:
        RESERVES.pop(oid, None)


def reserved_now() -> dict[str, int]:
    res: dict[str, int] = {}
    for v in RESERVES.values():
        for pid, q in v["items"].items():
            res[pid] = res.get(pid, 0) + q
    return res


def committed_qty() -> None:
    """products.json qty уже содержит списанные остатки (бот платит списанием),
    поэтому available = products.qty − активные резервы. Начальные остатки полки
    живут в data/stock.csv — источник правды для ручного/Sheets управления."""
    return None


def api_products() -> dict:
    with LOCK:
        purge_expired()
        sync_sheet()  # если сеть есть — обновит из таблицы, тихо
        doc = load("products.json") or {}
        stock = load_stock()          # таблица выше приоритетом
        res = reserved_now()
        for p in doc.get("products", []):
            pid = p["id"]
            if pid in stock:
                p["qty"] = max(0, stock[pid])
            p["reserved"] = res.get(pid, 0)
            p["available"] = max(0, p["qty"] - res.get(pid, 0))
    return {"brand": doc.get("brand", "LUMÉ"), "products": doc.get("products", []),
            "updated": doc.get("updated"), "ttl": RESERVE_TTL}


def api_status() -> dict:
    st = load("bot_status.json") or {}
    return {"shop": "online", "consultant": "online" if st.get("online") else "away",
            "reserves": len(RESERVES), "ttl": RESERVE_TTL,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S")}


def _commit_stock(deals: dict[str, int]) -> None:
    """Финальное списание: правит И таблицу склада, И products.json (для бота)."""
    with LOCK:
        stock = load_stock()
        for pid, q in deals.items():
            stock[pid] = max(0, stock.get(pid, 0) - q)
        save_stock_csv(stock)

        doc = load("products.json") or {"products": []}
        by_id = {p["id"]: p for p in doc.get("products", [])}
        for pid, q in deals.items():
            if pid in by_id:
                by_id[pid]["qty"] = max(0, by_id[pid]["qty"] - q)
        doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save("products.json", doc)
    sync_bot_push(deals)


def sync_bot_push(deals: dict[str, int]) -> None:
    """Мостик для бота: локально остатки бота читают products.json — уже обновлён."""
    return None


def api_order(body: dict) -> tuple[int, dict]:
    """Резерв (TTL), НЕ списание."""
    items = (body or {}).get("items")
    if not isinstance(items, dict) or not items:
        return 400, {"ok": False, "error": "items: {pid: qty} обязательны"}
    with LOCK:
        purge_expired()
        doc = load("products.json") or {"products": []}
        by_id = {p["id"]: p for p in doc.get("products", [])}
        stock = load_stock()
        res = reserved_now()

        clean = {}
        for pid, q in items.items():
            p = by_id.get(str(pid).lower())
            try:
                q = int(q)
            except (TypeError, ValueError):
                return 400, {"ok": False, "error": f"qty не число: {pid}"}
            if q < 1 or not p:
                return 400, {"ok": False, "error": f"товар не найден: {pid}"}
            clean[str(pid).lower()] = q

        lacking = {}
        for pid, q in clean.items():
            available = max(0, (stock.get(pid, by_id[pid]["qty"])) - res.get(pid, 0))
            if q > available:
                lacking[pid] = available
        if lacking:
            return 409, {"ok": False, "error": "меньше, чем хочется — сейчас доступно:",
                         "stock": lacking, "ttl": RESERVE_TTL}

        oid = time.strftime("%y%m%d") + "-" + uuid.uuid4().hex[:6].upper()
        RESERVES[oid] = {"items": clean, "deadline": time.time() + RESERVE_TTL,
                         "source": "site"}
        total = sum(by_id[pid]["price"] * q for pid, q in clean.items())
    return 200, {"ok": True, "id": oid, "total": total, "ttl": RESERVE_TTL,
                 "items": clean, "note": "резерв 15 минут · списание после оплаты"}


def api_confirm(oid: str) -> tuple[int, dict]:
    with LOCK:
        r = RESERVES.get(oid)
        if not r:
            return 404, {"ok": False, "error": "резерв истёк или не найден"}
        deals = dict(r["items"])
        RESERVES.pop(oid, None)
    _commit_stock(deals)
    orders = load("orders.json") or {"orders": []}
    orders["orders"].insert(0, {"id": oid, "items": deals, "status": "paid",
                                "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "site"})
    for o in orders["orders"][:50]:
        pass
    orders["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save("orders.json", orders)
    return 200, {"ok": True, "id": oid, "status": "paid", "items": deals}


def api_cancel(oid: str) -> tuple[int, dict]:
    with LOCK:
        r = RESERVES.pop(oid, None)
    return (200, {"ok": True, "status": "cancelled"}) if r else (
        404, {"ok": False, "error": "резерв не найден"})


def api_orders_log() -> dict:
    return load("orders.json") or {"orders": []}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

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
        try:
            return self._get()
        except Exception as e:
            return self._send(500, {"ok": False, "error": e.__class__.__name__})

    def _get(self):
        route = urlparse(self.path).path
        if route == "/api/products":
            return self._send(200, api_products())
        if route == "/api/status":
            return self._send(200, api_status())
        if route == "/api/health":
            return self._send(200, {"ok": True, "reserves": len(RESERVES)})
        if route == "/api/orders":
            return self._send(200, api_orders_log())
        if route == "/api/stock.csv":
            f = stock_path()
            return self._send(200, f.read_bytes(), "text/csv; charset=utf-8") if f.exists() else \
                self._send(404, {"ok": False, "error": "stock.csv нет"})
        return self._static(route)

    def do_POST(self):
        try:
            return self._post()
        except Exception as e:
            return self._send(500, {"ok": False, "error": e.__class__.__name__})

    def _post(self):
        route = urlparse(self.path).path
        parts = [x for x in route.split("/") if x]
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return self._send(400, {"ok": False, "error": "bad json"})
        if route == "/api/orders":
            code, body = api_order(body)
            return self._send(code, body)
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "orders":
            oid, action = parts[2], parts[3]
            if action == "confirm":
                code, body = api_confirm(oid)
                return self._send(code, body)
            if action == "cancel":
                code, body = api_cancel(oid)
                return self._send(code, body)
        return self._send(404, {"ok": False, "error": "not found"})


def run():
    port = int(os.environ.get("PORT") or 8300)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"LUMÉ backend: http://127.0.0.1:{port}  ·  резервов: {len(RESERVES)} · TTL {RESERVE_TTL//60} мин")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
