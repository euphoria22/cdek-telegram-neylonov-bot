"""Сквозной прогон бота: настоящий Dispatcher и хэндлеры, фейковый Telegram и мок API СДЭК."""
import itertools
import json

import httpx
import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, Update

from bot.__main__ import build_dispatcher
from bot.cdek import CdekClient
from bot.config import Config
from bot.db import Database

UID = 777


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.ids = itertools.count(1000)

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name in ("SendMessage", "SendDocument", "EditMessageText", "EditMessageReplyMarkup"):
            return Message.model_validate({
                "message_id": next(self.ids), "date": 0, "chat": {"id": UID, "type": "private"},
                "text": getattr(method, "text", None) or "x"}, context={"bot": bot})
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):
        raise NotImplementedError


POINTS = [
    {"code": "MSK100", "name": "На Тверской", "type": "PVZ", "work_time": "Пн-Вс 10-21",
     "location": {"address": "ул. Тверская, 7", "latitude": 55.757, "longitude": 37.613}},
    {"code": "MSK200", "name": "На Арбате", "type": "PVZ", "work_time": "Пн-Вс 9-20",
     "location": {"address": "ул. Арбат, 10", "latitude": 55.75, "longitude": 37.59}},
    {"code": "SPB5", "name": "Трибуца", "type": "PVZ", "work_time": "Пн-Вс 10-20",
     "location": {"address": "ул. Адмирала Трибуца, 5", "latitude": 59.85, "longitude": 30.15}},
]


class FakeCdek:
    def __init__(self):
        self.orders = []
        self.intakes = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        p, m = req.url.path, req.method
        body = json.loads(req.content) if req.content and m in ("POST",) and "oauth" not in p else None
        if p.endswith("/oauth/token"):
            return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
        if p.endswith("/location/suggest/cities"):
            name = req.url.params["name"]
            if name.startswith("Моск"):
                return httpx.Response(200, json=[{"code": 44, "full_name": "Москва, Россия"}])
            return httpx.Response(200, json=[{"code": 137, "full_name": "Санкт-Петербург, Россия"},
                                             {"code": 999, "full_name": "Санкт-Пётр, Где-то"}])
        if p.endswith("/deliverypoints"):
            return httpx.Response(200, json=POINTS)
        if p.endswith("/calculator/tarifflist"):
            return httpx.Response(200, json={"tariff_codes": [
                {"tariff_code": 139, "tariff_name": "Посылка дверь-дверь", "delivery_mode": 1, "delivery_sum": 900,
                 "period_min": 1, "period_max": 2},
                {"tariff_code": 136, "tariff_name": "Посылка склад-склад", "delivery_mode": 4, "delivery_sum": 350,
                 "period_min": 2, "period_max": 3},
                {"tariff_code": 137, "tariff_name": "Посылка склад-дверь", "delivery_mode": 3, "delivery_sum": 500,
                 "period_min": 2, "period_max": 3},
            ]})
        if p.endswith("/calculator/tariff"):
            # 234 нет в tarifflist, но он считается — бот должен его досчитать
            if body.get("tariff_code") not in (136, 137, 139, 234):
                return httpx.Response(400, json={"errors": [{"code": "v2_tariff", "message": "Тариф недоступен"}]})
            if body["tariff_code"] == 234:
                return httpx.Response(200, json={"delivery_sum": 400, "total_sum": 400, "period_min": 4, "period_max": 6})
            return httpx.Response(200, json={"delivery_sum": 350, "total_sum": 420, "period_min": 2, "period_max": 3,
                                             "services": [{"code": "INSURANCE", "sum": 20}, {"code": "CARTON_BOX_S", "sum": 50}]})
        if p.endswith("/orders") and m == "POST" and "/print/" not in p:
            self.orders.append(body)
            return httpx.Response(202, json={"entity": {"uuid": f"uuid-{len(self.orders)}"},
                                             "requests": [{"type": "CREATE", "state": "ACCEPTED"}]})
        if "/orders/uuid-" in p and m == "GET":
            return httpx.Response(200, json={"entity": {"uuid": p.rsplit("/", 1)[1], "cdek_number": "1234567890",
                                                        "statuses": [{"code": "CREATED", "name": "Создан",
                                                                      "date_time": "2026-11-16T10:00:00+0000"}]},
                                             "requests": [{"type": "CREATE", "state": "SUCCESSFUL"}]})
        if p.endswith("/print/orders") and m == "POST":
            return httpx.Response(202, json={"entity": {"uuid": "P1"}})
        if p.endswith("/print/orders/P1"):
            return httpx.Response(200, json={"entity": {"uuid": "P1", "url": "https://api.edu.cdek.ru/v2/print/orders/P1.pdf",
                                                        "statuses": [{"code": "READY"}]}})
        if p.endswith("P1.pdf"):
            return httpx.Response(200, content=b"%PDF-1.4 fake", headers={"content-type": "application/pdf"})
        if p.endswith("/intakes"):
            self.intakes.append(body)
            return httpx.Response(202, json={"entity": {"uuid": "INTAKE1"}, "requests": [{"state": "ACCEPTED"}]})
        return httpx.Response(404, json={"errors": [{"code": "nf", "message": f"no mock for {m} {p}"}]})


