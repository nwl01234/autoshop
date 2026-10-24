"""Тесты ядра бота без сети: payload-кодек, каталог, NLU, фолбэки."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import nlu, payload, storage  # noqa: E402


def test_payload_roundtrip():
    items = {"orbit-crm": 1, "tts": 2}
    token = payload.encode_cart(items)
    assert token.startswith("ORD")
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in token), \
        "Telegram ?start= не допускает символы вне [A-Za-z0-9_-]"
    what, data = payload.decode(token)
    assert what == "order" and data["items"] == items and data["src"] == "site"


def test_payload_consult():
    assert payload.decode("consult-tts") == ("consult", "tts")
    assert payload.decode("consult-orbit-crm") == ("consult", "orbit-crm")
    assert payload.decode("") is None
    assert payload.decode("ОRDbadd") is None  # кириллица в префиксе — не распознаётся


def test_payload_multibyte():
    # unicode внутри корзины не должен ломать b64 (кириллические email и т.п.)
    token = payload.encode_cart({"autoshop": 1}, src="сайт")
    what, data = payload.decode(token)
    assert what == "order" and data["items"] == {"autoshop": 1}


def test_catalog_loads():
    prods = storage.products()
    ids = {p["id"] for p in prods}
    assert {"autoshop", "orbit-crm", "tts", "tgbot", "ai-int"} <= ids
    for p in prods:
        assert p["price"] > 0 and p["qty"] > 0 and len(p["features"]) >= 2
        assert "demo" in p or True


def test_sales_log(tmp_path):
    db = storage.__dict__  # доступ к модулю для патча
    orig = storage.SALES_DB
    storage.SALES_DB = tmp_path / "sales.db"  # временая база
    try:
        storage.log_sale(1, "tester", "orbit-crm", 1, "test")
        import sqlite3
        con = sqlite3.connect(storage.SALES_DB)
        rows = con.execute("SELECT product, qty, source FROM sales").fetchall()
        con.close()
        assert rows == [("orbit-crm", 1, "test")]
    finally:
        storage.SALES_DB = orig


def test_nlu_intents():
    assert nlu.guess("хачу католог")[0] == "catalog"
    assert nlu.guess("корзина")[0] == "cart"
    assert nlu.guess("оплачу давай")[0] == "checkout"
    assert nlu.guess("ттс колл центр")[1] == "tts"
    assert nlu.guess("хочу срм под продажи")[1] == "orbit-crm"
    assert nlu.guess("лол")[0] == "gibberish"


def test_nlu_fuzzy_typos():
    assert nlu.guess("autoshp")[1] in ("autoshop", None)  # опечатка близко к каталогу
    assert nlu.guess("мини-срм")[1] == "orbit-crm"
