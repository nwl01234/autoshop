"""Тесты ядра LUMÉ без сети: payload-кодек, каталог косметики, NLU, консультант."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import nlu, payload, sales, storage  # noqa: E402


def test_payload_roundtrip():
    items = {"serum": 1, "spf50": 2}
    token = payload.encode_cart(items)
    assert token.startswith("ORD")
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in token), \
        "Telegram ?start= не допускает символы вне [A-Za-z0-9_-]"
    what, data = payload.decode(token)
    assert what == "order" and data["items"] == items and data["src"] == "site"


def test_payload_multibyte():
    token = payload.encode_cart({"eye": 1}, src="сайт")
    what, data = payload.decode(token)
    assert what == "order" and data["items"] == {"eye": 1}


def test_catalog_cosmetics():
    prods = storage.products()
    ids = {p["id"] for p in prods}
    assert {"serum", "retinol", "spf50", "vitc", "clay", "eye", "balm", "toner"} <= ids
    for p in prods:
        assert p["price"] > 0 and isinstance(p["qty"], int) and p["qty"] >= 0
        assert len(p["features"]) >= 2 and len(p["name"]) > 5


def test_sales_log(tmp_path):
    orig = storage.SALES_DB
    storage.SALES_DB = tmp_path / "sales.db"
    try:
        storage.log_sale(1, "tester", "serum", 1, "test")
        import sqlite3
        con = sqlite3.connect(storage.SALES_DB)
        rows = con.execute("SELECT product, qty, source FROM sales").fetchall()
        con.close()
        assert rows == [("serum", 1, "test")]
    finally:
        storage.SALES_DB = orig


def test_nlu_intents():
    assert nlu.guess("хачу каталог")[0] == "catalog"
    assert nlu.guess("корзина")[0] == "cart"
    assert nlu.guess("оформлю давай")[0] == "checkout"
    assert nlu.guess("доставка когда")[0] == "delivery"
    assert nlu.guess("ретинол ноч")[1] == "retinol"
    assert nlu.guess("маска от черных точек")[1] == "clay"
    assert nlu.guess("лол")[0] == "gibberish"


def test_nlu_fuzzy_typos():
    assert nlu.guess("ретинлл ночн")[1] == "retinol"
    assert nlu.guess("сывротка хналурон")[1] in ("serum", None)


def test_sales_answers():
    for q, must in {
        "осталось ли ретинола": "В наличии",
        "как наносить сыворотку": "капли",
        "доставка в Казань": "СДЭК",
        "возврат если аллергия": "возврат",
        "скидки будут": "скидок нет",
        "сколько стоит спф": "₽",
        "привет": "LUMÉ",
    }.items():
        a = sales.answer(q)
        assert a and must.lower() in a.lower(), (q, a)
    assert sales.answer("хаыф кыщшк йывап") is None
