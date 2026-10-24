"""Telegram-бот AutoShop: каталог, корзина, оплата, синк остатков в GitHub,
консультант продумывает вопросы о товарах и продаёт."""
import datetime
import html as _h
import uuid

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import ai, nlu, payload as payload_codec, storage
from . import config as cfg

router = Router()
CART: dict[int, dict[str, int]] = {}
ORDERS: dict[str, dict] = {}


class St(StatesGroup):
    consult = State()
    name = State()
    contact = State()


def esc(s) -> str:
    return _h.escape(str(s or ""))


def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def btn(text, data="", url="") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data or "noop", url=url or None)


def conf() -> dict:
    return storage.load_config()


def local_stock() -> dict:         # локальная копия (быстро, для карточек)
    return {p["id"]: p["qty"] for p in storage.products()}


def demo_url(pid: str) -> str:
    """URL живого демо (Orbit CRM): конфиг/env главнее каталога."""
    crm = conf().get("crm_demo_url") or ""
    if pid == "orbit-crm" and crm.strip():
        return crm.strip()
    return (storage.product(pid).get("demo") or {}).get("url") or ""


# ---------------- тексты витрины ----------------

def catalog_text() -> str:
    lines = ["🛍 <b>Каталог</b> — товары здесь это реальные работающие проекты:\n"]
    for p in storage.products():
        lines.append(f"{p['emoji']} <b>{esc(p['name'])}</b> · {p['price']} ₽ {esc(p['unit'])}\n{esc(p['tagline'])}")
    return "\n\n".join(lines)


def catalog_kb() -> InlineKeyboardMarkup:
    rows = [[btn(f"{p['emoji']} {p['name']}", f"prod:{p['id']}")] for p in storage.products()]
    rows.append([btn("🛒 Корзина", "act:cart"), btn("🏠 Меню", "act:menu")])
    return kb(rows)


def product_text(pid: str) -> str:
    p = storage.product(pid)
    q = local_stock()[pid]
    feats = "\n".join(f"  ✦ {esc(f)}" for f in p["features"])
    return (f"{p['emoji']} <b>{esc(p['name'])}</b>\n\n{esc(p['tagline'])}\n\n{feats}\n\n"
            f"💵 Цена: <b>{p['price']} ₽ {esc(p['unit'])}</b>\n📦 В наличии: <b>{q}</b>")


def product_kb(pid: str) -> InlineKeyboardMarkup:
    p = storage.product(pid)
    c = conf()
    rows = [[btn("➕ В корзину", f"add:{pid}"), btn(f"💬 Задать вопрос", f"ask:{pid}")]]
    d = storage.product(pid).get("demo") or {}
    url = demo_url(pid)
    if d.get("type") == "showcase":
        site = c.get("site_url", "").rstrip("/") or "https://" + cfg.GITHUB_REPO.replace("/", ".github.io/")
        rows.append([btn("🎨 Открыть витрину проекта", url=f"{site}/tts.html")])
    elif d.get("type") == "url" and url.startswith("http"):
        rows.append([btn("▶️ Запустить живое демо", url=url)])
    elif d.get("type") == "url":
        rows.append([btn("▶️ Демо — открывается после настройки", f"demo:{pid}")])
    if pid in ("tgbot", "ai-int"):
        rows.append([btn("📝 Обсудить мою задачу", f"consult:{pid}")])
    rows.append([btn("⬅️ Каталог", "act:catalog"), btn("🏠 Меню", "act:menu")])
    return kb(rows)


def menu_kb() -> InlineKeyboardMarkup:
    return kb([
        [btn("🛍 Каталог", "act:catalog"), btn("🚀 Проекты", "act:demos")],
        [btn("🛒 Корзина", "act:cart"), btn("❓ Помощь", "act:help")],
    ])


# ---------------- команды ----------------

@router.message(CommandStart())
async def start(m: Message, state: FSMContext, command: CommandObject | None = None):
    await state.clear()
    payload = (command.args if command else "") or ""
    if payload and await handle_payload(m, payload, state):
        return
    await m.answer(
        f"👋 Привет, <b>{esc(m.from_user.first_name)}</b>!\n\n"
        "Я магазин <b>AutoShop</b>: внутри продаются реальные работающие проекты — "
        "CRM, голосовой ИИ-оператор, боты и AI-интеграции.\n\n"
        "🎯 Опишите вашу задачу в свободной форме — подберу решение и доведу сделку до конца.",
        reply_markup=menu_kb())


