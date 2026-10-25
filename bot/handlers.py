"""LUMÉ — магазин косметики внутри Telegram: каталог фото-карточками, корзина,
остатки, оформление заказа. Мини-приложение магазина открывается ВНУТРИ Telegram."""
import datetime
import html as _h
import json as _json
import uuid

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo)

from . import nlu, payload as payload_codec, sales, storage
from . import config as cfg

router = Router()
CART: dict[int, dict[str, int]] = {}
ORDERS: dict[str, dict] = {}


class St(StatesGroup):
    name = State()
    address = State()


def esc(s) -> str:
    return _h.escape(str(s or ""))


def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def btn(text, data="", url="", web_app=None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data or "noop", url=url or None, web_app=web_app)


def site_base() -> str:
    return "https://" + cfg.GITHUB_REPO.replace("/", ".github.io/")


def webapp_btn() -> InlineKeyboardButton:
    """Кнопка, открывающая магазин ВНУТРИ Telegram (_mini app_), не в браузере."""
    return btn("🏪 Магазин · внутри Telegram", web_app=WebAppInfo(url=site_base() + "/"))


def reply_kb() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура с кнопкой-приложением. Запуск магазина ИМЕННО отсюда
    разрешает WebApp.sendData(): корзина уходит боту одним тапом из мини-аппа.
    (Из меню-кнопки и inline-кнопок Telegram sendData не разрешает — там
    срабатывает fallback с deep-link.)"""
    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(text="🛍 Открыть магазин", web_app=WebAppInfo(url=site_base() + "/")),
            KeyboardButton(text="💬 Консультант"),
        ]],
        resize_keyboard=True, is_persistent=True,
        input_field_placeholder="Спросить консультанта…")


def stock_of(pid: str) -> int:
    p = storage.product(pid)
    return int(p.get("qty", 0)) if p else 0


def price_str(p: dict) -> str:
    return f"{p['price']:,} ₽".replace(",", " ")


def total_of(items: dict) -> int:
    return sum((storage.product(p) or {}).get("price", 0) * q for p, q in items.items())


def menu_kb(n: int = 0) -> InlineKeyboardMarkup:
    return kb([
        [btn("🛍 Каталог", "act:catalog"), webapp_btn()],
        [btn(f"🛒 Корзина{' · ' + str(n) if n else ''}", "act:cart"),
         btn("🚚 Доставка", "act:delivery")],
        [btn("💬 Помощь", "act:help")],
    ])


def cart_kb() -> InlineKeyboardMarkup:
    return kb([[btn("🛍 Каталог", "act:catalog"), webapp_btn()]])


# ---------------- витрины ----------------

def catalog_text() -> str:
    rows = ["💧 <b>LUMÉ · каталог</b>\nВсё в наличии — количества настоящие:\n"]
    for p in storage.products():
        q = stock_of(p["id"])
        mark = "🟢" if q > 9 else ("🟠" if q > 0 else "🔴")
        rows.append(f"{p['emoji']} <b>{esc(p['name'])}</b> — {price_str(p)} · {mark} {q} шт")
    return "\n".join(rows)


def catalog_kb() -> InlineKeyboardMarkup:
    prods = storage.products()
    rows = []
    for i in range(0, len(prods), 2):
        pair = prods[i:i + 2]
        rows.append([btn(f"{p['emoji']} {p['name'].split(' ·')[0]} — {price_str(p)}", f"prod:{p['id']}") for p in pair])
    rows.append([webapp_btn()])
    rows.append([btn("🛒 Корзина", "act:cart"), btn("🚚 Доставка", "act:delivery")])
    return kb(rows)


def product_caption(pid: str) -> str:
    p = storage.product(pid)
    feats = "\n".join(f" ✦ {esc(f)}" for f in p["features"])
    q = stock_of(pid)
    line = (f"🟢 В наличии: {q} шт" if q > 4 else
            (f"🟠 Мало: {q} шт" if q > 0 else "🔴 Распродано"))
    return (f"<b>{esc(p['name'])}</b>\n\n{esc(p['tagline'])}\n\n{feats}\n\n"
            f"💰 <b>{price_str(p)}</b>\n{line}")


def product_kb(pid: str) -> InlineKeyboardMarkup:
    rows = []
    if stock_of(pid) > 0:
        rows.append([btn("🛒 В корзину", f"add:{pid}"), btn("🛒 Открыть корзину", "act:cart")])
    rows.append([btn("⬅️ Каталог", "act:catalog"), webapp_btn()])
    return kb(rows)


def cover_url(pid: str) -> str:
    return f"{site_base()}/img/products/{pid}.jpg"


async def show(m: Message, text: str, markup) -> None:
    """Правильная замена витрины: работает и для текстовых, и для фото-сообщений.

    Telegram не даёт edit_text по сообщению с фото — отсюда «кнопка оборачивается
    в вечный спиннер». Логика: edit_text → edit_caption → delete + новый ответ.
    """
    try:
        await m.edit_text(text, reply_markup=markup)
        return
    except Exception:
        pass
    try:
        await m.edit_caption(caption=text, reply_markup=markup)
        return
    except Exception:
        pass
    try:
        await m.delete()
    except Exception:
        pass
    await m.answer(text, reply_markup=markup)


def cart_view_text(uid: int) -> str:
    items = CART.get(uid) or {}
    if not items:
        return None
    rows = ["🛒 <b>Корзина</b>\n"]
    for pid, q in items.items():
        p = storage.product(pid)
        s = stock_of(pid)
        warn = "" if s >= q else f"  ⚠️ на складе {s}"
        rows.append(f"• {p['name']} · {q} — {p['price'] * q} ₽{warn}")
    rows.append(f"\n<b>Итого: {total_of(items):,} ₽</b>".replace(",", " "))
    return "\n".join(rows)


def cart_view_kb(uid: int) -> InlineKeyboardMarkup:
    items = CART.get(uid) or {}
    rows = [[btn("−", f"dec:{pid}"), btn(f"{storage.product(pid)['name'].split(' ·')[0][:14]} ×{q}", f"prod:{pid}"),
             btn("＋", f"inc:{pid}")] for pid, q in items.items()]
    over = any(stock_of(pid) < q for pid, q in items.items())
    if items and not over:
        rows.append([btn(f"💳 Оформить · {total_of(items):,} ₽".replace(",", " "), "cart:checkout")])
    elif over:
        rows.append([btn("⚠️ Остаток изменился — поправьте корзину", "noop")])
    rows.append([btn("🗑 Очистить", "cart:clr"), btn("🛍 Каталог", "act:catalog")])
    rows.append([webapp_btn()])
    return kb(rows)


# ---------------- вход ----------------

@router.message(CommandStart())
async def start(m: Message, state: FSMContext, command: CommandObject | None = None):
    await state.clear()
    payload = (command.args if command else "") or ""
    if payload and await handle_payload(m, payload, state):
        return
    n = sum((CART.get(m.from_user.id) or {}).values())
    # постоянная клавиатура: запуск магазина отсюда разрешает sendData (корзина
    # уходит боту прямо из мини-аппа, без единого лишнего шага)
    await m.answer("Магазин всегда под рукой 👇", reply_markup=reply_kb())
    await m.answer(
        f"✨ <b>LUMÉ</b> — уходовая косметика с честными остатками.\n\n"
        "Сыворотки, SPF и ретинол. Покупайте прямо здесь, в чате — "
        "или откройте витрину магазина кнопкой ниже (внутри Telegram).",
        reply_markup=menu_kb(n))


async def handle_payload(m: Message, payload: str, state: FSMContext) -> bool:
    """Корзина, переданная мини-приложением: ORD<b64>."""
    got = payload_codec.decode(payload)
    if got is None or got[0] != "order":
        return False
    valid: dict[str, int] = {}
    for pid, q in (got[1].get("items") or {}).items():
        p = storage.product(pid)
        if not p:
            continue
        valid[pid] = min(max(1, int(q)), max(stock_of(pid), 0))
    valid = {k: v for k, v in valid.items() if v}
    if not valid:
        await m.answer("К сожалению, всё из вашей корзины уже распродано. "
                       "Новое поступление попробуйте ✨", reply_markup=menu_kb())
        return True
    CART[m.from_user.id] = valid
    await m.answer(cart_view_text(m.from_user.id), reply_markup=cart_view_kb(m.from_user.id))
    return True


@router.message(F.web_app_data)
async def webapp_order(m: Message, state: FSMContext):
    """Заказ из Mini App: WebApp.sendData() присылает сюда JSON корзины.
    Покупатель жмёт «Оформить» в магазине — и дальше всё происходит здесь, в чате."""
    try:
        data = _json.loads(m.web_app_data.data or "{}")
    except Exception:
        data = {}
    items = data.get("items") if isinstance(data.get("items"), dict) else {}
    valid: dict[str, int] = {}
    for pid, q in items.items():
        p = storage.product(str(pid).lower())
        if not p:
            continue
        try:
            q = int(q)
        except (TypeError, ValueError):
            continue
        q = min(max(1, q), max(stock_of(p["id"]), 0))
        if q > 0:
            valid[p["id"]] = q
    if not valid:
        await m.answer("Из корзины ничего не пришло — всё уже законтрактовано? "
                       "Откройте каталог и соберите заново 👇", reply_markup=menu_kb())
        return
    CART[m.from_user.id] = valid
    total = total_of(valid)
    lines = "\n".join(f"• {storage.product(p)['name']} × {q}" for p, q in valid.items())
    await m.answer(
        f"🧾 <b>Заказ из магазина</b>\n{lines}\n<b>Итого: {total:,} ₽</b>\n\n".replace(",", " ") +
        "Осталось два шага — куда доставлять?")
    await state.set_state(St.name)
    await m.answer("1/2 — Имя получателя:")


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
        await show(m, "Главное:", menu_kb())
        return await A()

    if d == "act:catalog":
        await show(m, catalog_text(), catalog_kb())
        return await A()

    if d == "act:delivery":
        await show(
            m,
            "🚚 <b>Доставка и возврат</b>\n\n"
            "• СДЭК / Боксберри — 24–48 часов после подтверждения, 300 ₽\n"
            "• Бесплатно от 3 500 ₽\n"
            "• Возврат 14 дней: не подошло — вернём 100%\n"
            "• Оплата: реквизиты приходят в чате после подтверждения заказа",
            reply_markup=kb([[btn("🛍 Каталог", "act:catalog"), btn("💬 Помощь", "act:help")]]))
        return await A()

    if d == "act:help":
        await show(
            m,
            "Спросите по-простому: «что от постакне», «сколько осталось сыворотки», "
            "«как наносить ретинол» — отвечу.\n\nИли откройте витрину:",
            reply_markup=kb([[webapp_btn()], [btn("🛍 Каталог", "act:catalog"), btn("🏠 Меню", "act:menu")]]))
        return await A()

    if d.startswith("prod:"):
        pid = d[5:]
        p = storage.product(pid)
        if not p:
            return await A("Товар не найден", alert=True)
        media_tried = False
        try:
            await m.answer_photo(photo=cover_url(pid), caption=product_caption(pid), reply_markup=product_kb(pid))
            media_tried = True
        except Exception:
            pass
        if not media_tried:
            await show(m, product_caption(pid), product_kb(pid))
        return await A()

    if d.startswith("add:"):
        pid = d[4:]
        if stock_of(pid) <= 0:
            return await A("🕐 Распродано. Поставим в известность, когда появится", alert=True)
        c = CART.setdefault(cq.from_user.id, {})
        if c.get(pid, 0) + 1 > stock_of(pid):
            return await A(f"На складе всего {stock_of(pid)} шт — добавлен максимум", alert=True)
        c[pid] = c.get(pid, 0) + 1
        await A(f"➕ {storage.product(pid)['name'].split(' ·')[0]} · в корзине {c[pid]}")
        return await cart_view(m, cq.from_user.id)

    if d.startswith("inc:"):
        pid = d[4:]
        c = CART.setdefault(cq.from_user.id, {})
        if (c.get(pid, 0) + 1) > stock_of(pid):
            return await A(f"Столько нет на складе: осталось {stock_of(pid)} шт", alert=True)
        c[pid] = c.get(pid, 0) + 1
        await A()
        return await cart_view(m, cq.from_user.id)

    if d.startswith("dec:"):
        pid = d[4:]
        c = CART.setdefault(cq.from_user.id, {})
        c[pid] = max(0, c.get(pid, 0) - 1)
        if not c[pid]:
            c.pop(pid)
        await A()
        return await cart_view(m, cq.from_user.id)

    if d == "cart:clr":
        CART[cq.from_user.id] = {}
        await A("🗑 Корзина очищена")
        return await cart_view(m, cq.from_user.id)

    if d == "act:cart":
        await A()
        return await cart_view(m, cq.from_user.id)

    if d.startswith("pay:"):
        o = ORDERS.pop(d[4:], None)
        if not o:
            return await A("Заказ уже обработан", alert=True)
        pid_q: dict[str, int] = o["items"]
        validated = {}
        for pid, q in pid_q.items():
            s = stock_of(pid)
            if s >= q:  # остатки проверяются ПЕРЕД списанием — fullstack честность
                validated[pid] = q
        if not validated:
            await state.clear()
            await show(m, "🕐 Пока оформляли — всё распродано. Сожалеем!", menu_kb())
            return await A()
        uid, (name, address) = o["uid"], o["user"]
        for pid, q in validated.items():
            storage.log_sale(uid, f"{name} · {address}", pid, q, "telegram")
        data = storage.products_doc()
        by_id = {x["id"]: x for x in data["products"]}
        for pid, q in validated.items():
            if pid in by_id:
                by_id[pid]["qty"] = max(0, by_id[pid]["qty"] - q)
        data["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        mode = storage.sync_to_github(data)
        CART[uid] = {}
        await state.clear()
        sold = "\n".join(f"• {storage.product(p)['name']} × {q}" for p, q in validated.items())
        total = total_of(validated)
        await show(
            m,
            f"✅ <b>Оплачено · заказ собирается</b>\n\n{sold}\n\n"
            f"<b>Сумма: {total:,} ₽</b>\n\n".replace(",", " ") +
            "📦 Отправка СДЭК 24–48 ч · трек-номер придёт сюда же.\n"
            "Остатки на витрине уже обновились. С возвращением 💫",
            kb([[webapp_btn()], [btn("🛍 Каталог", "act:catalog")]]))
        print(f"[pay] заказ {d[4:]} · sync: {mode} · позиций: {len(validated)}")
        return

    await A()


async def cart_view(m: Message, uid: int):
    text = cart_view_text(uid)
    if not text:
        await show(m, "🛒 Корзина пуста.", cart_kb())
        return
    await show(m, text, cart_view_kb(uid))


@router.callback_query(F.data == "cart:checkout")
async def checkout(cq: CallbackQuery, state: FSMContext):
    items = CART.get(cq.from_user.id) or {}
    if not items:
        return await cq.answer("Корзина пуста", show_alert=True)
    if any(stock_of(pid) < q for pid, q in items.items()):
        return await cq.answer("Остатки изменились — пересоберите корзину", show_alert=True)
    await state.set_state(St.name)
    await cq.message.answer("📦 Куда доставлять?\n1/2 — Имя получателя:")
    await cq.answer()


# ---------------- FSM оформления ----------------

@router.message(St.name)
async def order_name(m: Message, state: FSMContext):
    if len(m.text or "") < 2:
        return await m.answer("Напишите имя 🙏")
    await state.update_data(name=m.text[:60])
    await state.set_state(St.address)
    await m.answer("2/2 — Город, пункт СДЭК или адрес:")


@router.message(St.address)
async def order_address(m: Message, state: FSMContext):
    data = await state.get_data()
    items = CART.get(m.from_user.id) or {}
    if not items:
        await state.clear()
        return await m.answer("Корзина опустела 🤷 Начнём заново?", reply_markup=menu_kb())
    oid = uuid.uuid4().hex[:8].upper()
    ORDERS[oid] = {"items": dict(items), "uid": m.from_user.id, "user": (data["name"], (m.text or "")[:80])}
    await state.clear()
    CART[m.from_user.id] = {}
    total = total_of(items)
    names = "\n".join(f"• {storage.product(p)['name']} × {q} — {storage.product(p)['price'] * q} ₽" for p, q in items.items())
    await m.answer(
        f"🧾 <b>Заказ {oid}</b>\n{names}\n<b>Итого: {total:,} ₽</b>\n\n".replace(",", " ") +
        f"👤 {esc(data['name'])} · {esc(m.text[:80])}\n\nОплата 👇",
        reply_markup=kb([
            [btn(f"💳 Оплатить · {total:,} ₽".replace(",", " "), f"pay:{oid}")],
            [btn("✖️ Отменить", f"cancel:{oid}")]]))


@router.callback_query(F.data.startswith("cancel:"))
async def order_cancel(cq: CallbackQuery, state: FSMContext):
    ORDERS.pop(cq.data[7:], None)
    await state.clear()
    await show(cq.message, "🚫 Заказ отменён.", menu_kb())
    await cq.answer()


# ---------------- живой диалог ----------------

@router.message()
async def nlu_router(m: Message, state: FSMContext):
    text = m.text or ""
    intent, pid = nlu.guess(text)
    if intent == "cart":
        return await cart_view(m, m.from_user.id)
    if intent == "catalog":
        return await m.answer(catalog_text(), reply_markup=catalog_kb())
    if intent == "checkout":
        items = CART.get(m.from_user.id) or {}
        if items and all(stock_of(p) >= q for p, q in items.items()):
            await state.set_state(St.name)
            return await m.answer("📦 Куда доставлять?\n1/2 — Имя получателя:")
        return await m.answer(
            "Сначала соберите корзину 👇",
            reply_markup=kb([[btn("🛍 Каталог", "act:catalog"), btn("🛒 Корзина", "act:cart")]]))
    body = sales.answer(text)
    if body:
        mark = menu_kb(sum(CART.get(m.from_user.id, {}).values())) if intent != "help" else kb([
            [btn("🛍 Каталог", "act:catalog"), webapp_btn()]])
        return await m.answer(body, reply_markup=mark)
    return await m.answer(sales.smalltalk(), reply_markup=kb([
        [btn("🛍 Каталог", "act:catalog"), webapp_btn()],
        [btn("💬 Опишу запрос", "act:help")]]))
