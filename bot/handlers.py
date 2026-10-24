"""Telegram-бот AutoShop: каталог, корзина, оплата, синк остатков в GitHub,
оффлайн-консультант отвечает на любой вопрос и выводит диалог на сделку."""
import datetime
import html as _h
import uuid

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import nlu, payload as payload_codec, sales, storage, config as cfg

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


def site_base() -> str:
    c = conf()
    return (c.get("site_url", "").rstrip("/") or "https://" + cfg.GITHUB_REPO.replace("/", ".github.io/"))


def local_stock() -> dict:
    return {p["id"]: p["qty"] for p in storage.products()}


def demo_url(pid: str) -> str:
    """URL живого демо (Orbit CRM): конфиг/env главнее каталога."""
    crm = (conf().get("crm_demo_url") or "").strip()
    if pid == "orbit-crm" and crm:
        return crm
    return (storage.product(pid).get("demo") or {}).get("url") or ""


# ---------------- тексты витрин ----------------

def price_str(p: dict) -> str:
    """от 15 000 ₽ · внедрение — услуги без остатков."""
    pre = "от " if p.get("unit") == "от" else ""
    return f"{pre}{p['price']:,} ₽".replace(",", " ")


def catalog_text() -> str:
    lines = ["🛍 <b>Каталог</b> — каждый товар это работающая система:\n"]
    for p in storage.products():
        lines.append(f"{p['emoji']} <b>{esc(p['name'])}</b> — {price_str(p)}")
    return "\n".join(lines)


def catalog_kb() -> InlineKeyboardMarkup:
    rows = [[btn(f"{p['emoji']} {p['name']}", f"prod:{p['id']}")] for p in storage.products()]
    rows.append([btn("🏪 Открыть магазин (сайт)", url=site_base())])
    rows.append([btn("🛒 Корзина", "act:cart"), btn("🏠 Меню", "act:menu")])
    return kb(rows)


def product_text(pid: str) -> str:
    p = storage.product(pid)
    feats = "\n".join(f"  ✦ {esc(f)}" for f in p["features"])
    return (f"<b>{esc(p['name'])}</b>\n\n{esc(p['tagline'])}\n\n{feats}\n\n"
            f"💵 Стоимость: <b>{price_str(p)}</b> — разовая, внедрение и поддержка включены.")


def product_kb(pid: str) -> InlineKeyboardMarkup:
    p = storage.product(pid)
    d = p.get("demo") or {}
    rows = [[btn("➕ В корзину", f"add:{pid}"), btn("💬 Задать вопрос", f"ask:{pid}")]]
    if d.get("type") == "showcase":
        rows.append([btn("🎨 Открыть витрину проекта", url=f"{site_base()}/tts.html")])
    elif d.get("type") == "url" and demo_url(pid).startswith("http"):
        rows.append([btn("▶️ Запустить живое демо", url=demo_url(pid))])
    elif d.get("type") == "url":
        rows.append([btn("▶️ Демо — запросить показ", f"demo:{pid}")])
    rows.append([btn("🏪 Весь магазин (сайт)", url=site_base())])
    rows.append([btn("⬅️ Каталог", "act:catalog"), btn("🏠 Меню", "act:menu")])
    return kb(rows)


def product_card(pid: str) -> str:
    """URL обложки товара для фото-карточки (публичная страница Pages)."""
    return f"{site_base()}/img/products/{pid}.png"


def menu_kb() -> InlineKeyboardMarkup:
    return kb([
        [btn("🏪 Открыть магазин", url=site_base()), btn("🛍 Каталог", "act:catalog")],
        [btn("🚀 Мои проекты", "act:demos"), btn("🛒 Корзина", "act:cart")],
        [btn("❓ Помощь", "act:help")],
    ])


def fallback_kb() -> InlineKeyboardMarkup:
    return kb([
        [btn("🏪 Открыть магазин", url=site_base()), btn("🛍 Каталог", "act:catalog")],
        [btn("💬 Опишу свою задачу", "act:free")],
    ])


# ---------------- вход ----------------

@router.message(CommandStart())
async def start(m: Message, state: FSMContext, command: CommandObject | None = None):
    await state.clear()
    payload = (command.args if command else "") or ""
    if payload and await handle_payload(m, payload, state):
        return
    await m.answer(
        f"👋 Привет, <b>{esc(m.from_user.first_name)}</b>!\n\n"
        "Я магазин <b>AutoShop</b>: внутри — реальные работающие проекты: "
        "CRM, голосовой ИИ-оператор, боты и AI-интеграция.\n\n"
        "🎯 Опиши свою задачу в свободной форме — подберу решение и доведу до оплаты.",
        reply_markup=menu_kb())


