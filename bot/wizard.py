"""Мастер оформления заказа: спрашивает только незаполненные шаги черновика."""
import asyncio
import html
import logging
import math
from dataclasses import dataclass

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup)

from . import draft as dr
from .catalog import DELIVERY_MODES, POINT_TYPE, SERVICES, SIDE_EMOJI, TARIFFS
from .cdek import CdekClient, CdekError
from .config import Config
from .db import Database

e = html.escape
PAGE = 8


class OrderSG(StatesGroup):
    order_type = State()
    number = State()
    from_city = State()
    to_city = State()
    packages = State()
    items = State()
    tariff = State()
    tariff_code = State()
    from_point = State()
    to_point = State()
    declared_value = State()
    cod = State()
    cod_amount = State()
    services = State()
    sender = State()
    recipient = State()
    comment = State()
    summary = State()


def ikb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=c) for t, c in row] for row in rows if row])


NAV = [("↩️ Назад", "wz:back"), ("✖️ Отмена", "wz:cancel")]


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📦 Новый заказ"), KeyboardButton(text="📋 Шаблоны")],
        [KeyboardButton(text="🗂 Мои заказы"), KeyboardButton(text="🔎 Отследить")],
    ], resize_keyboard=True)


def location_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📍 Ближайшие ко мне", request_location=True)],
        [KeyboardButton(text="📦 Новый заказ"), KeyboardButton(text="📋 Шаблоны")],
    ], resize_keyboard=True)


@dataclass
class Ctx:
    bot: Bot
    chat_id: int
    user_id: int
    state: FSMContext
    cdek: CdekClient
    db: Database
    config: Config

    async def send(self, text: str, markup=None) -> Message:
        return await self.bot.send_message(self.chat_id, text, reply_markup=markup)

    async def draft(self) -> dict:
        return dict((await self.state.get_data()).get("draft") or {})

    async def save(self, d: dict) -> None:
        await self.state.update_data(draft=dr.normalize(d))

    @property
    def tpl_owner(self) -> int:
        return 0 if self.config.shared_templates else self.user_id


def make_ctx(event: Message | CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config) -> Ctx:
    msg = event.message if isinstance(event, CallbackQuery) else event
    return Ctx(event.bot, msg.chat.id, event.from_user.id, state, cdek, db, config)


# ---------------------------------------------------------------- списки с пагинацией
LIST_EXTRAS: dict[str, list[list[tuple[str, str]]]] = {
    "tf": [[("⌨️ Ввести код тарифа", "tf:manual")]],
    "fp": [[("🔄 Все пункты города", "pz:all:from")]],
    "tp": [[("🔄 Все пункты города", "pz:all:to")]],
}


def render_list(kind: str, items: list[dict], page: int, title: str) -> tuple[str, InlineKeyboardMarkup]:
    pages = max(1, math.ceil(len(items) / PAGE))
    page = max(0, min(page, pages - 1))
    chunk = list(enumerate(items))[page * PAGE:(page + 1) * PAGE]
    lines = [title, f"<i>Найдено: {len(items)}</i>"]
    rows: list[list[tuple[str, str]]] = []
    for i, it in chunk:
        if it.get("detail"):
            lines.append(f"\n<b>{i + 1}.</b> {it['detail']}")
            rows.append([(f"{i + 1}. {it['label']}"[:60], f"pk:{kind}:{i}")])
        else:
            rows.append([(it["label"][:60], f"pk:{kind}:{i}")])
    nav = []
    if page > 0:
        nav.append(("◀️", f"pg:{kind}:{page - 1}"))
    if pages > 1:
        nav.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(("▶️", f"pg:{kind}:{page + 1}"))
    rows.append(nav)
    rows += LIST_EXTRAS.get(kind, [])
    rows.append(NAV)
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "…"
    return text, ikb(rows)


async def send_list(c: Ctx, kind: str, items: list[dict], title: str, edit: Message | None = None) -> None:
    await c.state.update_data(**{f"L_{kind}": items, f"T_{kind}": title})
    text, markup = render_list(kind, items, 0, title)
    if edit:
        await edit.edit_text(text, reply_markup=markup)
    else:
        await c.send(text, markup)


