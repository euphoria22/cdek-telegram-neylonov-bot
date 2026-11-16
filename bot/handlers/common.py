import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ..cdek import CdekClient
from ..config import Config
from ..db import Database
from ..wizard import ikb, main_menu, make_ctx, start_order

router = Router(name="common")
e = html.escape

HELP = (
    "🤖 <b>Бот оформления заказов СДЭК</b>\n\n"
    "📦 <b>Новый заказ</b> — пошаговое оформление: тип, города, места/товары, тариф с ценами, ПВЗ или адрес, "
    "страховка/наложка, доп. услуги и коробки, отправитель, получатель. В конце — сводка с расчётом стоимости, "
    "где можно изменить любой блок.\n\n"
    "⚡️ <b>Быстрый старт</b> — просто пришлите получателя одним сообщением:\n"
    "<code>1. Иванов Алексей Сергеевич\n2. Санкт-Петербург, ул. Адмирала Трибуца, 5, пом. 37-Н\n3. +79990257058</code>\n"
    "Если адрес — это ПВЗ СДЭК, бот выберет его; иначе адрес уйдёт курьеру. Остальное бот спросит.\n\n"
    "📋 <b>Шаблоны</b> — сохраняйте любой набор полей (например город + ПВЗ отправки + тариф). При создании "
    "заказа из шаблона бот спросит только недостающее.\n\n"
    "🗂 <b>Мои заказы</b> — статус, накладная и штрихкод PDF, вызов курьера, повтор, удаление.\n\n"
    "Команды: /new, /templates, /orders, /track, /savetpl (сохранить шаблон на любом шаге), /cancel"
)


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(HELP, reply_markup=main_menu())


@router.message(Command("cancel"))
@router.message(F.text.lower() == "отмена")
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменено.", reply_markup=main_menu())


@router.callback_query(F.data == "wz:cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer("Отменено")
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("Оформление отменено.", reply_markup=main_menu())


@router.message(Command("new"))
@router.message(F.text == "📦 Новый заказ")
async def cmd_new(m: Message, state: FSMContext, cdek: CdekClient, db: Database, config: Config):
    c = make_ctx(m, state, cdek, db, config)
    await state.clear()
    tpls = await db.list_templates(c.tpl_owner)
    if not tpls:
        return await start_order(c)
    rows = [[("🆕 С нуля", "tp:new:0")]] + [[(f"📄 {t['name']}", f"tp:use:{t['id']}")] for t in tpls]
    await m.answer("📦 <b>Новый заказ</b> — начать с нуля или из шаблона?", reply_markup=ikb(rows))
