"""Справочники СДЭК: режимы доставки, тарифы, доп. услуги и упаковка.

Список услуг/тарифов можно расширять — коды берутся из документации API v2
(приложения «Тарифы» и «Дополнительные услуги»). Доступность конкретной услуги
зависит от договора: если услуга недоступна, СДЭК вернёт ошибку, и бот её покажет.
"""
from dataclasses import dataclass

# delivery_mode из калькулятора → (сторона отправки, сторона получения)
DELIVERY_MODES: dict[int, tuple[str, str]] = {
    1: ("door", "door"),
    2: ("door", "pvz"),
    3: ("pvz", "door"),
    4: ("pvz", "pvz"),
    6: ("door", "postamat"),
    7: ("pvz", "postamat"),
    8: ("postamat", "door"),
    9: ("postamat", "pvz"),
    10: ("postamat", "postamat"),
}

SIDE_EMOJI = {"door": "🚪", "pvz": "🏢", "postamat": "📮"}
SIDE_FROM_TITLE = {"door": "забор курьером от двери", "pvz": "сдача в ПВЗ", "postamat": "сдача в постамат"}
SIDE_TO_TITLE = {"door": "доставка до двери", "pvz": "до ПВЗ", "postamat": "до постамата"}
POINT_TYPE = {"pvz": "PVZ", "postamat": "POSTAMAT"}

# Тарифы для ручного ввода кода (если калькулятор не вернул список)
TARIFFS: dict[int, tuple[str, int]] = {
    136: ("Посылка склад-склад", 4),
    137: ("Посылка склад-дверь", 3),
    138: ("Посылка дверь-склад", 2),
    139: ("Посылка дверь-дверь", 1),
    366: ("Посылка дверь-постамат", 6),
    368: ("Посылка склад-постамат", 7),
    233: ("Экономичная посылка склад-дверь", 3),
    234: ("Экономичная посылка склад-склад", 4),
    378: ("Экономичная посылка склад-постамат", 7),
    291: ("CDEK Express склад-склад", 4),
    293: ("CDEK Express дверь-дверь", 1),
    294: ("CDEK Express склад-дверь", 3),
    295: ("CDEK Express дверь-склад", 2),
    480: ("Экспресс дверь-дверь", 1),
    481: ("Экспресс дверь-склад", 2),
    482: ("Экспресс склад-дверь", 3),
    483: ("Экспресс склад-склад", 4),
    485: ("Экспресс дверь-постамат", 6),
    486: ("Экспресс склад-постамат", 7),
    62: ("Магистральный экспресс склад-склад", 4),
    63: ("Магистральный супер-экспресс склад-склад", 4),
    121: ("Магистральный экспресс дверь-дверь", 1),
    122: ("Магистральный экспресс склад-дверь", 3),
    123: ("Магистральный экспресс дверь-склад", 2),
    124: ("Магистральный супер-экспресс дверь-дверь", 1),
    125: ("Магистральный супер-экспресс склад-дверь", 3),
    126: ("Магистральный супер-экспресс дверь-склад", 2),
    3: ("Супер-экспресс до 18", 1),
    7: ("Международный экспресс документы дверь-дверь", 1),
    8: ("Международный экспресс грузы дверь-дверь", 1),
}


@dataclass(frozen=True)
class Service:
    code: str
    title: str
    kind: str          # "flag" — вкл/выкл, "count" — с количеством (parameter)
    group: str         # "main" — услуги, "pack" — упаковка
    only: int | None = None  # 1 — только ИМ, 2 — только «доставка», None — оба


