"""Обработчики шагов мастера заказа."""
import html
import math
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import draft as dr
from ..catalog import TARIFFS
from ..cdek import CdekClient, CdekError
from ..config import Config
from ..db import Database
from ..wizard import (OrderSG, ask_cod_delivery, ask_items, ask_tariff, complete, edit_block, go_back, ikb, main_menu, make_ctx,
                      render_list, render_services, send_list, show_summary)

router = Router(name="order")
e = html.escape


# ---------------------------------------------------------------- навигация
@router.callback_query(F.data == "wz:back")
async def cb_back(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await cb.answer()
    await go_back(make_ctx(cb, state, cdek, db, config))


@router.callback_query(F.data.startswith("pg:"))
async def cb_page(cb: CallbackQuery, state: FSMContext):
    _, kind, page = cb.data.split(":")
    data = await state.get_data()
    items = data.get(f"L_{kind}")
    if not items:
        return await cb.answer("Список устарел", show_alert=True)
    text, markup = render_list(kind, items, int(page), data.get(f"T_{kind}", ""))
    await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()


@router.callback_query(F.data.startswith("pk:"))
async def cb_pick(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    _, kind, idx = cb.data.split(":")
    data = await state.get_data()
    items = data.get(f"L_{kind}") or []
    if int(idx) >= len(items):
        return await cb.answer("Список устарел", show_alert=True)
    it = items[int(idx)]
    c = make_ctx(cb, state, cdek, db, config)
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    if kind in ("fc", "tc"):
        side = "from" if kind == "fc" else "to"
        await cb.message.answer(f"✅ {e(it['label'])}")
        await complete(c, f"{side}_city", {"code": it["code"], "name": it["name"], "label": it["label"]})
    elif kind == "tf":
        await cb.message.answer(f"✅ Тариф: {it['code']} · {e(it['name'])}")
        await complete(c, "tariff", {"code": it["code"], "name": it["name"], "mode": it["mode"]})
    elif kind in ("fp", "tp"):
        await choose_point(c, "from" if kind == "fp" else "to", it)


# ---------------------------------------------------------------- тип / номер
@router.callback_query(OrderSG.order_type, F.data.startswith("ot:"))
async def cb_order_type(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    t = int(cb.data[3:])
    await cb.message.answer("✅ " + ("Интернет-магазин" if t == 1 else "Доставка"))
    await complete(make_ctx(cb, state, cdek, db, config), "order_type", t)


@router.callback_query(OrderSG.number, F.data == "num:gen")
async def cb_number_gen(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await cb.answer()
    num = f"TG{cb.from_user.id % 100000}-{int(time.time())}"
    await cb.message.edit_text(f"🔢 Номер заказа: <code>{num}</code>")
    await complete(make_ctx(cb, state, cdek, db, config), "number", num)


@router.message(OrderSG.number, F.text)
async def msg_number(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    num = m.text.strip()
    if not 1 <= len(num) <= 40:
        return await m.answer("Номер должен быть от 1 до 40 символов")
    await complete(make_ctx(m, state, cdek, db, config), "number", num)


# ---------------------------------------------------------------- города
@router.message(OrderSG.from_city, F.text)
@router.message(OrderSG.to_city, F.text)
async def msg_city(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    side = "from" if await state.get_state() == OrderSG.from_city.state else "to"
    q = m.text.strip()
    if len(q) < 2:
        return await m.answer("Введите хотя бы 2 буквы")
    cities = await cdek.find_cities(q)
    c = make_ctx(m, state, cdek, db, config)
    if not cities:
        return await m.answer("Ничего не найдено, попробуйте иначе")
    if len(cities) == 1:
        it = cities[0]
        await m.answer(f"✅ {e(it['label'])}")
        return await complete(c, f"{side}_city", it)
    await send_list(c, "fc" if side == "from" else "tc", cities,
                    "🏙 Выберите город " + ("отправления" if side == "from" else "получения"))


# ---------------------------------------------------------------- места и товары
@router.message(OrderSG.packages, F.text)
async def msg_packages(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    c = make_ctx(m, state, cdek, db, config)
    d = await c.draft()
    try:
        pkgs = dr.parse_packages(m.text)
    except ValueError as ex:
        return await m.answer(f"⚠️ {e(str(ex))}")
    total = sum(p["weight"] for p in pkgs)
    await m.answer(f"✅ Мест: {len(pkgs)}, общий вес {dr.fmt_kg(total)} кг")
    if d.get("order_type") == 1:
        return await ask_items(c, pkgs, 0)
    await complete(c, "packages", pkgs)


@router.message(OrderSG.items, F.text)
async def msg_items(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    c = make_ctx(m, state, cdek, db, config)
    data = await state.get_data()
    pkgs, idx = data.get("pkg_tmp"), data.get("pkg_idx", 0)
    if not pkgs:
        return await go_back(c)
    try:
        items = dr.parse_items(m.text)
    except ValueError as ex:
        return await m.answer(f"⚠️ {e(str(ex))}")
    pkgs[idx]["items"] = items
    total = sum(i["cost"] * i["amount"] for i in items)
    await m.answer(f"✅ Место {idx + 1}: товаров {len(items)} на {dr.fmt_money(total)}")
    if idx + 1 < len(pkgs):
        return await ask_items(c, pkgs, idx + 1)
    await complete(c, "packages", pkgs)


# ---------------------------------------------------------------- тариф
@router.callback_query(F.data == "tf:retry")
async def cb_tariff_retry(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await cb.answer()
    c = make_ctx(cb, state, cdek, db, config)
    await ask_tariff(c, await c.draft())


@router.callback_query(F.data == "tf:manual")
async def cb_tariff_manual(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(OrderSG.tariff_code)
    known = ", ".join(str(k) for k in sorted(TARIFFS))
    await cb.message.answer(f"⌨️ Введите код тарифа СДЭК.\nИзвестные коды: <code>{known}</code>", reply_markup=ikb([[
        ("↩️ К списку", "tf:retry")]]))


@router.message(OrderSG.tariff_code, F.text)
async def msg_tariff_code(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    c = make_ctx(m, state, cdek, db, config)
    if not m.text.strip().isdigit() or int(m.text) not in TARIFFS:
        return await m.answer("Неизвестный код тарифа. Добавьте его в bot/catalog.py (TARIFFS) или выберите из списка.")
    code = int(m.text)
    name, mode = TARIFFS[code]
    d = await c.draft()
    tariff = {"code": code, "name": name, "mode": mode}
    try:
        calc = await cdek.calc_tariff(dr.build_calc({**d, "tariff": tariff, "services": {}, "from_point": None,
                                                     "to_point": None}))
        await m.answer(f"✅ Тариф {code} · {e(name)}: {dr.fmt_money(calc.get('delivery_sum'))}, "
                       f"{calc.get('period_min')}–{calc.get('period_max')} дн.")
    except CdekError as ex:
        return await m.answer(f"⚠️ Тариф недоступен для направления: {e(str(ex))}")
    await complete(c, "tariff", tariff)


# ---------------------------------------------------------------- пункты / адреса
async def choose_point(c, side: str, it: dict) -> None:
    d = await c.draft()
    city = d[f"{side}_city"]
    kind = "postamat" if it.get("type") == "POSTAMAT" else "pvz"
    point = {"kind": kind, "code": it["code"], "address": it["address"], "work_time": it.get("work_time", ""),
             "city_code": city["code"], "city": city}
    await c.send(f"✅ Пункт {'отправки' if side == 'from' else 'получения'}: <code>{e(it['code'])}</code> "
                 f"{e(it['address'])}", main_menu())
    await complete(c, f"{side}_point", point)


def _haversine(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@router.callback_query(F.data.startswith("pz:all:"))
async def cb_points_all(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    side = cb.data.split(":")[2]
    data = await state.get_data()
    items = data.get(f"pvz_{side}") or []
    await cb.answer()
    await send_list(make_ctx(cb, state, cdek, db, config), "fp" if side == "from" else "tp", items,
                    "<b>Все пункты города</b>", edit=cb.message)


@router.message(OrderSG.from_point, F.location)
@router.message(OrderSG.to_point, F.location)
async def msg_point_location(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    data = await state.get_data()
    side = data.get("point_side")
    if data.get("point_kind") == "door":
        return await m.answer("Для доставки до двери введите адрес текстом.")
    items = [dict(it) for it in data.get(f"pvz_{side}") or [] if it.get("lat") is not None]
    lat, lon = m.location.latitude, m.location.longitude
    for it in items:
        it["dist"] = _haversine(lat, lon, it["lat"], it["lon"])
    items.sort(key=lambda x: x["dist"])
    items = items[:24]
    for it in items:
        dist = it["dist"]
        it["detail"] = f"<b>{dist:.1f} км</b> · " + it["detail"] if dist >= 1 else f"<b>{dist * 1000:.0f} м</b> · " + it["detail"]
    await send_list(make_ctx(m, state, cdek, db, config), "fp" if side == "from" else "tp", items,
                    "📍 <b>Ближайшие пункты</b>")


@router.message(OrderSG.from_point, F.text)
@router.message(OrderSG.to_point, F.text)
async def msg_point_text(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    c = make_ctx(m, state, cdek, db, config)
    data = await state.get_data()
    side, kind = data.get("point_side"), data.get("point_kind")
    if kind == "door":
        addr = m.text.strip()
        if len(addr) < 5:
            return await m.answer("Слишком короткий адрес")
        d = await c.draft()
        city = d[f"{side}_city"]
        return await complete(c, f"{side}_point", {"kind": "door", "address": addr[:255],
                                                    "city_code": city["code"], "city": city})
    all_items = data.get(f"pvz_{side}") or []
    q = m.text.strip().lower()
    exact = [it for it in all_items if it["code"].lower() == q]
    if exact:
        return await choose_point(c, side, exact[0])
    words = q.split()
    found = [it for it in all_items if all(w in it["search"] for w in words)]
    if not found:
        return await m.answer("Ничего не найдено. Попробуйте другую часть адреса или отправьте геолокацию.")
    await send_list(c, "fp" if side == "from" else "tp", found, f"🔎 <b>Поиск:</b> {e(m.text.strip())}")


# ---------------------------------------------------------------- страховка / наложка
@router.callback_query(OrderSG.declared_value, F.data == "dv:0")
async def cb_no_insurance(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await cb.answer()
    await cb.message.edit_text("🛡 Без страховки")
    await complete(make_ctx(cb, state, cdek, db, config), "declared_value", 0)


@router.message(OrderSG.declared_value, F.text)
async def msg_declared(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    try:
        v = dr.parse_money(m.text)
    except ValueError:
        return await m.answer("Введите сумму числом, например 5000")
    await complete(make_ctx(m, state, cdek, db, config), "declared_value", v)


@router.callback_query(OrderSG.cod, F.data.startswith("cod:"))
async def cb_cod(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await cb.answer()
    mode = cb.data[4:]
    await cb.message.edit_reply_markup(reply_markup=None)
    c = make_ctx(cb, state, cdek, db, config)
    if mode in dr.COD_PAYS_DELIVERY:
        return await ask_cod_delivery(c, mode)
    await cb.message.answer("✅ Получатель платит: " + dr.cod_str({"mode": mode}))
    await complete(c, "cod", {"mode": mode})


async def _finish_cod(c, mode: str, markup: float | None = None, fixed: float | None = None):
    cod = {"mode": mode, "markup": markup} if markup is not None else {"mode": mode, "delivery": fixed}
    await c.send("✅ Получатель платит: " + dr.cod_str(cod))
    await complete(c, "cod", cod)


@router.callback_query(OrderSG.cod_amount, F.data.startswith("cdm:"))
async def cb_cod_markup(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    mode = (await state.get_data()).get("cod_mode", "all")
    await _finish_cod(make_ctx(cb, state, cdek, db, config), mode, markup=float(cb.data[4:]))


@router.message(OrderSG.cod_amount, F.text)
async def msg_cod_amount(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    mode = (await state.get_data()).get("cod_mode", "all")
    c = make_ctx(m, state, cdek, db, config)
    try:
        v = dr.parse_money(m.text)
    except ValueError:
        return await m.answer("Введите сумму (<code>390</code>) или наценку (<code>15%</code>)")
    if "%" in m.text:
        if v > 500:
            return await m.answer("Слишком большая наценка")
        return await _finish_cod(c, mode, markup=v)
    await _finish_cod(c, mode, fixed=v)


# ---------------------------------------------------------------- доп. услуги / упаковка
@router.callback_query(OrderSG.services, F.data.startswith("sv:"))
async def cb_services(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    parts = cb.data.split(":")
    action = parts[1]
    data = await state.get_data()
    tmp: dict = dict(data.get("svc_tmp") or {})
    page = data.get("svc_page", "main")
    c = make_ctx(cb, state, cdek, db, config)
    if action == "ok":
        await cb.answer()
        await cb.message.edit_text("⚙️ Услуги: " + dr.services_str(tmp))
        return await complete(c, "services", tmp)
    if action == "t":
        code = parts[2]
        if code in tmp:
            tmp.pop(code)
        else:
            tmp[code] = True
    elif action in ("+", "-"):
        code = parts[2]
        n = tmp.get(code, 0) + (1 if action == "+" else -1)
        if n > 0:
            tmp[code] = min(n, 99)
        else:
            tmp.pop(code, None)
    elif action == "pg":
        page = parts[2]
    elif action == "clr":
        tmp = {}
    await state.update_data(svc_tmp=tmp, svc_page=page)
    d = await c.draft()
    text, markup = render_services(tmp, d["order_type"], page)
    await cb.answer()
    await cb.message.edit_text(text, reply_markup=markup)


# ---------------------------------------------------------------- отправитель / получатель
@router.callback_query(OrderSG.sender, F.data == "snd:skip")
async def cb_sender_skip(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await cb.answer()
    await cb.message.edit_text("👤 Отправитель: из договора")
    await complete(make_ctx(cb, state, cdek, db, config), "sender", {})


@router.message(OrderSG.sender, F.contact)
@router.message(OrderSG.recipient, F.contact)
async def msg_person_contact(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    key = "sender" if await state.get_state() == OrderSG.sender.state else "recipient"
    ct = m.contact
    name = " ".join(x for x in (ct.last_name, ct.first_name) if x)
    try:
        person = {"name": name, "phone": dr.normalize_phone(ct.phone_number)}
    except ValueError as ex:
        return await m.answer(str(ex))
    await m.answer(f"✅ {dr.person_str(person)}\n<i>Чтобы указать компанию/email — отправьте данные текстом.</i>")
    await complete(make_ctx(m, state, cdek, db, config), key, person)


@router.message(OrderSG.sender, F.text)
@router.message(OrderSG.recipient, F.text)
async def msg_person(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    key = "sender" if await state.get_state() == OrderSG.sender.state else "recipient"
    try:
        person = dr.parse_person(m.text)
    except ValueError as ex:
        return await m.answer(f"⚠️ {e(str(ex))}. Пример:\n<code>Иванов Иван\n+79991234567</code>")
    await m.answer(f"✅ {dr.person_str(person)}")
    await complete(make_ctx(m, state, cdek, db, config), key, person)


# ---------------------------------------------------------------- комментарий
@router.callback_query(OrderSG.comment, F.data == "cm:skip")
async def cb_comment_skip(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    await complete(make_ctx(cb, state, cdek, db, config), "comment", "")


@router.message(OrderSG.comment, F.text)
async def msg_comment(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await complete(make_ctx(m, state, cdek, db, config), "comment", m.text.strip()[:255])


# ---------------------------------------------------------------- сводка
@router.callback_query(F.data.startswith("ed:"))
async def cb_edit(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    block = cb.data[3:]
    if block not in dr.EDIT_BLOCKS:
        return await cb.answer()
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=None)
    await edit_block(make_ctx(cb, state, cdek, db, config), block)


@router.callback_query(OrderSG.summary, F.data == "sm:calc")
async def cb_recalc(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await cb.answer("Пересчитываю…")
    await cb.message.edit_reply_markup(reply_markup=None)
    await show_summary(make_ctx(cb, state, cdek, db, config))


@router.callback_query(OrderSG.summary, F.data == "sm:create")
async def cb_create(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    c = make_ctx(cb, state, cdek, db, config)
    d = await c.draft()
    if dr.next_step(d):
        await cb.answer("Заказ заполнен не полностью", show_alert=True)
        return await show_summary(c)
    await cb.answer("Отправляю в СДЭК…")
    await cb.message.edit_reply_markup(reply_markup=None)
    wait = await cb.message.answer("⏳ Создаю заказ в СДЭК…")
    try:
        calc = await cdek.calc_tariff(dr.build_calc(d)) if (d.get("cod") or {}).get("markup") is not None else None
        res = await cdek.create_order(dr.build_order(d, calc))
        uuid = res["entity"]["uuid"]
        order = await cdek.wait_order(uuid)
    except CdekError as ex:
        await wait.edit_text(f"❌ СДЭК отклонил заказ:\n{e(str(ex))}\n\nИсправьте данные и попробуйте снова.")
        return await show_summary(c)
    number = (order.get("entity") or {}).get("cdek_number")
    oid = await db.add_order(cb.from_user.id, uuid, str(number) if number else None, dr.order_title(d), d)
    await state.clear()
    from .orders import order_actions_kb  # локальный импорт, чтобы избежать цикла
    text = (f"✅ <b>Заказ создан!</b>\n"
            f"Номер СДЭК: <code>{number or 'присваивается…'}</code>\nUUID: <code>{uuid}</code>\n"
            f"{e(dr.order_title(d))}")
    await wait.edit_text(text, reply_markup=order_actions_kb(oid, d))
    await cb.message.answer("Готово. Что дальше?", reply_markup=main_menu())


@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


# Неожиданный ввод на шагах с кнопками
@router.message(OrderSG.order_type)
@router.message(OrderSG.cod)
@router.message(OrderSG.services)
@router.message(OrderSG.tariff)
@router.message(OrderSG.summary)
async def msg_use_buttons(m: Message):
    await m.answer("Пожалуйста, воспользуйтесь кнопками выше 👆 (или «✖️ Отмена»).")