async def handle_payload(m: Message, payload: str, state: FSMContext) -> bool:
    """Deep-link: ORD<b64> (корзина с сайта), consult-<pid> (диалог по товару)."""
    got = payload_codec.decode(payload)
    if got is None:
        return False
    what, value = got

    if what == "consult":
        if storage.product(value):
            await _start_consult(m, value, state)
            return True
        return False

    raw_items = (value or {}).get("items") or {}
    valid = {pid: max(1, int(q)) for pid, q in raw_items.items() if storage.product(pid)}
    if not valid:
        return False
    CART[m.from_user.id] = valid
    names = "\n".join(f"• {storage.product(p)['name']} × {q}" for p, q in valid.items())
    total = sum(storage.product(p)["price"] * q for p, q in valid.items())
    src = "с сайта" if (value or {}).get("src") == "site" else "со ссылкой"
    await m.answer(
        f"🛒 Принял корзину {src}:\n{names}\n\n<b>Итого: {total} ₽</b>\n\n"
        "📝 <b>Оформление заказа</b>\nКак к вам обращаться?")
    await St.name.set()
    return True


async def _start_consult(m: Message, pid: str, state: FSMContext) -> None:
    p = storage.product(pid)
    await state.set_state(St.consult)
    await m.answer(
        f"{p.get('emoji', '')} <b>{esc(p['name'])}</b>\n\n"
        "Чтобы я подобрал решение точно, ответьте одним сообщением:\n"
        "1️⃣ Что за бизнес/процесс?\n"
        "2️⃣ Что требует автоматизации?\n"
        "3️⃣ Сроки и ориентир по бюджету?")


