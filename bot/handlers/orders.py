"""Созданные заказы: статус, печать, удаление, вызов курьера, повтор, отслеживание."""
import datetime as dt
import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ..cdek import CdekClient, CdekError
from ..config import Config
from ..db import Database
from ..wizard import ikb, main_menu, make_ctx, start_order

router = Router(name="orders")
e = html.escape

TIME_WINDOWS = [("09:00", "13:00"), ("10:00", "14:00"), ("12:00", "16:00"), ("14:00", "18:00"), ("10:00", "18:00")]
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


class TrackSG(StatesGroup):
    number = State()


def order_actions_kb(oid: int, d: dict):
    rows = [
        [("🔄 Статус", f"or:st:{oid}")],
        [("🖨 Накладная PDF", f"or:wb:{oid}"), ("🏷 Штрихкод", f"or:bc:{oid}")],
    ]
    if (d.get("from_point") or {}).get("kind") == "door":
        rows.append([("🚚 Вызвать курьера", f"or:in:{oid}")])
    rows.append([("🔁 Повторить заказ", f"or:rep:{oid}"), ("🗑 Удалить", f"or:del:{oid}")])
    return ikb(rows)


def statuses_text(entity: dict, limit: int = 8) -> str:
    sts = entity.get("statuses") or []
    lines = []
    for s in sts[:limit]:
        when = (s.get("date_time") or "")[:16].replace("T", " ")
        lines.append(f"• {e(when)} — {e(s.get('name') or s.get('code') or '')}"
                     + (f" ({e(s['city'])})" if s.get("city") else ""))
    return "\n".join(lines) or "нет данных"


@router.message(Command("orders"))
@router.message(F.text == "🗂 Мои заказы")
async def cmd_orders(m: Message, state: FSMContext, db: Database):
    await state.clear()
    orders = await db.list_orders(m.from_user.id)
    if not orders:
        return await m.answer("Заказов пока нет.", reply_markup=main_menu())
    rows = []
    for o in orders:
        date = dt.datetime.fromtimestamp(o["created_at"]).strftime("%d.%m")
        rows.append([(f"{date} · {o['cdek_number'] or '…'} · {o['title']}"[:60], f"or:v:{o['id']}")])
    await m.answer("🗂 <b>Последние заказы</b>", reply_markup=ikb(rows))


@router.callback_query(F.data.startswith("or:"))
async def cb_order(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    parts = cb.data.split(":")
    action, oid = parts[1], int(parts[2])
    order = await db.get_order(cb.from_user.id, oid)
    if not order:
        return await cb.answer("Заказ не найден", show_alert=True)
    uuid, d = order["uuid"], order["draft"]

    if action in ("v", "st"):
        await cb.answer()
        info = await cdek.get_order(uuid)
        ent = info.get("entity") or {}
        num = ent.get("cdek_number")
        if num and not order["cdek_number"]:
            await db.set_cdek_number(oid, str(num))
        errors = "; ".join(err.get("message", "") for r in info.get("requests") or [] for err in r.get("errors") or [])
        text = (f"📦 <b>Заказ {num or '(номер ещё не присвоен)'}</b>\n{e(order['title'])}\n"
                f"UUID: <code>{uuid}</code>\n\n<b>Статусы:</b>\n{statuses_text(ent)}")
        if errors:
            text += f"\n\n⚠️ {e(errors)}"
        return await cb.message.answer(text, reply_markup=order_actions_kb(oid, d))

    if action in ("wb", "bc"):
        await cb.answer("Формирую документ, это займёт несколько секунд…")
        try:
            pdf = await (cdek.print_waybill(uuid) if action == "wb" else cdek.print_barcode(uuid))
        except CdekError as ex:
            return await cb.message.answer(f"❌ {e(str(ex))}")
        name = f"{'waybill' if action == 'wb' else 'barcode'}_{order['cdek_number'] or oid}.pdf"
        return await cb.message.answer_document(BufferedInputFile(pdf, filename=name))

    if action == "del":
        await cb.answer()
        return await cb.message.answer(f"Удалить заказ {order['cdek_number'] or uuid} в СДЭК?",
                                       reply_markup=ikb([[("🗑 Да, удалить", f"or:delok:{oid}"), ("Нет", "noop")]]))
    if action == "delok":
        await cb.answer()
        try:
            await cdek.delete_order(uuid)
        except CdekError as ex:
            return await cb.message.edit_text(f"❌ Не удалось удалить: {e(str(ex))}")
        return await cb.message.edit_text("🗑 Запрос на удаление заказа отправлен в СДЭК.")

    if action == "rep":
        await cb.answer()
        base = {k: v for k, v in d.items() if k != "number"}
        return await start_order(make_ctx(cb, state, cdek, db, config), base,
                                 "🔁 Данные заказа скопированы — проверьте и измените нужное в сводке.")

    if action == "in":  # выбор даты забора
        await cb.answer()
        today = dt.date.today()
        days = [today + dt.timedelta(days=i) for i in range(0, 8)]
        days = [x for x in days if x.weekday() < 6][:6]
        rows = [[(f"{WEEKDAYS[x.weekday()]} {x:%d.%m}", f"or:ind:{oid}:{x:%Y%m%d}")] for x in days]
        return await cb.message.answer("🚚 Дата приезда курьера:", reply_markup=ikb(rows))
    if action == "ind":
        await cb.answer()
        day = parts[3]
        rows = [[(f"{a}–{b}", f"or:int:{oid}:{day}:{a.replace(':', '')}{b.replace(':', '')}")] for a, b in TIME_WINDOWS]
        return await cb.message.edit_text("🕒 Интервал:", reply_markup=ikb(rows))
    if action == "int":
        await cb.answer()
        day, win = parts[3], parts[4]
        payload = {
            "order_uuid": uuid,
            "intake_date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
            "intake_time_from": f"{win[:2]}:{win[2:4]}",
            "intake_time_to": f"{win[4:6]}:{win[6:]}",
            "need_call": False,
        }
        try:
            res = await cdek.create_intake(payload)
        except CdekError as ex:
            return await cb.message.edit_text(f"❌ Заявка на курьера отклонена: {e(str(ex))}")
        return await cb.message.edit_text(
            f"✅ Курьер вызван на {payload['intake_date']} {payload['intake_time_from']}–{payload['intake_time_to']}\n"
            f"Заявка: <code>{(res.get('entity') or {}).get('uuid', '')}</code>")
    await cb.answer()


# ---------------------------------------------------------------- отслеживание
@router.message(Command("track"))
@router.message(F.text == "🔎 Отследить")
async def cmd_track(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(TrackSG.number)
    await m.answer("Введите номер заказа СДЭК:")


@router.message(TrackSG.number, F.text)
async def msg_track(m: Message, state: FSMContext, cdek: CdekClient):
    num = m.text.strip()
    if not num.isdigit():
        return await m.answer("Номер СДЭК состоит из цифр")
    try:
        info = await cdek.find_order(num)
    except CdekError as ex:
        return await m.answer(f"❌ {e(str(ex))}")
    await state.clear()
    ent = info.get("entity") or {}
    to = (ent.get("to_location") or {}).get("city") or (ent.get("delivery_point") or "")
    await m.answer(f"📦 <b>{num}</b> {('→ ' + e(str(to))) if to else ''}\n\n{statuses_text(ent, 15)}",
                   reply_markup=main_menu())