# ---------------------------------------------------------------- шаги
async def ask_order_type(c: Ctx, d: dict) -> None:
    await c.state.set_state(OrderSG.order_type)
    await c.send("<b>Тип заказа</b>\n\n🛒 <b>Интернет-магазин</b> — с товарами, наложенным платежом, номером заказа ИМ.\n"
                 "📦 <b>Доставка</b> — обычное отправление (документы, груз) по договору доставки.",
                 ikb([[("🛒 Интернет-магазин", "ot:1")], [("📦 Доставка", "ot:2")], NAV]))


async def ask_number(c: Ctx, d: dict) -> None:
    await c.state.set_state(OrderSG.number)
    await c.send("🔢 Введите <b>номер заказа</b> в вашей системе (уникальный для ИМ):",
                 ikb([[("🎲 Сгенерировать", "num:gen")], NAV]))


async def ask_city(c: Ctx, side: str) -> None:
    await c.state.set_state(OrderSG.from_city if side == "from" else OrderSG.to_city)
    what = "отправления" if side == "from" else "получения"
    await c.send(f"🏙 Введите <b>город {what}</b> (можно начало названия):", ikb([NAV]))


async def ask_packages(c: Ctx, d: dict) -> None:
    await c.state.set_state(OrderSG.packages)
    extra = "\nДля «Доставки» в конце строки можно указать описание вложения." if d.get("order_type") == 2 else \
        "\nПосле этого попрошу список товаров для каждого места."
    await c.send(
        "📦 <b>Грузовые места</b>\nКаждое место — отдельной строкой:\n"
        "<code>вес_кг ДxШxВ_см [кол-во] [описание]</code>\n\nПримеры:\n"
        "<code>1.5 30x20x10</code>\n<code>0.4 20x15x5 3 Документы</code>" + extra
        + "\n\n💡 Коробки СДЭК можно добавить на шаге «Упаковка».",
        ikb([NAV]))


async def ask_items(c: Ctx, pkgs: list[dict], idx: int) -> None:
    await c.state.set_state(OrderSG.items)
    await c.state.update_data(pkg_tmp=pkgs, pkg_idx=idx)
    p = pkgs[idx]
    await c.send(
        f"🧾 <b>Товары в месте {idx + 1} из {len(pkgs)}</b> ({dr.fmt_kg(p['weight'])} кг, "
        f"{p['length']}×{p['width']}×{p['height']} см)\nКаждый товар строкой:\n"
        "<code>Название; цена ₽; вес кг; [кол-во]; [артикул]</code>\n\nПример:\n"
        "<code>Футболка белая; 1490; 0.25; 2; TSH-01</code>\n<code>Кружка; 590; 0.4</code>",
        ikb([NAV]))


async def calc_missing_tariffs(c: Ctx, d: dict, known: set) -> list[dict]:
    """tarifflist возвращает не все тарифы договора — досчитываем известные коды по одному."""
    base = dr.build_tarifflist(d)
    sem = asyncio.Semaphore(6)

    async def one(code: int):
        async with sem:
            try:
                r = await c.cdek.calc_tariff({**base, "tariff_code": code})
            except CdekError as ex:
                logging.getLogger(__name__).debug("tariff %s unavailable: %s", code, ex)
                return None
        if r.get("delivery_sum") is None:
            return None
        name, mode = TARIFFS[code]
        return {"tariff_code": code, "tariff_name": name, "delivery_mode": mode, "delivery_sum": r["delivery_sum"],
                "period_min": r.get("period_min"), "period_max": r.get("period_max")}

    res = await asyncio.gather(*(one(code) for code in TARIFFS if code not in known))
    return [r for r in res if r]


