"""Кодек deep-link payload'ов между сайтом и ботом.

Формат (Telegram допускает в ?start= только [A-Za-z0-9_-]):
  ORD<b64url json>   — корзина с сайта: {"src": "site", "items": {pid: qty}}
  consult-<pid>      — открыть диалог по конкретному товару/услуге
"""
import base64
import json

CONSULT = "consult-"
ORDER = "ORD"


def encode_cart(items: dict[str, int], src: str = "site") -> str:
    raw = json.dumps({"src": src, "items": items}, ensure_ascii=False, separators=(",", ":"))
    b = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
    return ORDER + b.rstrip("=")


def decode(payload: str) -> tuple[str, dict | str] | None:
    """('order', {'items': {...}, 'src': ...}) | ('consult', pid) | None"""
    if not payload:
        return None
    if payload.startswith(CONSULT):
        return "consult", payload[len(CONSULT):]
    if payload.startswith(ORDER):
        try:
            b = payload[len(ORDER):]
            b += "=" * (-len(b) % 4)
            data = json.loads(base64.urlsafe_b64decode(b).decode("utf-8"))
            if isinstance(data.get("items"), dict):
                return "order", data
        except Exception:
            return None
    return None