async def handle_payload(m: Message, payload: str, state: FSMContext) -> bool:
    """Ссылки deep-link: ORD<b64> (корзина сайта), consult-<pid> (диалог по товару)."""
    got = payload_codec.decode(payload)
    if got is None:
        return False
    what, value = got

    if what == "consult":
        if storage.product(value):
            await _start_consult(m, value, state)
            return True
        return False

    data = value
    raw_items = data.get("items") or {}
    valid = {pid: max(1, int(q)) for pid, q in raw_items.items() if storage.product(pid)}
    if not valid:
        return False
    CART[m.from_user.id] = valid
    names = "\n".join(f"• {storage.product(p)['name']} × {q}" for p, q in valid.items())
    total = sum(storage.product(p)["price"] * q for p, q in valid.items())
    src = "с сайта" if data.get("src") == "site" else "со ссылкой"
    await m.answer(
        f"🛒 Принял корзину {src}:\n{names}\n\n<b>Итого: {total} ₽</b>\n\n"
        "📝 <b>Оформление заказа</b>\nКак к вам обращаться?")
    await St.name.set()
    return True


async def _start_consult(m: Message, pid: str, state: FSMContext) -> bool:
    p = storage.product(pid)
    await state.set_state(St.consult)
    await m.answer(
        f"{p.get('emoji', '')} <b>{esc(p['name'])}</b>\n\n"
        "Чтобы я посчитал вам точнее, ответьте в одном сообщении:\n"
        "1️⃣ Что за бизнес/процесс?\n"
        "2️⃣ Что конкретно требует автоматизации?\n"
        "3️⃣ Сроки и ориентир по бюджету?")
    return True


# ---------------- callbacks ----------------