async def ask_tariff(c: Ctx, d: dict) -> None:
    await c.state.set_state(OrderSG.tariff)
    await c.bot.send_chat_action(c.chat_id, "typing")
    try:
        tariffs = await c.cdek.tariff_list(dr.build_tarifflist(d))
    except CdekError as ex:
        await c.send(f"⚠️ Не удалось получить список тарифов: {e(str(ex))}",
                     ikb([[("⌨️ Ввести код тарифа", "tf:manual")], [("🔁 Повторить", "tf:retry")], NAV]))
        return
    tariffs = list(tariffs) + await calc_missing_tariffs(c, d, {t.get("tariff_code") for t in tariffs})
    # при явной смене тарифа из сводки показываем все; несовместимые пункты потом сбросятся
    any_mode = (await c.state.get_data()).get("tf_any")
    items = []
    for t in tariffs:
        mode = t.get("delivery_mode")
        if mode not in DELIVERY_MODES or (not any_mode and not dr.point_matches_tariff_mode(d, mode)):
            continue
        fs, ts = DELIVERY_MODES[mode]
        days = f"{t.get('period_min')}–{t.get('period_max')} дн"
        items.append({
            "code": t["tariff_code"], "name": t.get("tariff_name") or str(t["tariff_code"]), "mode": mode,
            "label": f"{SIDE_EMOJI[fs]}→{SIDE_EMOJI[ts]} {dr.fmt_money(t.get('delivery_sum'))} · {days} · {t.get('tariff_name')}",
            "sum": t.get("delivery_sum") or 0,
        })
    items.sort(key=lambda x: (x["mode"], x["sum"]))
    if not items:
        await c.send("😕 Нет доступных тарифов для этого направления и мест.",
                     ikb([[("⌨️ Ввести код тарифа", "tf:manual")], NAV]))
        return
    hint = ""
    if not any_mode and (d.get("from_point") or d.get("to_point")):
        hint = "\n<i>Показаны тарифы, подходящие к уже выбранным пунктам.</i>"
    await send_list(c, "tf", items,
                    f"🚚 <b>Тариф</b> {e(d['from_city']['name'])} → {e(d['to_city']['name'])}\n"
                    f"🚪 дверь · 🏢 ПВЗ · 📮 постамат{hint}")


def point_item(p: dict) -> dict:
    loc = p.get("location") or {}
    addr = loc.get("address") or loc.get("address_full") or ""
    metro = p.get("nearest_metro_station") or ""
    detail = f"<code>{e(p['code'])}</code> {e(addr)}"
    if metro:
        detail += f" (м. {e(metro)})"
    if p.get("work_time"):
        detail += f"\n🕒 {e(p['work_time'])}"
    return {
        "code": p["code"], "label": f"{p['code']} {addr}", "detail": detail, "address": addr,
        "work_time": p.get("work_time") or "", "type": p.get("type"),
        "lat": loc.get("latitude"), "lon": loc.get("longitude"),
        "search": " ".join(str(x) for x in (p["code"], p.get("name"), addr, metro, loc.get("address_full"))).lower(),
    }


async def ask_point(c: Ctx, d: dict, side: str) -> None:
    fs, ts = dr.sides(d)
    kind = fs if side == "from" else ts
    city = d[f"{side}_city"]
    await c.state.set_state(OrderSG.from_point if side == "from" else OrderSG.to_point)
    await c.state.update_data(point_side=side, point_kind=kind)
    if kind == "door":
        who = "забора груза (откуда заберёт курьер)" if side == "from" else "доставки получателю"
        await c.send(f"🏠 Введите <b>адрес {who}</b> в г. {e(city['name'])}:\n"
                     "<i>улица, дом, квартира/офис</i>", ikb([NAV]))
        return
    max_kg = math.ceil(max(p["weight"] for p in d["packages"]) / 1000) if d.get("packages") else None
    points = await c.cdek.delivery_points(
        city["code"], POINT_TYPE[kind], reception=side == "from", handout=side == "to", weight_max=max_kg)
    items = [point_item(p) for p in points]
    await c.state.update_data(**{f"pvz_{side}": items})
    what = "ПВЗ" if kind == "pvz" else "постамат"
    if not items:
        await c.send(f"😕 В г. {e(city['name'])} нет подходящих пунктов ({what}). Выберите другой тариф или город.",
                     ikb([[("✏️ Сменить тариф", "ed:tf")], [("✏️ Сменить город", "ed:fc" if side == "from" else "ed:tc")], NAV]))
        return
    await c.send(f"{SIDE_EMOJI[kind]} Выбор пункта ({what}) {'отправки' if side == 'from' else 'получения'} "
                 f"в г. {e(city['name'])}.\n\nМожно:\n• выбрать из списка;\n• написать часть адреса, метро или код пункта;\n"
                 "• отправить геолокацию — покажу ближайшие.", location_kb())
    await send_list(c, "fp" if side == "from" else "tp", items, f"<b>Пункты в г. {e(city['name'])}</b>")