SERVICES: list[Service] = [
    # --- Основные услуги ---
    Service("DELIV_WEEKEND", "Доставка в выходной", "flag", "main"),
    Service("TRYING_ON", "Примерка на дому", "flag", "main", only=1),
    Service("PART_DELIV", "Частичная доставка", "flag", "main", only=1),
    Service("INSPECTION_CARGO", "Осмотр вложения", "flag", "main", only=1),
    Service("BAN_ATTACHMENT_INSPECTION", "Запрет осмотра вложения", "flag", "main", only=1),
    Service("REVERSE", "Реверс", "flag", "main"),
    Service("DANGER_CARGO", "Опасный груз", "flag", "main"),
    Service("THERMAL_MODE", "Тепловой режим", "flag", "main"),
    Service("SMS", "SMS-уведомление получателю", "flag", "main"),
    Service("PHOTO_DOCUMENT", "Фото документов", "flag", "main"),
    Service("GET_UP_FLOOR_BY_ELEVATOR", "Подъём на этаж лифтом", "flag", "main"),
    Service("GET_UP_FLOOR_BY_HAND", "Подъём вручную, этажей", "count", "main"),
    # --- Упаковка ---
    Service("CARTON_BOX_XS", "Коробка XS", "count", "pack"),
    Service("CARTON_BOX_S", "Коробка S", "count", "pack"),
    Service("CARTON_BOX_M", "Коробка M", "count", "pack"),
    Service("CARTON_BOX_L", "Коробка L", "count", "pack"),
    Service("CARTON_BOX_500GR", "Коробка 500 г", "count", "pack"),
    Service("CARTON_BOX_1KG", "Коробка 1 кг", "count", "pack"),
    Service("CARTON_BOX_2KG", "Коробка 2 кг", "count", "pack"),
    Service("CARTON_BOX_3KG", "Коробка 3 кг", "count", "pack"),
    Service("CARTON_BOX_5KG", "Коробка 5 кг", "count", "pack"),
    Service("CARTON_BOX_10KG", "Коробка 10 кг", "count", "pack"),
    Service("CARTON_BOX_15KG", "Коробка 15 кг", "count", "pack"),
    Service("CARTON_BOX_20KG", "Коробка 20 кг", "count", "pack"),
    Service("CARTON_BOX_30KG", "Коробка 30 кг", "count", "pack"),
    Service("CARTON_FILLER", "Наполнитель", "count", "pack"),
    Service("BUBBLE_WRAP", "Пузырчатая плёнка, м", "count", "pack"),
    Service("WASTE_PAPER", "Макулатурная бумага, м", "count", "pack"),
    Service("SECURE_PACKAGE_A2", "Сейф-пакет А2", "count", "pack"),
    Service("SECURE_PACKAGE_A3", "Сейф-пакет А3", "count", "pack"),
    Service("SECURE_PACKAGE_A4", "Сейф-пакет А4", "count", "pack"),
    Service("SECURE_PACKAGE_A5", "Сейф-пакет А5", "count", "pack"),
]
SERVICES_BY_CODE = {s.code: s for s in SERVICES}

# Блоки заказа, которые можно сохранять в шаблон (ключ черновика → название)
TEMPLATE_BLOCKS: list[tuple[str, str]] = [
    ("order_type", "Тип заказа"),
    ("from_city", "Город отправки"),
    ("from_point", "ПВЗ / адрес отправки"),
    ("to_city", "Город получения"),
    ("to_point", "ПВЗ / адрес получения"),
    ("packages", "Грузовые места"),
    ("tariff", "Тариф"),
    ("declared_value", "Объявленная стоимость (страховка)"),
    ("cod", "Оплата получателем / наценка"),
    ("services", "Доп. услуги и упаковка"),
    ("sender", "Отправитель"),
    ("recipient", "Получатель"),
    ("comment", "Комментарий"),
]
TEMPLATE_DEFAULT = {"order_type", "from_city", "from_point", "tariff", "services", "sender"}


@dataclass(frozen=True)
class Box:
    code: str      # код доп. услуги СДЭК
    title: str
    length: int    # см
    width: int
    height: int
    max_weight: int  # г


# Фирменные коробки СДЭК для рекомендации. Размеры — из документации API (XS) и руководства СДЭК
# по упаковке 2021 г. / сайтов СДЭК; перед массовым использованием сверьте с актуальными в вашем ПВЗ.
# Коробки 500 г и 15 кг не рекомендуются: их размеры не удалось подтвердить (заказать вручную можно).
BOXES: list[Box] = [
    Box("CARTON_BOX_XS", "Коробка XS", 17, 12, 9, 500),
    Box("CARTON_BOX_1KG", "Коробка 1 кг", 24, 17, 10, 1000),
    Box("CARTON_BOX_S", "Коробка S", 23, 19, 10, 2000),
    Box("CARTON_BOX_2KG", "Коробка 2 кг", 34, 24, 10, 2000),
    Box("CARTON_BOX_3KG", "Коробка 3 кг", 24, 24, 21, 3000),
    Box("CARTON_BOX_M", "Коробка M", 33, 25, 15, 5000),
    Box("CARTON_BOX_5KG", "Коробка 5 кг", 40, 24, 21, 5000),
    Box("CARTON_BOX_L", "Коробка L", 31, 25, 38, 12000),
    Box("CARTON_BOX_10KG", "Коробка 10 кг", 40, 35, 28, 10000),
    Box("CARTON_BOX_20KG", "Коробка 20 кг", 47, 40, 43, 20000),
    Box("CARTON_BOX_30KG", "Коробка 30 кг", 69, 39, 42, 30000),
]
BOXES_BY_CODE = {b.code: b for b in BOXES}

# в названиях услуг-коробок показываем размеры
SERVICES[:] = [Service(x.code, f"{x.title} {BOXES_BY_CODE[x.code].length}×{BOXES_BY_CODE[x.code].width}×"
                       f"{BOXES_BY_CODE[x.code].height}", x.kind, x.group, x.only)
               if x.code in BOXES_BY_CODE else x for x in SERVICES]
SERVICES_BY_CODE.update({x.code: x for x in SERVICES})