class Harness:
    def __init__(self, tmp_path, monkeypatch):
        import asyncio as _a
        orig_sleep = _a.sleep
        monkeypatch.setattr("bot.cdek.asyncio.sleep", lambda s: orig_sleep(0))
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session)
        self.fake = FakeCdek()
        self.cdek = CdekClient("https://api.edu.cdek.ru/v2", "id", "secret", transport=httpx.MockTransport(self.fake))
        self.db = Database(str(tmp_path / "t.sqlite3"))
        cfg = Config("x", "id", "s", "", set(), False, "", None)
        self.dp = build_dispatcher(MemoryStorage(), self.cdek, self.db, cfg)
        self.uid = itertools.count(1)
        self.mark = 0

    def _base(self):
        return {"message_id": 1, "date": 0, "chat": {"id": UID, "type": "private"},
                "from": {"id": UID, "is_bot": False, "first_name": "U"}}

    async def feed(self, **payload):
        upd = Update.model_validate({"update_id": next(self.uid), **payload}, context={"bot": self.bot})
        self.mark = len(self.session.calls)
        await self.dp.feed_update(self.bot, upd)
        return self.sent()

    async def text(self, t):
        return await self.feed(message={**self._base(), "text": t})

    async def pkgs(self, t=None, click=None, choice="bx:no"):
        """Ввод мест (текстом или кнопкой) + ответ на вопрос об упаковке СДЭК."""
        first = await (self.click(click) if click else self.text(t))
        if choice in self.buttons(first):
            return first + await self.click(choice)
        return first

    async def loc(self, lat, lon):
        return await self.feed(message={**self._base(), "location": {"latitude": lat, "longitude": lon}})

    async def click(self, data):
        return await self.feed(callback_query={"id": "c", "from": self._base()["from"], "chat_instance": "ci",
                                               "data": data, "message": {**self._base(), "text": "old"}})

    def sent(self):
        """Тексты и кнопки, отправленные ботом за последний апдейт."""
        out = []
        for m in self.session.calls[self.mark:]:
            text = getattr(m, "text", None) or ""
            kb = getattr(m, "reply_markup", None)
            buttons = [b.callback_data for row in getattr(kb, "inline_keyboard", []) or [] for b in row]
            out.append((type(m).__name__, text, buttons))
        return out

    @staticmethod
    def joined(sent):
        return "\n".join(t for _, t, _ in sent)

    @staticmethod
    def buttons(sent):
        return [b for _, _, bs in sent for b in bs]