async def ask_declared_value(c: Ctx, d: dict) -> None:
    await c.state.set_state(OrderSG.declared_value)
    await c.send("🛡 <b>Страховка.</b> Введите объявленную стоимость отправления, ₽ "
                 "(от неё считается страховка и ответственность СДЭК):",
                 ikb([[("Без страховки", "dv:0")], NAV]))


async def ask_cod(c: Ctx, d: dict) -> None:
    await c.state.set_state(OrderSG.cod)
    await c.send(f"💵 <b>Что оплачивает получатель?</b>\nСтоимость товаров: {dr.fmt_money(dr.items_total(d))}\n\n"
                 "За доставку можно взять фиксированную сумму или тариф СДЭК с наценкой в %.",
                 ikb([[("Ничего — всё оплачиваю я", "cod:none")],
                      [("Только товары (наложка)", "cod:items")],
                      [("Товары + доставка", "cod:all")],
                      [("Только доставка", "cod:delivery")], NAV]))


async def ask_cod_delivery(c: Ctx, mode: str) -> None:
    await c.state.set_state(OrderSG.cod_amount)
    await c.state.update_data(cod_mode=mode)
    await c.send("🚚 <b>Сколько взять с получателя за доставку?</b>\n"
                 "• наценка к тарифу СДЭК — кнопкой или текстом, например <code>15%</code>;\n"
                 "• фиксированная сумма — числом, например <code>390</code>.\n\n"
                 "<i>Наценка считается от стоимости тарифа и пересчитывается при создании заказа "
                 "(округление вверх до рубля).</i>",
                 ikb([[("По тарифу (0%)", "cdm:0"), ("+10%", "cdm:10")],
                      [("+20%", "cdm:20"), ("+30%", "cdm:30")], NAV]))


def render_services(tmp: dict, order_type: int, page: str) -> tuple[str, InlineKeyboardMarkup]:
    rows: list[list[tuple[str, str]]] = []
    for s in SERVICES:
        if s.group != page or s.only not in (None, order_type):
            continue
        if s.kind == "flag":
            rows.append([(("✅ " if s.code in tmp else "▫️ ") + s.title, f"sv:t:{s.code}")])
        else:
            n = tmp.get(s.code, 0)
            rows.append([("➖", f"sv:-:{s.code}"), (f"{s.title}: {n}" if n else s.title, "noop"), ("➕", f"sv:+:{s.code}")])
    if page == "main":
        packs = sum(v for k, v in tmp.items() if isinstance(v, int) and k not in ("GET_UP_FLOOR_BY_HAND",))
        rows.append([(f"📦 Упаковка и коробки ›{f' ({packs})' if packs else ''}", "sv:pg:pack")])
        text = "⚙️ <b>Дополнительные услуги</b>\nОтметьте нужные и нажмите «Готово»."
    else:
        rows.append([("‹ К услугам", "sv:pg:main")])
        text = "📦 <b>Упаковка СДЭК</b>\nУкажите количество коробок/материалов (➕/➖)."
    rows.append([("✅ Готово", "sv:ok"), ("🧹 Сбросить", "sv:clr")])
    rows.append(NAV)
    return text, ikb(rows)


async def ask_services(c: Ctx, d: dict) -> None:
    await c.state.set_state(OrderSG.services)
    tmp = dict(d.get("services") or {})
    await c.state.update_data(svc_tmp=tmp, svc_page="main")
    text, markup = render_services(tmp, d["order_type"], "main")
    await c.send(text, markup)


async def ask_person(c: Ctx, d: dict, who: str) -> None:
    await c.state.set_state(OrderSG.sender if who == "sender" else OrderSG.recipient)
    title = "Отправитель" if who == "sender" else "Получатель"
    rows = []
    if who == "sender" and d.get("order_type") == 1:
        rows.append([("Взять из договора", "snd:skip")])
    rows.append(NAV)
    await c.send(f"👤 <b>{title}</b>. Отправьте одним сообщением (каждое с новой строки):\n"
                 "<code>ФИО\nтелефон\n[компания]\n[email]</code>\n\nИли пришлите контакт из Telegram.",
                 ikb(rows))


