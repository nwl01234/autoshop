"""NLU бота: интенции (паттерн → fuzzy-фолбэк) + поиск товара (точный ключ → fuzzy).

Держим в уме кривой русский: «католог», «хачу срм», «ттс колл центр», «автошоп».
"""
import re
from difflib import SequenceMatcher

PATTERN_RULES = [
    ("checkout", r"оплач\w*|оплат\w*|оформить|чекаут"),
    ("cart",     r"кор[зс]ин|карзин|добав|в\s+корз"),
    ("catalog",  r"каталог\w*|товар\w*|ассортимен\w*|магазин|купить|куплю"),
    ("demos",    r"демо|демонстрац|запусти|проект\w*"),
    ("help",     r"помо[щч]|help|\?$"),
    ("gibberish", r"^лол$|лмао|ъыъ|азаза|azaz|^хд$|бип|буп|^ыва$|куку"),
]
FUZZY_RULES = {  # если паттерн не попал, интенцию спарим по схожести слова
    "catalog":  ["каталог", "категория", "показать всё", "асортимент"],
    "cart":     ["корзина", "корз"],
    "checkout": ["оформить", "оплатить", "заказать"],
    "demos":    ["демо", "проекты", "витрина"],
    "help":     ["помощь", "справка", "что ты умеешь"],
}
KEYS = {
    "autoshop":  ["autoshop", "автосхоп", "автошоп", "авташоп", "авашоп", "магазин под ключ", "автономный магазин"],
    "orbit-crm": ["orbit crm", "срм", "црм", "орбит", "мини-срм", "система продаж", "лид", "воронка", "сделк"],
    "tts":       ["call center", "каллцентр", "калл-центр", "колл-центр", "колл", "озвучк", "голос", "tts", "ттс", "звонк", "оператор", "whisper"],
    "tgbot":     ["бот под заказ", "заказать бот", "бот для бизнеса", "телеграм бот", "телеграм-бот", "тгбот"],
    "ai-int":    ["ии", "нейросет", "чат-бот", "rag", "автоматизация", "интеграц", "ии-автоматиза"],
}

THRESH = 0.58


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е").strip()


def guess_product(t: str) -> str | None:
    # 1) точное вхождение ключа бьёт fuzzy (важно для коротких: «срм», «ттс»)
    for pid, keys in KEYS.items():
        for k in keys:
            if len(k) >= 3 and k in t:
                return pid
    # 2) fuzzy по опечатке
    best_pid, best = None, 0.0
    for pid, keys in KEYS.items():
        for k in keys:
            r = SequenceMatcher(None, t, k).ratio()
            if r > best:
                best_pid, best = pid, r
    return best_pid if best >= THRESH else None


def _fuzzy_intent(t: str) -> str | None:
    # сравниваем по словам: «хачу католог» → «католог»~«каталог» = 0.93
    tokens = re.findall(r"\w{3,}", t) or [t]
    for intent, words in FUZZY_RULES.items():
        for w in words:
            for tok in tokens:
                if w in tok or tok in w or SequenceMatcher(None, tok, w).ratio() >= 0.72:
                    return intent
    return None


def guess(text: str) -> tuple[str, str | None]:
    t = _norm(text)
    for intent, pat in PATTERN_RULES:
        if re.search(pat, t):
            return intent, guess_product(t)
    intent = _fuzzy_intent(t)
    if intent:
        return intent, guess_product(t)
    pid = guess_product(t)
    return ("catalog", pid) if pid else ("gibberish", None)
