"""Шаблоны: частичное сохранение черновика и создание заказа из шаблона."""
import html

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import draft as dr
from ..catalog import TEMPLATE_BLOCKS, TEMPLATE_DEFAULT
from ..cdek import CdekClient
from ..config import Config
from ..db import Database
from ..wizard import Ctx, advance, ikb, main_menu, make_ctx, start_order

router = Router(name="templates")
e = html.escape


class TplSG(StatesGroup):
    choose = State()
    name = State()
    rename = State()


# ---------------------------------------------------------------- сохранение
def blocks_kb(d: dict, selected: set[str]):
    rows = []
    for key, title in TEMPLATE_BLOCKS:
        if key in d:
            rows.append([(("☑️ " if key in selected else "⬜️ ") + title, f"ts:t:{key}")])
    rows.append([("Выбрать всё", "ts:all"), ("Снять всё", "ts:none")])
    rows.append([("➡️ Далее", "ts:ok"), ("↩️ Вернуться", "ts:back")])
    return ikb(rows)


async def start_save(c: Ctx, message=None) -> None:
    """Запуск сохранения шаблона из текущего черновика (из сводки или на любом шаге)."""
    data = await c.state.get_data()
    d = data.get("draft") or {}
    if not any(k in d for k, _ in TEMPLATE_BLOCKS):
        await c.send("Пока нечего сохранять — заполните хотя бы один шаг.")
        return
    selected = [k for k, _ in TEMPLATE_BLOCKS if k in d and k in TEMPLATE_DEFAULT]
    await c.state.update_data(tpl_sel=selected, tpl_return=await c.state.get_state())
    await c.state.set_state(TplSG.choose)
    await c.send("💾 <b>Что сохранить в шаблон?</b>\nОтмеченные поля будут подставляться автоматически, "
                 "остальное бот спросит при создании заказа.", blocks_kb(d, set(selected)))


