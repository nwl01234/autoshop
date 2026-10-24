"""Каталог и остатки: одна копия data/products.json для сайта (GitHub Pages) и бота.

Без GITHUB_TOKEN бот работает локально; с токеном — коммитит в репо сайта сам.
"""
import base64
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import httpx

from . import config

DATA_PATH = config.DATA_DIR / "products.json"
CONFIG_PATH = config.DATA_DIR / "config.json"
SALES_DB = config.DATA_DIR / "sales.db"
API = "https://api.github.com"

_lock = threading.Lock()


def _load_local() -> dict:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_PATH.exists():
        raise FileNotFoundError("data/products.json отсутствует")
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def load_config() -> dict:
    d = {"bot_username": config.SHOP_BOT_USERNAME, "crm_demo_url": config.CRM_DEMO_URL,
         "tts_live_url": config.TTS_LIVE_URL}
    try:
        d.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return d


def save_config(url_key: str, value: str) -> None:
    d = load_config()
    d[url_key] = value
    CONFIG_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def products() -> list:
    return _load_local()["products"]


def product(pid: str) -> dict | None:
    return next((p for p in products() if p["id"] == pid), None)


def stock() -> dict:
    try:
        raw = httpx.get(f"https://raw.githubusercontent.com/{config.GITHUB_REPO}/main/data/products.json", timeout=10)
        return {p["id"]: p["qty"] for p in raw.json()["products"]}
    except Exception:
        return {p["id"]: p["qty"] for p in products()}


def sync_to_github(data: dict | None = None) -> str:
    """Обновление data/products.json в репо сайта (коммит → Pages сам перестроится)."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    d = data or _load_local()
    DATA_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    if not config.GITHUB_TOKEN:
        return "local"
    try:
        with httpx.Client(timeout=20) as c:
            r = c.get(f"{API}/repos/{config.GITHUB_REPO}/contents/data/products.json",
                      headers=_gh())
            sha = r.json().get("sha") if r.status_code == 200 else None
            body = {"message": "shop: sync stock", "content": _b64(DATA_PATH.read_text(encoding="utf-8"))}
            if sha:
                body["sha"] = sha
            r = c.put(f"{API}/repos/{config.GITHUB_REPO}/contents/data/products.json",
                      headers=_gh(), json=body)
            r.raise_for_status()
        return "github"
    except Exception as e:
        print(f"[sync] fallback local: {e.__class__.__name__}")
        return "local"


def _gh() -> dict:
    return {"Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"}


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


# ---------- продажи (log «кто · что · когда») ----------

def log_sale(uid: int, tag: str, pid: str, qty: int, source: str) -> None:
    with _lock:
        con = sqlite3.connect(SALES_DB)
        con.execute("CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY, ts TEXT, uid INT, who TEXT, product TEXT, qty INT, source TEXT)")
        con.execute("INSERT INTO sales(ts, uid, who, product, qty, source) VALUES(?,?,?,?,?,?)",
                    (datetime.now().isoformat(timespec="seconds"), uid, tag, pid, qty, source))
        con.commit()
        con.close()
