import asyncio
import html
import logging

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent, Update

from .cdek import CdekClient, CdekError
from .config import load_config
from .db import Database
from .handlers import common, order, orders, templates

log = logging.getLogger("cdek_bot")


class AccessMiddleware(BaseMiddleware):
    def __init__(self, allowed: set[int]):
        self.allowed = allowed

    async def __call__(self, handler, event: Update, data: dict):
        user = data.get("event_from_user")
        if self.allowed and (user is None or user.id not in self.allowed):
            if event.message:
                await event.message.answer(f"⛔️ Нет доступа. Ваш Telegram ID: <code>{user.id if user else '?'}</code>")
            elif event.callback_query:
                await event.callback_query.answer("Нет доступа", show_alert=True)
            return None
        return await handler(event, data)


async def on_error(event: ErrorEvent, bot: Bot):
    exc = event.exception
    upd = event.update
    chat_id = None
    if upd.message:
        chat_id = upd.message.chat.id
    elif upd.callback_query and upd.callback_query.message:
        chat_id = upd.callback_query.message.chat.id
    if isinstance(exc, CdekError):
        text = f"❗️ Ошибка СДЭК: {html.escape(str(exc))}"
        log.warning("CDEK error: %s", exc)
    else:
        text = "❗️ Что-то пошло не так. Попробуйте ещё раз или /cancel."
        log.exception("Unhandled error", exc_info=exc)
    if chat_id:
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            pass
    return True


def make_session(cfg):
    """Обход блокировки Telegram: свой адрес Bot API (Cloudflare Worker) и/или прокси."""
    if not cfg.telegram_api_url and not cfg.telegram_proxy:
        return None
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.client.telegram import TelegramAPIServer
    kw = {}
    if cfg.telegram_api_url:
        kw["api"] = TelegramAPIServer.from_base(cfg.telegram_api_url)
        log.info("Telegram API через %s", cfg.telegram_api_url)
    if cfg.telegram_proxy:
        kw["proxy"] = cfg.telegram_proxy
        log.info("Telegram через прокси %s", cfg.telegram_proxy.split("@")[-1])
    return AiohttpSession(**kw)


def build_dispatcher(storage, cdek: CdekClient, db: Database, cfg) -> Dispatcher:
    dp = Dispatcher(storage=storage)
    dp["cdek"], dp["db"], dp["config"] = cdek, db, cfg
    dp.update.outer_middleware(AccessMiddleware(cfg.allowed_users))
    # порядок важен: кнопки главного меню должны срабатывать на любом шаге мастера
    dp.include_routers(common.router, templates.router, orders.router, order.router)
    dp.errors.register(on_error)
    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("Запуск бота…")
    cfg = load_config()
    log.info("CDEK API: %s, разрешённых пользователей: %s", cfg.cdek_base_url, len(cfg.allowed_users) or "все")
    if not cfg.allowed_users:
        log.warning("ALLOWED_USERS пуст — ботом может пользоваться любой, кто его найдёт!")

    if cfg.redis_url:
        from aiogram.fsm.storage.redis import RedisStorage
        storage = RedisStorage.from_url(cfg.redis_url)
    else:
        storage = MemoryStorage()

    bot = Bot(cfg.bot_token, session=make_session(cfg), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    cdek = CdekClient(cfg.cdek_base_url, cfg.cdek_client_id, cfg.cdek_client_secret)
    db = Database(cfg.db_path)
    await db.init()

    dp = build_dispatcher(storage, cdek, db, cfg)

    me = await bot.get_me()
    log.info("Подключились к Telegram как @%s", me.username)
    await bot.set_my_commands([
        BotCommand(command="new", description="Новый заказ"),
        BotCommand(command="templates", description="Шаблоны"),
        BotCommand(command="orders", description="Мои заказы"),
        BotCommand(command="track", description="Отследить по номеру"),
        BotCommand(command="savetpl", description="Сохранить текущие данные как шаблон"),
        BotCommand(command="cancel", description="Отменить"),
    ])
    log.info("Бот работает, жду сообщений")
    try:
        await dp.start_polling(bot)
    finally:
        await cdek.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