@router.message(Command("savetpl"))
async def cmd_savetpl(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await start_save(make_ctx(m, state, cdek, db, config))


@router.callback_query(F.data == "sm:tpl")
async def cb_save_from_summary(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await cb.answer()
    await start_save(make_ctx(cb, state, cdek, db, config))


@router.callback_query(TplSG.choose, F.data.startswith("ts:"))
async def cb_choose(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    data = await state.get_data()
    d = data.get("draft") or {}
    sel = set(data.get("tpl_sel") or [])
    action = cb.data.split(":")[1]
    c = make_ctx(cb, state, cdek, db, config)
    if action == "t":
        key = cb.data.split(":")[2]
        sel ^= {key}
    elif action == "all":
        sel = {k for k, _ in TEMPLATE_BLOCKS if k in d}
    elif action == "none":
        sel = set()
    elif action == "back":
        await cb.answer()
        await cb.message.edit_reply_markup(reply_markup=None)
        return await advance(c)
    elif action == "ok":
        if not sel:
            return await cb.answer("Отметьте хотя бы одно поле", show_alert=True)
        await cb.answer()
        await cb.message.edit_text("💾 В шаблон войдут:\n" + "\n".join(dr.describe(d, sel)))
        await state.set_state(TplSG.name)
        tpls = await db.list_templates(c.tpl_owner)
        hint = "\n\nСуществующие: " + ", ".join(f"<code>{e(t['name'])}</code>" for t in tpls) if tpls else ""
        return await cb.message.answer("Введите <b>название шаблона</b> (если такое уже есть — шаблон будет "
                                       "перезаписан):" + hint)
    await state.update_data(tpl_sel=list(sel))
    await cb.answer()
    await cb.message.edit_reply_markup(reply_markup=blocks_kb(d, sel))


@router.message(TplSG.name, F.text)
async def msg_tpl_name(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    name = m.text.strip()[:60]
    if not name:
        return await m.answer("Введите название")
    data = await state.get_data()
    d = data.get("draft") or {}
    sel = set(data.get("tpl_sel") or [])
    tpl = {k: d[k] for k, _ in TEMPLATE_BLOCKS if k in sel and k in d}
    c = make_ctx(m, state, cdek, db, config)
    await db.save_template(c.tpl_owner, name, tpl)
    await m.answer(f"✅ Шаблон «{e(name)}» сохранён.")
    await advance(c)  # продолжаем оформление с того же места


# ---------------------------------------------------------------- список / применение
async def show_templates(c: Ctx, edit: Message | None = None) -> None:
    tpls = await c.db.list_templates(c.tpl_owner)
    if not tpls:
        text = ("📋 Шаблонов пока нет.\n\nОформите заказ и нажмите «💾 В шаблон» в сводке (или /savetpl на любом "
                "шаге) — и выберите, какие поля сохранить, например только город + ПВЗ отправки + тариф.")
        markup = None
    else:
        text = "📋 <b>Шаблоны</b>"
        markup = ikb([[(f"📄 {t['name']}", f"tp:v:{t['id']}")] for t in tpls])
    if edit:
        await edit.edit_text(text, reply_markup=markup)
    else:
        await c.send(text, markup or main_menu())


@router.message(Command("templates"))
@router.message(F.text == "📋 Шаблоны")
async def cmd_templates(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    await state.clear()
    await show_templates(make_ctx(m, state, cdek, db, config))


@router.callback_query(F.data.startswith("tp:"))
async def cb_tpl(cb: CallbackQuery, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    _, action, tid = cb.data.split(":")
    c = make_ctx(cb, state, cdek, db, config)
    if action == "new":  # новый заказ «с нуля» из меню выбора
        await cb.answer()
        await cb.message.edit_reply_markup(reply_markup=None)
        return await start_order(c)
    if action == "list":
        await cb.answer()
        return await show_templates(c, edit=cb.message)
    tpl = await db.get_template(c.tpl_owner, int(tid))
    if not tpl:
        return await cb.answer("Шаблон не найден", show_alert=True)
    await cb.answer()
    if action == "v":
        text = f"📄 <b>{e(tpl['name'])}</b>\n\n" + "\n".join(dr.describe(tpl["data"]))
        await cb.message.edit_text(text, reply_markup=ikb([
            [("📦 Создать заказ", f"tp:use:{tid}")],
            [("✏️ Переименовать", f"tp:ren:{tid}"), ("🗑 Удалить", f"tp:del:{tid}")],
            [("‹ К списку", "tp:list:0")]]))
    elif action == "use":
        await cb.message.edit_reply_markup(reply_markup=None)
        filled = ", ".join(t for k, t in TEMPLATE_BLOCKS if k in tpl["data"])
        await start_order(c, tpl["data"], f"📄 Шаблон «{e(tpl['name'])}» применён.\nУже заполнено: {filled}.")
    elif action == "del":
        await cb.message.edit_text(f"Удалить шаблон «{e(tpl['name'])}»?", reply_markup=ikb([
            [("🗑 Да, удалить", f"tp:delok:{tid}"), ("Отмена", f"tp:v:{tid}")]]))
    elif action == "delok":
        await db.delete_template(c.tpl_owner, int(tid))
        await show_templates(c, edit=cb.message)
    elif action == "ren":
        await state.set_state(TplSG.rename)
        await state.update_data(tpl_id=int(tid))
        await cb.message.answer("Введите новое название:")


@router.message(TplSG.rename, F.text)
async def msg_rename(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    c = make_ctx(m, state, cdek, db, config)
    tid = (await state.get_data()).get("tpl_id")
    try:
        await db.rename_template(c.tpl_owner, tid, m.text.strip()[:60])
    except Exception:
        return await m.answer("Шаблон с таким названием уже есть")
    await state.clear()
    await m.answer("✅ Переименовано")
    await show_templates(c)