@router.callback_query(F.data)
async def callbacks(cq: CallbackQuery, state: FSMContext):
    d = cq.data
    m = cq.message
    def A(text="", alert=False):
        return cq.answer(text or None, show_alert=alert)

    if d == "noop":
        return await A()

    if d == "act:menu":
        await state.clear()
        await m.edit_text("🏠 Главное меню 👇", reply_markup=menu_kb())
        return await A()

    if d == "act:catalog":
        await m.edit_text(catalog_text(), reply_markup=catalog_kb())
        return await A()

    if d == "act:demos":
        txt = ("🚀 <b>Мои проекты</b>:\n\n"
               "🧩 <b>Orbit CRM</b> — полноценная CRM: канбан, сделки, отчёты, AI-ассистент.\n"
               "🎙 <b>AI Call Center</b> — голосовой ИИ-оператор для бизнеса.\n"
               "🛍 <b>AutoShop</b> — этот магазин сам является проектом.")
        rows = []
        if demo_url("orbit-crm"):
            rows.append([btn("▶️ Живое демо Orbit CRM", url=demo_url("orbit-crm"))])
        else:
            rows.append([btn("🧩 CRM — демо по запросу", "demo:orbit-crm")])
        c = conf()
        site = c.get("site_url", "").rstrip("/") or "https://" + cfg.GITHUB_REPO.replace("/", ".github.io/")
        rows.append([btn("🎨 Витрина AI Call Center", url=f"{site}/tts.html")])
        rows.append([btn("🏠 Меню", "act:menu")])
        await m.edit_text(txt, reply_markup=kb(rows))
        return await A()

    if d == "act:help":
        await m.edit_text(
            "Я не просто меню — я продаю высокотехнологичные проекты 😉\n\n"
            "Пишите текстом любым русским: «хочу бот для записи клиентов», «сколько стоит срм», "
            "«какие проекты есть» — пойму. Или выбирайте ниже: 👇",
            reply_markup=menu_kb())
        return await A()

    if d.startswith("prod:"):
        pid = d.split(":")[1]
        await m.edit_text(product_text(pid), reply_markup=product_kb(pid))
        return await A()

    if d.startswith("add:"):
        pid = d.split(":")[1]
        CART.setdefault(cq.from_user.id, {})
        CART[cq.from_user.id][pid] = CART[cq.from_user.id].get(pid, 0) + 1
        await A(f"➕ {storage.product(pid)['name']} в корзине!")
        return await cart_view(m, cq.from_user.id)

    if d.startswith("inc:"):
        CART[cq.from_user.id][d.split(":")[1]] += 1
        await A()
        return await cart_view(m, cq.from_user.id)

    if d.startswith("dec:"):
        pid = d.split(":")[1]
        c = CART.setdefault(cq.from_user.id, {})
        c[pid] = max(0, c.get(pid, 0) - 1)
        if not c[pid]:
            c.pop(pid)
        await A()
        return await cart_view(m, cq.from_user.id)

    if d == "cart:clr":
        CART[cq.from_user.id] = {}
        await A("🗑 Очищено")
        return await cart_view(m, cq.from_user.id)

    if d.startswith("consult:") or d.startswith("ask:"):
        pid = d.split(":")[1]
        await state.set_state(St.consult)
        p = storage.product(pid)
        await m.answer(f"💬 Тема: <b>{esc(p['name'])}</b>\n\n"
                       "Задайте вопрос или опишите задачу — отвечу подробно и по делу.")
        return await A()

    if d.startswith("demo:"):
        return await A(f"Живая ссылка выдаётся после настройки: {storage.product(d[5:])['name']}. "
                       "Могу ответить на любой вопрос по проекту прямо сейчас.", alert=True)

    if d.startswith("pay:"):
        """Оплата: списание остатка + журнал + синк GitHub."""
        o = ORDERS.pop(d[4:], None)
        if not o:
            return await A("Заказ уже обработан", alert=True)
        items = o["items"]
        (name, contact, uid, uname, chat_id) = o["user"]
        for p, q in items.items():
            storage.log_sale(uid, f"{name} / {uname} / {contact}", p, q, "telegram")
        # локальное списание
        data = storage._load_local()
        by_id = {x["id"]: x for x in data["products"]}
        for p, q in items.items():
            if p in by_id:
                by_id[p]["qty"] = max(0, by_id[p]["qty"] - q)
        data["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        mode = storage.sync_to_github(data)
        await A("Оплата подтверждена √")
        rows = "\n".join(f"• {storage.product(p)['name']} × {q}" for p, q in items.items())
        await m.edit_text(
            f"✅ <b>Оплачено!</b>\n\n{rows}\n\n"
            "🎁 Дальше:\n"
            "— доступ и материалы отправил/отправлю сюда;\n"
            "— журнал продажи записан, остаток обновился на сайте.\n\n"
            "Нужен ещё один проект? 😎",
            reply_markup=kb([[btn("🚀 Проекты", "act:demos")], [btn("🏠 Меню", "act:menu")]]))
        print(f"[pay] order {d[4:]} → sync: {mode}")
        return

    if d.startswith("cancel:"):
        ORDERS.pop(d[7:], None)
        await m.edit_text("🚫 Заказ отменён.", reply_markup=menu_kb())
        return await A()

    await A()


async def cart_view(m: Message, uid: int):
    items = CART.setdefault(uid, {})
    if not items:
        return await m.edit_text("🛒 Корзина пуста. Заглянешь в каталог? 👇", reply_markup=kb([[btn("🛍 Каталог", "act:catalog")]]))
    total = sum(storage.product(p)["price"] * q for p, q in items.items())
    lines = "\n".join(f"• {storage.product(p)['name']} × {q} — {storage.product(p)['price'] * q} ₽" for p, q in items.items())
    rows = [[btn("➖", f"dec:{p}"), btn(f"{storage.product(p)['name'][:16]} · {q}", f"prod:{p}"), btn("➕", f"inc:{p}")] for p, q in items.items()]
    rows.append([btn(f"💳 Оплатить · {total} ₽", "cart:checkout")])
    rows.append([btn("🗑 Очистить", "cart:clr"), btn("🏠 Меню", "act:menu")])
    await m.edit_text(f"🛒 <b>Корзина</b>\n\n{lines}\n\n<b>Итого: {total} ₽</b>", reply_markup=kb(rows))


#. Telegram WebApp доступен только HTTPS-страницам — включается после деплоя
@router.callback_query(F.data == "cart:checkout")
async def checkout(cq: CallbackQuery, state: FSMContext):
    if not CART.get(cq.from_user.id):
        return await cq.answer("Корзина пуста", show_alert=True)
    await state.set_state(St.name)
    await cq.message.answer("📝 <b>Оформление заказа</b>\nКак к вам обращаться?")
    await cq.answer()


# ---------------- FSM ----------------

@router.message(St.name)
async def order_name(m: Message, state: FSMContext):
    if len(m.text or "") < 2:
        return await m.answer("Напишите имя 🙏")
    await state.update_data(name=m.text[:60])
    await state.set_state(St.contact)
    await m.answer("📞 Контакт для связи (телефон или @username):")


@router.message(St.contact)
async def order_contact(m: Message, state: FSMContext):
    data = await state.get_data()
    items = CART.get(m.from_user.id, {})
    if not items:
        await state.clear()
        return await m.answer("Корзина опустела 🤷 Начнём заново?", reply_markup=menu_kb())
    oid = uuid.uuid4().hex[:8].upper()
    total = sum(storage.product(p)["price"] * q for p, q in items.items())
    names = "\n".join(f"• {storage.product(p)['name']} × {q} — {storage.product(p)['price'] * q} ₽"
                      for p, q in items.items())
    ORDERS[oid] = {"items": items,
                   "user": (data["name"], (m.text or "")[:80], m.from_user.id,
                            "@" + (m.from_user.username or "-"), m.chat.id)}
    await state.clear()
    CART[m.from_user.id] = {}
    await m.answer(
        f"🧾 <b>Заказ {oid}</b>\n{names}\n<b>Итого: {total} ₽</b>\n"
        f"👤 {esc(data['name'])} · {esc(m.text[:80])}\n\nОплата 👇",
        reply_markup=kb([[btn("✅ Оплатить (демо)", f"pay:{oid}")],
                         [btn("✖️ Отменить", f"cancel:{oid}")]]))


@router.message(St.consult)
async def consult(m: Message, state: FSMContext):
    """Консультант-продавец: отвечает по товарам и доводит до покупки."""
    products_brief = "\n".join(
        f"{p['emoji']} {p['name']} — {p['tagline']} Цена {p['price']} ₽ {p['unit']}. Умеет: {'; '.join(p['features'])}"
        for p in storage.products())
    system = (
        "Ты — дружелюбный продавец в Telegram-боте магазина AutoShop. Отвечай по-русски, живо, 3-6 предложений.\n"
        "Твоя цель: понять задачу клиента, порекомендовать подходящий товар и предложить следующий шаг "
        "(оформить заказ, задать вопрос, посмотреть демо).\n"
        "Если вопрос вне каталога — честно скажи и верни к товарам.\n"
        "Товары магазина:\n" + products_brief +
        "\nВ конце ответа мягко сделай призыв: «хотите — добавлю в корзину / покажу демо».")
    body = await ai.chat([{"role": "system", "content": system}, {"role": "user", "content": (m.text or "")[:800]}])
    await state.clear()
    await m.answer(body or "Не смог распознать — покажу каталог 👇", reply_markup=menu_kb())


# ---------------- natural language ----------------

GIB = ["Ядро понимания русского подшутило над этим 😅 Но я могу: показать каталог, собрать корзину, "
       "ответить на любой проектный вопрос. Давай с этого:",
       "Честно — не расшифровал 🙂 Меню всё равно полезнее: ",]

@router.message()
async def nlu_router(m: Message, state: FSMContext):
    text = m.text or ""
    intent, pid = nlu.guess(text)
    if intent == "cart":
        return await cart_view(m, m.from_user.id)
    if intent == "catalog":
        if pid:
            return await m.answer(product_text(pid), reply_markup=product_kb(pid))
        return await m.answer(catalog_text(), reply_markup=catalog_kb())
    if intent == "checkout":
        if CART.get(m.from_user.id):
            await state.set_state(St.name)
            return await m.answer("Оформляю заказ 🧾 Как к вам обращаться?")
        return await m.answer("Корзина пуста — сначала товар 👇", reply_markup=menu_kb())
    if intent == "demos":
        return await m.answer(
            "🚀 Проекты: CRM, голосовой ИИ-оператор, бот и AI-интеграция. Опишите задачу — покажу нужный.",
            reply_markup=menu_kb())
    if intent == "help":
        return await m.answer("Пишите по-русски как удобно: «какие есть», «сколько стоит бот», «нужна CRM» — пойму 🙂",
                              reply_markup=menu_kb())
    body = await ai.chat([{"role": "system", "content": (
        "Ты — продавец-консультант в Telegram-боте магазина AutoShop. Отвечай кратко и по делу, "
        "рекомендуй товары каталога и веди к покупке. Каталог: "
        + "; ".join(f"{p['name']} ({p['price']}₽)" for p in storage.products()))},
        {"role": "user", "content": text[:800]}])
    if body:
        return await m.answer(body, reply_markup=menu_kb())
    await m.answer(GIB[len(text) % len(GIB)], reply_markup=menu_kb())
