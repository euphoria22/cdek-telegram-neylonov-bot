"""Быстрый старт: получатель одним сообщением (ФИО, адрес ПВЗ или для курьера, телефон)."""
import html

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import draft as dr
from ..cdek import CdekClient, CdekError
from ..config import Config
from ..db import Database
from ..wizard import OrderSG, ikb, make_ctx, start_order

router = Router(name="quick")
e = html.escape

HINT = ("Чтобы начать заказ, нажмите «📦 Новый заказ» или пришлите данные получателя одним сообщением:\n"
        "<code>1. Иванов Алексей Сергеевич\n2. Санкт-Петербург, ул. Адмирала Трибуца, 5, пом. 37-Н\n"
        "3. +79990257058</code>\n\nАдрес может быть как ПВЗ, так и адресом для курьера — бот разберётся.")


async def build_prefill(cdek: CdekClient, req: dict) -> dict:
    prefill = {"recipient": req["recipient"], "_to_address": req["address"]}
    if req["city"]:
        prefill["_to_city_q"] = req["city"]
        try:
            cities = await cdek.find_cities(req["city"])
        except CdekError:
            cities = []
        exact = [x for x in cities if (x.get("name") or "").lower() == req["city"].lower()]
        if len(exact) == 1 or len(cities) == 1:
            prefill["to_city"] = (exact or cities)[0]
            prefill.pop("_to_city_q")
    return prefill


@router.message(StateFilter(None, OrderSG.order_type), F.text)
async def msg_quick(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    if m.text.startswith("/"):
        return await m.answer(HINT)
    try:
        req = dr.parse_quick_request(m.text)
    except ValueError as ex:
        if "\n" in m.text.strip():
            return await m.answer(f"⚠️ Не разобрал данные получателя: {e(str(ex))}.\n\n" + HINT)
        return await m.answer(HINT)
    c = make_ctx(m, state, cdek, db, config)
    prefill = await build_prefill(cdek, req)
    city = prefill["to_city"]["label"] if prefill.get("to_city") else (req["city"] or "уточню")
    notice = (f"📥 <b>Получатель:</b> {dr.person_str(req['recipient'])}\n"
              f"<b>Город:</b> {e(city)}\n<b>Адрес:</b> {e(req['address'])}\n"
              "<i>Если адрес совпадёт с ПВЗ — выберу его, иначе адрес пойдёт курьеру (если тариф «до двери»).</i>")
    tpls = await db.list_templates(c.tpl_owner)
    if not tpls:
        return await start_order(c, prefill, notice)
    await state.clear()
    await state.update_data(prefill=prefill)
    rows = [[("🆕 С нуля", "tp:new:0")]] + [[(f"📄 {t['name']}", f"tp:use:{t['id']}")] for t in tpls]
    await m.answer(notice + "\n\nОформить с нуля или по шаблону?", reply_markup=ikb(rows))