@pytest.fixture
async def h(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    await h.db.init()
    yield h
    from bot.handlers import common, order, orders, quick, rules, templates
    for r in (common.router, templates.router, orders.router, order.router, quick.router, rules.router):
        r._parent_router = None  # роутеры модульные — отвязываем для следующего теста
    await h.cdek.close()
    await h.db.close()


async def test_full_delivery_order_then_template(h: Harness):
    j = h.joined
    await h.text("/start")
    s = await h.text("📦 Новый заказ")
    assert "ot:2" in h.buttons(s)
    s = await h.click("ot:2")
    assert "город отправления" in j(s)
    s = await h.text("Моск")                       # один результат → выбирается сразу
    assert "город получения" in j(s)
    s = await h.text("Санкт")                      # два результата → список
    assert "pk:tc:0" in h.buttons(s)
    s = await h.click("pk:tc:0")
    assert "Грузовые места" in j(s)
    s = await h.pkgs("1.5 30x20x10 Документы")
    tariff_buttons = [b for b in h.buttons(s) if b.startswith("pk:tf:")]
    assert len(tariff_buttons) == 4               # 3 из tarifflist + досчитанный 234
    # сортировка по цене: 136 (350) · 234 (400) · 137 (500) · 139 (900)
    assert tariff_buttons[0] == "pk:tf:0"
    s = await h.click("pk:tf:0")
    assert "Выбор пункта" in j(s) and "MSK100" in j(s)
    s = await h.text("арбат")                      # поиск по адресу
    assert "MSK200" in j(s) and "MSK100" not in j(s)
    s = await h.click("pk:fp:0")
    assert "Пункт отправки" in j(s) and "получения" in j(s)
    s = await h.loc(55.757, 37.613)                # ближайшие к геолокации
    assert j(s).index("MSK100") < j(s).index("MSK200")
    s = await h.click("pk:tp:0")
    assert "Страховка" in j(s)
    s = await h.text("5000")
    assert "Дополнительные услуги" in j(s)
    await h.click("sv:t:SMS")
    s = await h.click("sv:pg:pack")
    assert "sv:+:CARTON_BOX_S" in h.buttons(s)
    await h.click("sv:+:CARTON_BOX_S")
    await h.click("sv:+:CARTON_BOX_S")
    s = await h.click("sv:ok")
    assert "Отправитель" in j(s)
    s = await h.text("Петров Пётр\n+7 999 111-22-33\nООО Ромашка")
    s = await h.text("Сидоров Сидор\n89990001122")
    s = await h.click("cm:skip")
    assert "Проверьте заказ" in j(s) and "420" in j(s) and "Коробка S" in j(s)

    # --- частичное сохранение шаблона: по умолчанию тип+город+ПВЗ отправки+тариф+услуги+отправитель
    s = await h.click("sm:tpl")
    assert "ts:t:recipient" in h.buttons(s)
    await h.click("ts:t:services")                 # снимаем услуги
    s = await h.click("ts:ok")
    s = await h.text("МСК ПВЗ 136")
    assert "сохранён" in j(s) and "Проверьте заказ" in j(s)  # вернулись к сводке

    # --- создание заказа
    s = await h.click("sm:create")
    assert "1234567890" in j(s)
    o = h.fake.orders[-1]
    assert o["type"] == 2 and o["tariff_code"] == 136
    assert o["shipment_point"] == "MSK200" and o["delivery_point"] == "MSK100"
    assert {"code": "INSURANCE", "parameter": "5000.0"} in o["services"]
    assert {"code": "CARTON_BOX_S", "parameter": "2"} in o["services"]
    assert o["packages"][0]["comment"] == "Документы" and o["packages"][0]["weight"] == 1500
    assert o["recipient"]["phones"] == [{"number": "+79990001122"}]
    assert o["sender"] == {"name": "Петров Пётр", "phones": [{"number": "+79991112233"}], "company": "ООО Ромашка"}

    # --- печать накладной
    s = await h.click("or:wb:1")
    assert any(k == "SendDocument" for k, _, _ in s)

    # --- заказ из шаблона: спрашивается только недостающее
    s = await h.text("📦 Новый заказ")
    assert "tp:use:1" in h.buttons(s)
    s = await h.click("tp:use:1")
    assert "город получения" in j(s)               # тип, город/ПВЗ отправки и тариф не спрашиваются
    await h.text("Моск")
    s = await h.pkgs("2 40x30x20")
    assert "Выбор пункта" in j(s) and "получения" in j(s)  # тариф из шаблона → сразу ПВЗ получения
    s = await h.click("pk:tp:1")
    s = await h.text("0")
    assert "Дополнительные услуги" in j(s)         # услуги не сохраняли → спрашиваются
    s = await h.click("sv:ok")
    assert "Получатель" in j(s)                    # отправитель взят из шаблона
    await h.text("Кузнецов\n+79995554433")
    s = await h.click("cm:skip")
    assert "Проверьте заказ" in j(s) and "MSK200" in j(s)

    # --- редактирование из сводки и «назад»
    s = await h.click("ed:tf")
    assert "pk:tf:3" in h.buttons(s)
    s = await h.click("pk:tf:3")                   # 139 дверь-дверь → пункты сбрасываются, просит адреса
    assert "адрес забора" in j(s)
    s = await h.click("wz:back")                   # отменяет выбор тарифа
    assert "pk:tf:0" in h.buttons(s)


async def test_im_order_with_items_and_cod(h: Harness):
    j = h.joined
    await h.text("📦 Новый заказ")
    s = await h.click("ot:1")
    assert "num:gen" in h.buttons(s)
    await h.click("num:gen")
    await h.text("Моск")
    await h.text("Моск")
    s = await h.pkgs("1 20x20x20\n0.5 10x10x10")
    assert "место 1 из 2" in j(s).lower() or "месте 1 из 2" in j(s).lower()
    s = await h.text("Футболка + 1000 + 0.3 + 2")
    assert "2 из 2" in j(s)
    s = await h.text("Кепка; 500; 0.2")
    s = await h.click("pk:tf:2")                   # 137 склад-дверь
    await h.click("pk:fp:0")
    s = await h.text("ул. Ленина, 1, кв. 5")
    assert "Что оплачивает получатель" in j(s) and "2 500" in j(s)
    s = await h.click("cod:all")
    await h.text("300")
    await h.click("sv:ok")
    s = await h.click("snd:skip")
    await h.text("Иванов\n+79990000000")
    await h.text("Позвонить заранее")
    s = await h.click("sm:create")
    o = h.fake.orders[-1]
    assert o["type"] == 1 and o["number"].startswith("TG")
    assert o["to_location"] == {"code": 44, "address": "ул. Ленина, 1, кв. 5"}
    assert o["delivery_recipient_cost"] == {"value": 300.0}
    assert o["packages"][1]["items"][0]["payment"] == {"value": 500.0}
    assert "sender" not in o and o["comment"] == "Позвонить заранее"
    assert "or:in:1" not in h.buttons(s)           # курьер только для «от двери»


async def test_menu_button_interrupts_wizard(h: Harness):
    await h.text("📦 Новый заказ")
    await h.click("ot:2")
    s = await h.text("🗂 Мои заказы")               # не воспринимается как название города
    assert "Заказов пока нет" in h.joined(s)


async def test_im_recipient_pays_delivery_with_markup(h: Harness):
    j = h.joined
    await h.text("📦 Новый заказ")
    await h.click("ot:1")
    await h.click("num:gen")
    await h.text("Моск")
    await h.text("Моск")
    await h.pkgs("1 20x20x20")
    await h.text("Книга; 800; 0.9")
    await h.click("pk:tf:0")                       # 136 склад-склад
    await h.click("pk:fp:0")
    s = await h.click("pk:tp:1")
    assert "cod:delivery" in h.buttons(s)
    s = await h.click("cod:delivery")
    assert "cdm:10" in h.buttons(s)
    s = await h.text("10%")
    assert "по тарифу +10%" in j(s)
    await h.click("sv:ok")
    await h.click("snd:skip")
    await h.text("Иванов\n+79990000000")
    s = await h.click("cm:skip")
    # тариф 350 ₽ +10% = 385 ₽, товары не оплачиваются получателем
    assert "Взять с получателя:</b> доставка 385 ₽" in j(s) and "маржа на доставке: 35 ₽" in j(s)
    # наценка сохраняется в шаблоне
    await h.click("sm:tpl")
    await h.click("ts:t:cod")
    await h.click("ts:ok")
    await h.text("ИМ наценка 10")
    tpl = (await h.db.list_templates(UID))[0]["data"]
    assert tpl["cod"] == {"mode": "delivery", "markup": 10.0}
    await h.click("sm:create")
    o = h.fake.orders[-1]
    assert o["delivery_recipient_cost"] == {"value": 385.0}
    assert o["packages"][0]["items"][0]["payment"] == {"value": 0}


REQUEST_PVZ = """1. Иванов Алексей Сергеевич

2. Санкт-Петербург, ул. Адмирала Трибуца, 5, пом. 37-Н

3. +79990257058"""


async def test_quick_request_address_is_pvz(h: Harness):
    j = h.joined
    s = await h.text(REQUEST_PVZ)
    assert "Иванов Алексей Сергеевич" in j(s) and "Санкт-Петербург" in j(s) and "ot:2" in h.buttons(s)
    await h.click("ot:2")
    s = await h.text("Моск")
    assert "Грузовые места" in j(s)                # город получения взят из запроса
    s = await h.pkgs("1 20x20x10")
    assert "совпадает с ПВЗ <code>SPB5</code>" in j(s)
    assert len([b for b in h.buttons(s) if b.startswith("pk:tf:")]) == 2  # только тарифы до ПВЗ: 136, 234
    s = await h.click("pk:tf:0")
    s = await h.click("pk:fp:0")
    assert "Страховка" in j(s)                     # пункт получения уже выбран
    await h.text("0")
    await h.click("sv:ok")
    s = await h.text("Петров Пётр\n+79991112233")
    assert "Комментарий" in j(s)                   # получатель не спрашивается
    s = await h.click("cm:skip")
    assert "Проверьте заказ" in j(s)
    await h.click("sm:create")
    o = h.fake.orders[-1]
    assert o["delivery_point"] == "SPB5" and o["tariff_code"] == 136
    assert o["recipient"] == {"name": "Иванов Алексей Сергеевич", "phones": [{"number": "+79990257058"}]}


async def test_quick_request_courier_address_with_template(h: Harness):
    j = h.joined
    await h.db.save_template(UID, "Из Москвы", {"order_type": 2, "from_city": {"code": 44, "name": "Москва",
                                                                               "label": "Москва, Россия"}})
    s = await h.text("Петрова Мария\nМосква, ул. Ленина, 10, кв. 5\n8 999 123 45 67")
    assert "tp:use:1" in h.buttons(s)
    s = await h.click("tp:use:1")
    assert "Грузовые места" in j(s)                # тип и город отправки — из шаблона, город получения — из запроса
    s = await h.pkgs("1 20x20x10")
    assert "совпадает" not in j(s) and len([b for b in h.buttons(s) if b.startswith("pk:tf:")]) == 4
    s = await h.click("pk:tf:3")                   # 139 дверь-дверь
    assert "адрес забора" in j(s)
    s = await h.text("ул. Пушкина, 1")
    assert "из запроса" in j(s) and "Страховка" in j(s)  # адрес курьеру подставлен
    await h.text("0")
    await h.click("sv:ok")
    await h.text("Петров Пётр\n+79991112233")
    await h.click("cm:skip")
    await h.click("sm:create")
    o = h.fake.orders[-1]
    assert o["to_location"] == {"code": 44, "address": "ул. Ленина, 10, кв. 5"}
    assert o["recipient"]["name"] == "Петрова Мария" and o["recipient"]["phones"] == [{"number": "+79991234567"}]


async def test_quick_request_same_street_pvz_tariff(h: Harness):
    j = h.joined
    await h.text("Сидоров Сидор\nМосква, ул. Арбат, 3\n+79990000001")
    await h.click("ot:2")
    await h.text("Моск")
    s = await h.pkgs("1 20x20x10")
    assert "совпадает" not in j(s)                 # дом 3 ≠ ПВЗ в доме 10
    await h.click("pk:tf:0")                       # 136 до ПВЗ
    s = await h.click("pk:fp:0")
    assert "рядом с адресом" in j(s) and "<code>MSK200</code> ул. Арбат" in j(s) and "Найдено: 1" in j(s)


async def test_unknown_text_shows_hint(h: Harness):
    s = await h.text("привет")
    assert "одним сообщением" in h.joined(s)


async def _to_packages_step(h: Harness, otype: str = "ot:2"):
    await h.text("📦 Новый заказ")
    await h.click(otype)
    if otype == "ot:1":
        await h.click("num:gen")
    await h.text("Моск")
    return await h.text("Моск")


async def test_packages_by_buttons(h: Harness):
    j = h.joined
    s = await _to_packages_step(h)
    assert "pb:a:0" in h.buttons(s) and "pb:ok" not in h.buttons(s)
    await h.click("pb:a:0")                        # Пуховик
    s = await h.click("pb:a:5")                    # Футболка
    assert "55×41×20" in j(s) and "1.27 кг" in j(s) and "pb:ok" in h.buttons(s)
    await h.click("pb:a:5")
    s = await h.click("pb:pop")                    # убрали одну футболку
    assert "Футболка" in j(s) and "1.27 кг" in j(s)
    s = await h.pkgs(click="pb:ok")
    assert "pk:tf:0" in h.buttons(s)               # дальше — тариф
    data = await h.dp.storage.get_data(key=__import__("aiogram").fsm.storage.base.StorageKey(42, UID, UID))
    assert data["draft"]["packages"] == [{"weight": 1270, "length": 55, "width": 41, "height": 20,
                                         "comment": "Пуховик, Футболка"}]


async def test_packages_by_names_and_im_item_weight_from_rules(h: Harness):
    j = h.joined
    await _to_packages_step(h, "ot:1")
    s = await h.pkgs("пуховик, футболка 2")
    assert "Мест: 1, общий вес 1.54 кг" in j(s) and "55×41×25" in j(s)
    assert "Товары в месте 1 из 1" in j(s)
    s = await h.text("Пуховик Arctic + 8990\nФутболка белая + 1490 + + 2")
    data = await h.dp.storage.get_data(key=__import__("aiogram").fsm.storage.base.StorageKey(42, UID, UID))
    items = data["draft"]["packages"][0]["items"]
    assert [(i["weight"], i["amount"]) for i in items] == [(1000, 1), (270, 2)]


async def test_rules_edit(h: Harness):
    j = h.joined
    s = await h.text("/rules")
    assert "Пуховик: пуховик" in j(s)
    await h.click("rl:edit")
    s = await h.text("Ваза: ваз = 2000 30x30x40\nПо умолчанию: 500 30x20x4, мин 8, макс 50")
    assert "Сохранено правил: 1" in j(s)
    s = await _to_packages_step(h)
    assert "pb:a:0" in h.buttons(s) and "pb:a:1" not in h.buttons(s)
    s = await h.pkgs("ваза, книга")
    assert "2.5 кг" in j(s) and "30×30×44" in j(s)
    s = await h.text("/rules")
    await h.click("rl:reset")
    assert len((await h.db.get_setting(UID, "pack_rules")) or "") == 0


async def test_cdek_box_recommendation(h: Harness):
    j = h.joined
    await _to_packages_step(h)
    s = await h.click("pb:a:5")                    # футболка 25×30×5
    assert "Подойдёт упаковка СДЭК" in j(s) and "Коробка M 33×25×15" in j(s)   # подсказка во время заполнения
    s = await h.click("pb:ok")
    assert "bx:yes" in h.buttons(s) and "Коробка M" in j(s)
    s = await h.click("bx:yes")
    assert "pk:tf:0" in h.buttons(s)
    await h.click("pk:tf:0")
    await h.click("pk:fp:0")
    await h.click("pk:tp:0")
    s = await h.text("0")
    assert "Уже добавлены коробки СДЭК: Коробка M 33×25×15 × 1" in j(s)
    await h.click("sv:ok")
    await h.text("Петров Пётр\n+79991112233")
    await h.text("Иванов\n+79990000000")
    await h.click("cm:skip")
    await h.click("sm:create")
    o = h.fake.orders[-1]
    assert o["packages"][0] | {} == {**o["packages"][0], "length": 33, "width": 25, "height": 15, "weight": 270}
    assert {"code": "CARTON_BOX_M", "parameter": "1"} in o["services"]


async def test_cdek_boxes_added_when_services_in_template(h: Harness):
    await h.db.save_template(UID, "T", {"order_type": 2, "services": {"SMS": True}})
    await h.text("📦 Новый заказ")
    await h.click("tp:use:1")
    await h.text("Моск")
    await h.text("Моск")
    await h.pkgs("0.4 15x10x8\n0.4 15x10x8", choice="bx:yes")
    data = await h.dp.storage.get_data(key=__import__("aiogram").fsm.storage.base.StorageKey(42, UID, UID))
    assert data["draft"]["services"] == {"SMS": True, "CARTON_BOX_XS": 2}


async def test_im_items_prefilled_from_packages_step(h: Harness):
    j = h.joined
    await _to_packages_step(h, "ot:1")
    await h.click("pb:a:0")                        # Пуховик
    await h.click("pb:a:5")
    await h.click("pb:a:5")                        # Футболка ×2
    s = await h.pkgs(click="pb:ok")
    assert "взял с прошлого шага" in j(s) and "1. Пуховик (1 кг/шт)" in j(s) and "2. Футболка ×2 (0.27 кг/шт)" in j(s)
    s = await h.text("1490")
    assert "Нужно 2 цен" in j(s)                   # неверное число цен — понятная ошибка
    s = await h.text("8990 1490")
    assert "pk:tf:0" in h.buttons(s)               # дальше — тариф
    data = await h.dp.storage.get_data(key=__import__("aiogram").fsm.storage.base.StorageKey(42, UID, UID))
    [p] = data["draft"]["packages"]
    assert "_things" not in p
    assert [(i["name"], i["cost"], i["weight"], i["amount"]) for i in p["items"]] == [
        ("Пуховик", 8990.0, 1000, 1), ("Футболка", 1490.0, 270, 2)]
