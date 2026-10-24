"""NLU магазина LUMÉ: интент + guess_product (substring + fuzzy)."""
import re
from difflib import SequenceMatcher

INTENTS = [
    ("cart",     r"корзин|в корзин|telega тележк|cart"),
    ("catalog",  r"каталог|товар|ассорт|что есть|показать вс|что прода"),
    ("checkout", r"оформ|оплат|заказ|чекоут|куплю|взять"),
    ("help",     r"помощь|что умеешь|как эт|help|¿"),
    ("delivery", r"достав|сдэк|почт|когда прид"),
]

KEYS = {
    "serum":  ["сыворотк", "serum", "гиалурон", "увлажнени", "хналурон"],
    "retinol": ["ретинол", "ретиналь", "ретин", "retinol"],
    "spf50":  ["spf", "спф", "сфп", "санскрин", "защит", "солнц", "солнца", "флюид"],
    "vitc":   ["витамин с", "витам с", "аскорб", "vitamin", "сияни", "постакне", "тон"],
    "clay":   ["маска", "глина", "глиня", "пор", "детокс", "чёрные точк"],
    "eye":    ["глаз", "мешк", "круги", "кофеин", "eyes"],
    "balm":   ["бальзам", "демакияж", "умыван", "очищени", "очищаю", "бальзам умыван"],
    "toner":  ["тонер", "aha", "bha", "кислот", "пилинг", "текстур"],
}


def guess(q: str) -> tuple[str, str | None]:
    """(intent, product_id | None)"""
    ql = (q or "").lower().replace("ё", "е").strip()
    if not ql:
        return ("gibberish", None)
    for name, pattern in INTENTS:
        if re.search(pattern, ql):
            return (name, guess_product(ql))
    if guess_product(ql):
        return ("product", guess_product(ql))
    # длинное кириллическое слово → похоже на нормальный вопрос
    if re.search(r"[а-яa-z]{4,}", ql):
        return ("faq", None)
    return ("gibberish", None)


def guess_product(q: str) -> str | None:
    t = (q or "").lower().replace("ё", "е")
    for pid, keys in KEYS.items():
        for k in keys:
            if len(k) >= 3 and k in t:
                return pid
    # fuzz по словам запроса
    words = re.findall(r"\w{4,}", t)
    best, score = None, 0.0
    for pid, keys in KEYS.items():
        for k in keys:
            for w in words:
                r = SequenceMatcher(None, w, k).ratio()
                if r > score:
                    best, score = pid, r
    return best if score >= 0.76 else None