def consult_reply(text: str) -> tuple[str, InlineKeyboardMarkup]:
    """Ответ consultaта: стандартные вопросы → база знаний; бриф → вывод на заказ."""
    body = sales.answer(text or "")
    if body:
        return body, menu_kb()
    return (
        "🗂 Задача принята. Смотрите, какие решения под ваш запрос обычно срабатывают:\n\n"
        "🤖 <b>Telegram-бот под заказ</b> — от 15000 ₽, 7–10 дней\n"
        "⚙️ <b>AI-автоматизация процессов</b> — от 25000 ₽\n"
        "🛍 <b>Автономный магазин под ключ</b> — 14900 ₽\n\n"
        "👉 Выбери направление кнопкой — добавлю в корзину и оформим заказ.",
        kb([
            [btn("🤖 Бот под заказ", "prod:tgbot"), btn("⚙️ AI-автоматизация", "prod:ai-int")],
            [btn("🛍 Магазин под ключ", "prod:autoshop"), btn("🧩 Orbit CRM", "prod:orbit-crm")],
            [btn("🏠 Меню", "act:menu")],
        ]))


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
        txt = ("🚀 <b>Мои проекты</b> — открываются прямо сейчас:\n\n"
               "👨‍💻 <b>GitHub</b> — код магазина открыт.\n"
               "🎙 <b>AI Call Center</b> — витрина голосового оператора.\n"
               "🧩 <b>Orbit CRM</b> — канбан, сделки, KPI, AI-ассистент.\n"
               "🏪 <b>Магазин</b> — каталог с обложками и корзиной.")
        rows = [
            [btn("👨‍💻 Открыть GitHub (код)", url="https://github.com/nwl01234/portfolio-app")],
            [btn("🎨 Витрина AI Call Center", url=f"{site_base()}/tts.html")],
            [btn("🏪 Открыть магазин", url=site_base())],
        ]
        if demo_url("orbit-crm").startswith("http"):
            rows.append([btn("▶️ Живое демо Orbit CRM", url=demo_url("orbit-crm"))])
        else:
            rows.append([btn("🧩 CRM — запросить живой показ", "demo:orbit-crm")])
        rows.append([btn("🏠 Меню", "act:menu")])
        await m.edit_text(txt, reply_markup=kb(rows))
        return await A()

    if d == "act:cart":
        await A()
        return await cart_view(m, cq.from_user.id)

    if d == "act:help":
        await m.edit_text(
            "Я продумал вопросы через базу знаний — отвечаю по делу, без запудривания.\n\n"
            "Спроси: «сколько стоит CRM», «какие сроки», «как проходит оплата», "
            "или опиши свою задачу — выведу на решение.",
            reply_markup=menu_kb())
        return await A()

    if d == "act:free":
        await state.set_state(St.consult)
        await m.edit_text(
            "💬 Опиши свою задачу в свободной форме:\n\nбизнес → процесс → что болит.\n"
            "Найду решение и доведу до оплаты шаг за шагом.",
            reply_markup=kb([[btn("🏠 Меню", "act:menu")]]))
        return await A()

    if d.startswith("prod:"):
        pid = d.split(":")[1]
        try:
            await m.answer_photo(photo=product_card(pid),
                                 caption=product_text(pid),
                                 reply_markup=product_kb(pid))
        except Exception:
            # обложка недоступна → текстовая карточка
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
                       "Задайте вопрос или опишите задачу — отвечу по делу и доведу до результата.")
        return await A()

    if d.startswith("demo:"):
        p = storage.product(d[5:])
        return await A(f"Живая ссылка на «{p['name']}» выдаётся после настройки. Пока могу посмотреть сценарий и ответить на любой вопрос по проекту.", alert=True)

    if d.startswith("pay:"):
        o = ORDERS.pop(d[4:], None)
        if not o:
            return await A("Заказ уже обработан", alert=True)
        items = o["items"]
        (name, contact, uid, uname, chat_id) = o["user"]
        for p, q in items.items():
            storage.log_sale(uid, f"{name} / {uname} / {contact}", p, q, "telegram")
        data = storage.products_doc()
        by_id = {x["id"]: x for x in data["products"]}
        for p, q in items.items():
            if p in by_id:
                by_id[p]["qty"] = max(0, by_id[p]["qty"] - q)
        data["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        mode = storage.sync_to_github(data)
        await A("Оплата подтверждена ✅")
        rows = "\n".join(f"• {storage.product(p)['name']} × {q}" for p, q in items.items())
        await m.edit_text(
            f"✅ <b>Оплачено!</b>\n\n{rows}\n\n"
            "🎁 Дальше:\n"
            "— доступ к материалам/демо приходит сюда;\n"
            "— журнал продажи записан, остаток на сайте уже обновился.\n\n"
            "Нужен ещё проект? 😎",
            reply_markup=kb([[btn("🚀 Проекты", "act:demos")], [btn("🏠 Меню", "act:menu")]]))
        print(f"[pay] заказ {d[4:]} · sync: {mode}")
        return

    if d.startswith("cancel:"):
        ORDERS.pop(d[7:], None)
        await m.edit_text("🚫 Заказ отменён.", reply_markup=menu_kb())
        return await A()

    await A()


async def cart_view(m: Message, uid: int):
    items = CART.setdefault(uid, {})
    if not items:
        return await m.edit_text("🛒 Корзина пуста. Заглянешь в каталог? 👇",
                                 reply_markup=kb([[btn("🛍 Каталог", "act:catalog")], [btn("🏠 Меню", "act:menu")]]))
    total = sum(storage.product(p)["price"] * q for p, q in items.items())
    lines = "\n".join(f"• {esc(storage.product(p)['name'])} × {q} — {storage.product(p)['price'] * q} ₽"
                      for p, q in items.items())
    rows = [[btn("➖", f"dec:{p}"), btn(f"{storage.product(p)['name'][:14]}", f"prod:{p}"), btn("➕", f"inc:{p}")]
            for p, q in items.items()]
    rows.append([btn(f"💳 Оплатить · {total} ₽", "cart:checkout")])
    rows.append([btn("🗑 Очистить", "cart:clr"), btn("🏠 Меню", "act:menu")])
    await m.edit_text(f"🛒 <b>Корзина</b>\n\n{lines}\n\n<b>Итого: {total} ₽</b>", reply_markup=kb(rows))


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
    """Оффлайн-консультант: любой вопрос/бриф → осмысленный ответ + шаг к сделке."""
    reply, markup = consult_reply(m.text or "")
    await state.clear()
    await m.answer(reply, reply_markup=markup)


# ---------------- natural language ----------------

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
        return await m.answer("Корзина пуста — сначала выбери товар 👇", reply_markup=menu_kb())
    if intent == "demos":
        return await m.answer(
            "🚀 Проекты: CRM, голосовой ИИ-оператор, бот и AI-автоматизация. Опишите задачу — подберу нужный.",
            reply_markup=fallback_kb())
    if intent == "help":
        return await m.answer(
            "Пишите по-русски как удобно — пойму. Примеры: «сколько стоит CRM», «есть демо?», "
            "«нужен бот для записи клиентов» 🙂", reply_markup=menu_kb())
    reply = sales.answer(text)
    if reply:
        return await m.answer(reply, reply_markup=menu_kb())
    if intent == "gibberish":
        return await m.answer(
            sales.smalltalk(text),
            reply_markup=fallback_kb())
    # любой другой текст тоже не оставим без продажного ответа
    return await m.answer(sales.smalltalk(text), reply_markup=fallback_kb())