async def ask_comment(c: Ctx, d: dict) -> None:
    await c.state.set_state(OrderSG.comment)
    await c.send("💬 Комментарий к заказу (необязательно):", ikb([[("Без комментария", "cm:skip")], NAV]))


ASKERS = {
    "order_type": ask_order_type,
    "number": ask_number,
    "from_city": lambda c, d: ask_city(c, "from"),
    "to_city": lambda c, d: ask_city(c, "to"),
    "packages": ask_packages,
    "tariff": ask_tariff,
    "from_point": lambda c, d: ask_point(c, d, "from"),
    "to_point": lambda c, d: ask_point(c, d, "to"),
    "declared_value": ask_declared_value,
    "cod": ask_cod,
    "services": ask_services,
    "sender": lambda c, d: ask_person(c, d, "sender"),
    "recipient": lambda c, d: ask_person(c, d, "recipient"),
    "comment": ask_comment,
}


# ---------------------------------------------------------------- движок
async def start_order(c: Ctx, base: dict | None = None, notice: str | None = None) -> None:
    await c.state.clear()
    d = dr.normalize(dict(base or {}))
    await c.state.update_data(draft=d, hist=[])
    if notice:
        await c.send(notice, main_menu())
    await advance(c)


async def advance(c: Ctx) -> None:
    d = await c.draft()
    step = dr.next_step(d)
    if step is None:
        await show_summary(c)
        return
    await ASKERS[step](c, d)


async def complete(c: Ctx, key: str, value) -> None:
    data = await c.state.get_data()
    d = dict(data.get("draft") or {})
    d[key] = value
    hist = [k for k in data.get("hist", []) if k != key] + [key]
    # служебные списки больше не нужны — не держим их в хранилище
    cleanup = {k: None for k in data if k.startswith(("L_", "T_", "pvz_")) or k in ("pkg_tmp", "svc_tmp")}
    await c.state.update_data(draft=dr.normalize(d), hist=hist, tf_any=None, **cleanup)
    await advance(c)


async def go_back(c: Ctx) -> None:
    data = await c.state.get_data()
    d = dict(data.get("draft") or {})
    hist = list(data.get("hist", []))
    while hist:
        k = hist.pop()
        if k in d:
            del d[k]
            break
    else:
        await c.send("Это первый шаг. Чтобы выйти — «✖️ Отмена».")
    await c.state.update_data(draft=dr.normalize(d), hist=hist)
    await advance(c)


async def edit_block(c: Ctx, block: str) -> None:
    d = await c.draft()
    for k in dr.EDIT_BLOCKS[block][1]:
        d.pop(k, None)
    await c.save(d)
    await c.state.update_data(tf_any=block == "tf")
    await advance(c)


def summary_kb(d: dict) -> InlineKeyboardMarkup:
    blocks = [b for b, (_, keys) in dr.EDIT_BLOCKS.items() if dr.step_applicable(keys[0], d)]
    edit_rows, row = [], []
    for b in blocks:
        row.append(("✏️ " + dr.EDIT_BLOCKS[b][0], f"ed:{b}"))
        if len(row) == 2:
            edit_rows.append(row)
            row = []
    edit_rows.append(row)
    return ikb([[("✅ Оформить заказ", "sm:create")], *edit_rows,
                [("🔄 Пересчитать", "sm:calc"), ("💾 В шаблон", "sm:tpl")], [("✖️ Отмена", "wz:cancel")]])


async def show_summary(c: Ctx) -> None:
    d = await c.draft()
    calc, err = None, None
    try:
        calc = await c.cdek.calc_tariff(dr.build_calc(d))
    except CdekError as ex:
        err = str(ex)
    await c.state.set_state(OrderSG.summary)
    await c.send(dr.summary_text(d, calc, err), summary_kb(d))


__all__ = ["OrderSG", "Ctx", "make_ctx", "ikb", "NAV", "main_menu", "render_list", "send_list", "render_services",
           "start_order", "advance", "ask_cod_delivery", "complete", "go_back", "edit_block", "show_summary", "ask_items", "ask_tariff",
           "TARIFFS"]
