"""/rules — правила веса и габаритов по названию товара."""
import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import packing
from ..cdek import CdekClient
from ..config import Config
from ..db import Database
from ..wizard import get_rules, ikb, main_menu, make_ctx

router = Router(name="rules")
e = html.escape

FORMAT = ("Формат — правило на строку, проверяются сверху вниз:\n"
          "<code>Название: слово1, слово2 = вес_г ДxШxВ</code>\n"
          "Слова — начала слов в названии товара («куртк» подходит к «Куртка» и «Куртки»).\n"
          "Последняя строка — коробка, если правило не подошло:\n"
          "<code>По умолчанию: вес_г Д x Ш x высота_на_1_вещь, мин N, макс N</code>\n\n"
          "Несколько вещей кладутся стопкой: длина и ширина — по самой большой, высоты складываются; "
          "выше «макс» — следующее место.")


class RulesSG(StatesGroup):
    edit = State()


async def show_rules(c, edit_msg=None):
    rules = await get_rules(c)
    text = (f"📏 <b>Вес и габариты по названию товара</b>\n\n<code>{e(packing.rules_to_text(rules))}</code>\n\n"
            "Чтобы изменить — нажмите «✏️ Изменить», скопируйте текст выше, поправьте и отправьте.")
    markup = ikb([[("✏️ Изменить", "rl:edit")], [("↩️ Вернуть стандартные", "rl:reset")]])
    if edit_msg:
        await edit_msg.edit_text(text, reply_markup=markup)
    else:
        await c.send(text, markup)


@router.message(Command("rules"))
async def cmd_rules(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await state.clear()
    await show_rules(make_ctx(m, state, cdek, db, config))


@router.callback_query(F.data.startswith("rl:"))
async def cb_rules(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    c = make_ctx(cb, state, cdek, db, config)
    await cb.answer()
    if cb.data == "rl:edit":
        await state.set_state(RulesSG.edit)
        return await cb.message.answer("Пришлите новый список правил целиком.\n\n" + FORMAT,
                                       reply_markup=ikb([[("✖️ Отмена", "wz:cancel")]]))
    if cb.data == "rl:reset":
        await db.set_setting(c.tpl_owner, "pack_rules", None)
        await show_rules(c, edit_msg=cb.message)


@router.message(RulesSG.edit, F.text)
async def msg_rules(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    c = make_ctx(m, state, cdek, db, config)
    try:
        rules = packing.parse_rules(m.text)
    except ValueError as ex:
        return await m.answer(f"⚠️ {e(str(ex))}\n\n" + FORMAT)
    await db.set_setting(c.tpl_owner, "pack_rules", packing.rules_to_text(rules))
    await state.clear()
    await m.answer(f"✅ Сохранено правил: {len(rules.rules)}", reply_markup=main_menu())
    await show_rules(c)
