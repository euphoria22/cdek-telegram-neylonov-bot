"""Черновик заказа: порядок шагов, разбор ввода, сводка и сборка запросов к API.

Черновик — обычный dict. Ключ присутствует ⇔ шаг заполнен (значение может быть
пустым, например comment="" или services={}). Благодаря этому шаблон — это
просто подмножество ключей черновика, а мастер спрашивает только недостающее.
"""
import html
import math
import re

from .catalog import DELIVERY_MODES, SERVICES_BY_CODE, SIDE_EMOJI, SIDE_FROM_TITLE, SIDE_TO_TITLE

STEPS = [
    "order_type", "number", "from_city", "to_city", "packages", "tariff",
    "from_point", "to_point", "declared_value", "cod", "services",
    "sender", "recipient", "comment",
]

# Кнопки редактирования в сводке → какие ключи очистить
EDIT_BLOCKS: dict[str, tuple[str, list[str]]] = {
    "type": ("Тип", ["order_type"]),
    "num": ("№ заказа", ["number"]),
    "fc": ("Город отпр.", ["from_city", "from_point"]),
    "fp": ("ПВЗ/адрес отпр.", ["from_point"]),
    "tc": ("Город получ.", ["to_city", "to_point"]),
    "tp": ("ПВЗ/адрес получ.", ["to_point"]),
    "pkg": ("Места", ["packages"]),
    "tf": ("Тариф", ["tariff"]),
    "dv": ("Страховка", ["declared_value"]),
    "cod": ("Оплата получат.", ["cod"]),
    "sv": ("Услуги/упаковка", ["services"]),
    "snd": ("Отправитель", ["sender"]),
    "rcp": ("Получатель", ["recipient"]),
    "cm": ("Комментарий", ["comment"]),
}

e = html.escape


# ---------------------------------------------------------------- шаги
def step_applicable(step: str, d: dict) -> bool:
    t = d.get("order_type")
    if step in ("number", "cod"):
        return t == 1
    if step == "declared_value":
        return t == 2
    return True


def next_step(d: dict) -> str | None:
    for s in STEPS:
        if step_applicable(s, d) and s not in d:
            return s
    return None


def sides(d: dict) -> tuple[str | None, str | None]:
    t = d.get("tariff")
    return DELIVERY_MODES.get(t["mode"], (None, None)) if t else (None, None)


def normalize(d: dict) -> dict:
    """Приводит черновик в согласованное состояние (после шаблона/редактирования)."""
    t = d.get("order_type")
    if t == 2:
        d.pop("number", None)
        d.pop("cod", None)
    if t == 1:
        d.pop("declared_value", None)
        if "packages" in d and not all(p.get("items") for p in d["packages"]):
            d.pop("packages")  # для ИМ нужны товары в каждом месте
    for side in ("from", "to"):
        point = d.get(f"{side}_point")
        if not point:
            continue
        if f"{side}_city" not in d and point.get("city"):
            d[f"{side}_city"] = point["city"]  # город восстанавливаем из пункта
        side_kind = sides(d)[0 if side == "from" else 1]
        city = d.get(f"{side}_city")
        if (side_kind and point["kind"] != side_kind) or (city and point.get("city_code") != city["code"]):
            d.pop(f"{side}_point")
    if "services" in d and t:
        d["services"] = {c: v for c, v in d["services"].items()
                         if c not in SERVICES_BY_CODE or SERVICES_BY_CODE[c].only in (None, t)}
    return d


def point_matches_tariff_mode(d: dict, mode: int) -> bool:
    fs, ts = DELIVERY_MODES.get(mode, (None, None))
    if fs is None:
        return False
    fp, tp = d.get("from_point"), d.get("to_point")
    return (not fp or fp["kind"] == fs) and (not tp or tp["kind"] == ts)


# ---------------------------------------------------------------- разбор ввода
NUM = r"\d+(?:[.,]\d+)?"
PKG_RE = re.compile(
    rf"^\s*({NUM})\s*(?:кг|kg)?\s+({NUM})\s*[xх×*]\s*({NUM})\s*[xх×*]\s*({NUM})\s*(?:см|cm)?"
    rf"(?:\s+(\d+)\s*(?:шт|мест[а]?|м)?\.?)?\s*(.*)$", re.I)


def to_float(s: str) -> float:
    return float(s.replace(",", ".").replace(" ", ""))


def parse_packages(text: str) -> list[dict]:
    pkgs: list[dict] = []
    for n, line in enumerate((l for l in text.splitlines() if l.strip()), 1):
        m = PKG_RE.match(line)
        if not m:
            raise ValueError(f"Строка {n}: не понял «{line.strip()}». Формат: 1.5 30x20x10")
        w, l, wd, h, count, comment = m.groups()
        weight = round(to_float(w) * 1000)
        dims = [math.ceil(to_float(x)) for x in (l, wd, h)]
        if weight <= 0 or min(dims) <= 0:
            raise ValueError(f"Строка {n}: вес и габариты должны быть больше нуля")
        if weight > 1_000_000:
            raise ValueError(f"Строка {n}: слишком большой вес")
        for _ in range(int(count or 1)):
            pkgs.append({"weight": weight, "length": dims[0], "width": dims[1], "height": dims[2],
                         "comment": comment.strip()})
    if not pkgs:
        raise ValueError("Не указано ни одного места")
    if len(pkgs) > 50:
        raise ValueError("Слишком много мест (максимум 50)")
    return pkgs


def parse_items(text: str, weight_for=None) -> list[dict]:
    """Строка: Название + цена + [вес кг] + [кол-во] + [артикул] (разделитель «+», также принимается «;»).
    Если вес не указан — weight_for(name) (правила по названию товара)."""
    items = []
    for n, line in enumerate((l for l in text.splitlines() if l.strip()), 1):
        parts = [p.strip() for p in re.split(r"[+;]", line)]
        if len(parts) < 2 or not parts[0]:
            raise ValueError(f"Строка {n}: нужно минимум «Название + цена»")
        try:
            cost = to_float(parts[1])
            if len(parts) > 2 and parts[2]:
                weight = round(to_float(parts[2]) * 1000)
            elif weight_for:
                weight = weight_for(parts[0])
            else:
                raise ValueError(f"Строка {n}: укажите вес")
            amount = int(parts[3]) if len(parts) > 3 and parts[3] else 1
        except ValueError:
            raise ValueError(f"Строка {n}: цена, вес и количество должны быть числами")
        if cost < 0 or weight <= 0 or amount <= 0:
            raise ValueError(f"Строка {n}: проверьте цену/вес/количество")
        article = parts[4] if len(parts) > 4 and parts[4] else f"ITEM{n}"
        items.append({"name": parts[0][:255], "cost": cost, "weight": weight, "amount": amount, "ware_key": article[:50]})
    if not items:
        raise ValueError("Не указано ни одного товара")
    return items


def normalize_phone(s: str) -> str:
    digits = re.sub(r"\D", "", s)
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if not 10 <= len(digits) <= 15:
        raise ValueError("Некорректный телефон")
    return "+" + digits


def parse_person(text: str) -> dict:
    """Строки в любом порядке: ФИО, телефон, [email], [компания]."""
    lines = [l.strip() for l in re.split(r"[\n;]", text) if l.strip()]
    person: dict = {}
    others = []
    for l in lines:
        if "@" in l and "email" not in person:
            person["email"] = l
        elif "phone" not in person and re.fullmatch(r"[+\d\s()\-]{7,}", l):
            person["phone"] = normalize_phone(l)
        else:
            others.append(l)
    if not others:
        raise ValueError("Не указано ФИО")
    if "phone" not in person:
        raise ValueError("Не указан телефон")
    person["name"] = others[0][:255]
    if len(others) > 1:
        person["company"] = others[1][:255]
    return person


def parse_money(text: str) -> float:
    v = to_float(re.sub(r"[^\d.,]", "", text) or "x")
    if v < 0:
        raise ValueError
    return v


# ---------------------------------------------------------------- расчёты
def items_total(d: dict) -> float:
    return sum(i["cost"] * i["amount"] for p in d.get("packages", []) for i in p.get("items", []))


def declared_total(d: dict) -> float:
    return items_total(d) if d.get("order_type") == 1 else float(d.get("declared_value") or 0)


def fmt_money(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.2f}".replace(",", " ").replace(".00", "") + " ₽"


def fmt_kg(grams: int) -> str:
    return f"{grams / 1000:g}"


def services_list(d: dict, for_calc: bool = False) -> list[dict]:
    res = []
    for code, val in (d.get("services") or {}).items():
        item = {"code": code}
        if val is not None and val is not True:
            item["parameter"] = str(val)
        res.append(item)
    dv = declared_total(d)
    if dv > 0 and (d.get("order_type") == 2 or for_calc):
        res.append({"code": "INSURANCE", "parameter": str(round(dv, 2))})
    return res


def _location(d: dict, side: str) -> dict:
    loc = {"code": d[f"{side}_city"]["code"]}
    p = d.get(f"{side}_point")
    if p and p["kind"] == "door":
        loc["address"] = p["address"]
    return loc


def build_calc(d: dict) -> dict:
    payload = {
        "type": d["order_type"],
        "tariff_code": d["tariff"]["code"],
        "from_location": _location(d, "from"),
        "to_location": _location(d, "to"),
        "packages": [{k: p[k] for k in ("weight", "length", "width", "height")} for p in d["packages"]],
    }
    services = services_list(d, for_calc=True)
    if services:
        payload["services"] = services
    return payload


def build_tarifflist(d: dict) -> dict:
    return {
        "type": d["order_type"],
        "from_location": {"code": d["from_city"]["code"]},
        "to_location": {"code": d["to_city"]["code"]},
        "packages": [{k: p[k] for k in ("weight", "length", "width", "height")} for p in d["packages"]],
    }


def _person(p: dict) -> dict:
    out = {"name": p["name"], "phones": [{"number": p["phone"]}]}
    for k in ("company", "email"):
        if p.get(k):
            out[k] = p[k]
    return out


def build_order(d: dict, calc: dict | None = None) -> dict:
    t = d["order_type"]
    cod = d.get("cod") or {}
    order: dict = {"type": t, "tariff_code": d["tariff"]["code"], "recipient": _person(d["recipient"])}
    if t == 1:
        order["number"] = d["number"]
        delivery = recipient_delivery_cost(d, calc)
        if delivery > 0:
            order["delivery_recipient_cost"] = {"value": delivery}
    if d.get("comment"):
        order["comment"] = d["comment"][:255]
    if d.get("sender"):
        order["sender"] = _person(d["sender"])

    fp, tp = d["from_point"], d["to_point"]
    if fp["kind"] == "door":
        order["from_location"] = {"code": d["from_city"]["code"], "address": fp["address"]}
    else:
        order["shipment_point"] = fp["code"]
    if tp["kind"] == "door":
        order["to_location"] = {"code": d["to_city"]["code"], "address": tp["address"]}
    else:
        order["delivery_point"] = tp["code"]

    packages = []
    for n, p in enumerate(d["packages"], 1):
        pkg = {"number": str(n), **{k: p[k] for k in ("weight", "length", "width", "height")}}
        if p.get("comment") or t == 2:
            pkg["comment"] = (p.get("comment") or "Груз")[:255]
        if t == 1:
            pay = cod.get("mode") in COD_PAYS_ITEMS
            pkg["items"] = [{
                "name": i["name"], "ware_key": i["ware_key"], "cost": i["cost"], "weight": i["weight"],
                "amount": i["amount"], "payment": {"value": i["cost"] if pay else 0},
            } for i in p["items"]]
        packages.append(pkg)
    order["packages"] = packages

    services = services_list(d)
    if services:
        order["services"] = services
    return order


# ---------------------------------------------------------------- отображение
def point_str(p: dict | None) -> str:
    if not p:
        return "—"
    if p["kind"] == "door":
        return f"🚪 {e(p['address'])}"
    return f"{SIDE_EMOJI[p['kind']]} <code>{e(p['code'])}</code> {e(p.get('address', ''))}"


def person_str(p: dict | None) -> str:
    if p is None:
        return "—"
    if not p:
        return "из договора"
    parts = [e(p["name"]), e(p["phone"])]
    if p.get("company"):
        parts.append(e(p["company"]))
    if p.get("email"):
        parts.append(e(p["email"]))
    return ", ".join(parts)


def services_str(services: dict | None) -> str:
    if not services:
        return "нет"
    out = []
    for code, val in services.items():
        title = SERVICES_BY_CODE[code].title if code in SERVICES_BY_CODE else code
        out.append(title if val is None or val is True else f"{title} × {val}")
    return ", ".join(out)


def tariff_str(t: dict | None) -> str:
    if not t:
        return "—"
    fs, ts = DELIVERY_MODES.get(t["mode"], ("?", "?"))
    return f"{t['code']} · {e(t['name'])} ({SIDE_FROM_TITLE.get(fs, fs)} → {SIDE_TO_TITLE.get(ts, ts)})"


def describe(d: dict, keys=None) -> list[str]:
    """Краткое описание заполненных блоков (для шаблонов)."""
    t = d.get("order_type")
    lines = []
    show = (lambda k: k in d and (keys is None or k in keys))
    if show("order_type"):
        lines.append(f"Тип: {'интернет-магазин' if t == 1 else 'доставка'}")
    if show("from_city"):
        lines.append(f"Откуда: {e(d['from_city']['label'])}")
    if show("from_point"):
        lines.append(f"Пункт отправки: {point_str(d['from_point'])}")
    if show("to_city"):
        lines.append(f"Куда: {e(d['to_city']['label'])}")
    if show("to_point"):
        lines.append(f"Пункт получения: {point_str(d['to_point'])}")
    if show("packages"):
        lines.append("Места: " + "; ".join(
            f"{fmt_kg(p['weight'])} кг {p['length']}×{p['width']}×{p['height']}" for p in d["packages"]))
    if show("tariff"):
        lines.append(f"Тариф: {tariff_str(d['tariff'])}")
    if show("declared_value"):
        lines.append(f"Объявл. стоимость: {fmt_money(d['declared_value'])}")
    if show("cod"):
        lines.append(f"Наложка: {cod_str(d['cod'])}")
    if show("services"):
        lines.append(f"Услуги: {services_str(d['services'])}")
    if show("sender"):
        lines.append(f"Отправитель: {person_str(d['sender'])}")
    if show("recipient"):
        lines.append(f"Получатель: {person_str(d['recipient'])}")
    if show("comment"):
        lines.append(f"Комментарий: {e(d['comment']) or '—'}")
    return lines


COD_PAYS_ITEMS = ("items", "all")
COD_PAYS_DELIVERY = ("all", "delivery")


def delivery_rule_str(cod: dict) -> str:
    if cod.get("markup") is not None:
        return f"по тарифу +{cod['markup']:g}%"
    return fmt_money(cod.get("delivery", 0))


def cod_str(cod: dict | None) -> str:
    """Что платит получатель (только для ИМ)."""
    if not cod or cod.get("mode", "none") == "none":
        return "ничего (всё оплачивает отправитель)"
    parts = []
    if cod["mode"] in COD_PAYS_ITEMS:
        parts.append("товары")
    if cod["mode"] in COD_PAYS_DELIVERY:
        parts.append(f"доставка {delivery_rule_str(cod)}")
    return " + ".join(parts)


def recipient_delivery_cost(d: dict, calc: dict | None) -> float:
    """Сумма за доставку, которую СДЭК возьмёт с получателя (delivery_recipient_cost)."""
    cod = d.get("cod") or {}
    if d.get("order_type") != 1 or cod.get("mode") not in COD_PAYS_DELIVERY:
        return 0
    if cod.get("markup") is not None:
        if not calc or calc.get("delivery_sum") is None:
            raise ValueError("Нет расчёта тарифа — не из чего посчитать наценку")
        return float(math.ceil(round(calc["delivery_sum"] * (1 + cod["markup"] / 100), 2)))
    return float(cod.get("delivery") or 0)


def summary_text(d: dict, calc: dict | None = None, calc_err: str | None = None) -> str:
    t = d["order_type"]
    L = ["<b>🧾 Проверьте заказ</b>", ""]
    L.append(f"<b>Тип:</b> {'Интернет-магазин' if t == 1 else 'Доставка'}"
             + (f" · № <code>{e(d['number'])}</code>" if d.get("number") else ""))
    L.append(f"<b>Откуда:</b> {e(d['from_city']['label'])}\n   {point_str(d.get('from_point'))}")
    L.append(f"<b>Куда:</b> {e(d['to_city']['label'])}\n   {point_str(d.get('to_point'))}")
    L.append(f"<b>Тариф:</b> {tariff_str(d.get('tariff'))}")
    L.append("<b>Места:</b>")
    for n, p in enumerate(d["packages"], 1):
        line = f"   {n}. {fmt_kg(p['weight'])} кг, {p['length']}×{p['width']}×{p['height']} см"
        if p.get("comment"):
            line += f" — {e(p['comment'])}"
        L.append(line)
        for i in p.get("items", []):
            L.append(f"      • {e(i['name'])} ×{i['amount']} по {fmt_money(i['cost'])} ({fmt_kg(i['weight'])} кг)")
    if t == 1:
        L.append(f"<b>Стоимость товаров (страховка):</b> {fmt_money(items_total(d))}")
        L.append(f"<b>Оплата получателем:</b> {cod_str(d.get('cod'))}")
    else:
        dv = d.get("declared_value") or 0
        L.append(f"<b>Страховка:</b> {'объявленная стоимость ' + fmt_money(dv) if dv else 'нет'}")
    L.append(f"<b>Доп. услуги:</b> {services_str(d.get('services'))}")
    L.append(f"<b>Отправитель:</b> {person_str(d.get('sender'))}")
    L.append(f"<b>Получатель:</b> {person_str(d.get('recipient'))}")
    if d.get("comment"):
        L.append(f"<b>Комментарий:</b> {e(d['comment'])}")
    L.append("")
    if calc:
        L.append(f"💰 <b>Доставка:</b> {fmt_money(calc.get('delivery_sum'))}")
        for s in calc.get("services") or []:
            title = SERVICES_BY_CODE[s["code"]].title if s.get("code") in SERVICES_BY_CODE else (
                "Страховка" if s.get("code") == "INSURANCE" else s.get("code"))
            L.append(f"   + {e(str(title))}: {fmt_money(s.get('sum'))}")
        L.append(f"💳 <b>Итого:</b> {fmt_money(calc.get('total_sum'))}")
        pmin, pmax = calc.get("period_min"), calc.get("period_max")
        if pmin is not None:
            L.append(f"⏱ Срок: {pmin}–{pmax} раб. дн.")
    elif calc_err:
        L.append(f"⚠️ Расчёт стоимости не удался: {e(calc_err)}")
    cod = d.get("cod") or {}
    if t == 1 and cod.get("mode", "none") != "none":
        goods = items_total(d) if cod["mode"] in COD_PAYS_ITEMS else 0
        try:
            delivery = recipient_delivery_cost(d, calc)
        except ValueError:
            L.append("⚠️ Наценка будет рассчитана, когда удастся получить стоимость тарифа.")
        else:
            parts = []
            if goods:
                parts.append(f"товары {fmt_money(goods)}")
            if cod["mode"] in COD_PAYS_DELIVERY:
                rule = f" (тариф {fmt_money(calc.get('delivery_sum'))} +{cod['markup']:g}%)" if cod.get("markup") is not None else ""
                parts.append(f"доставка {fmt_money(delivery)}{rule}")
            L.append(f"📥 <b>Взять с получателя:</b> {' + '.join(parts)} = <b>{fmt_money(goods + delivery)}</b>")
            if calc and cod["mode"] in COD_PAYS_DELIVERY:
                L.append(f"📈 Ваша маржа на доставке: {fmt_money(delivery - (calc.get('delivery_sum') or 0))}")
    return "\n".join(L)


def order_title(d: dict) -> str:
    rcp = (d.get("recipient") or {}).get("name", "")
    return f"{d['from_city']['name']} → {d['to_city']['name']}, {rcp}"[:120]


# ---------------------------------------------------------------- быстрый запрос «получатель одним сообщением»
PHONE_RE = re.compile(r"(?:\+7|8|7)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
NAME_RE = re.compile(r"[А-ЯЁA-Z][а-яёa-zА-ЯЁA-Z\-]+(?:\s+[А-ЯЁA-Z][а-яёa-zА-ЯЁA-Z\-.]*){1,3}")
ADDR_HINT_RE = re.compile(r"(?:^|\s)(?:ул|улица|пр|просп|проспект|пер|наб|ш|шоссе|б-р|бульвар|пл|д|дом|кв|пом|г)\.?\s", re.I)
LIST_NUM_RE = re.compile(r"^\s*\d{1,2}\s*[.)]\s*")
REGION_RE = re.compile(r"обл\b|область|край\b|респ\b|республика|россия|\bао\b|район|р-н", re.I)
CITY_PREFIX_RE = re.compile(r"^(?:город|гор\.|г\.|г(?=\s)|пгт\.?(?=\s|$)|посёлок|поселок|пос\.|п\.|село|с\.|деревня|дер\.|д\.(?=\s*[А-ЯЁ]))\s*", re.I)
STREET_STOP = {"улица", "проспект", "просп", "переулок", "пер", "набережная", "наб", "шоссе", "бульвар", "площадь",
               "дом", "корпус", "корп", "строение", "стр", "лит", "литера", "проезд", "тупик", "линия"}
POINT_CODE_RE = re.compile(r"\b[A-Z]{2,5}\d{1,5}\b")


def split_address(addr: str) -> tuple[str | None, str]:
    """Выделяет город из адреса, где бы он ни стоял:
    «190000, Санкт-Петербург, ул. Х, 5» / «Улица Зорге, 191, город Новосибирск» / «ул. Х, 5, Омск»."""
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    parts = [p for p in parts if not re.fullmatch(r"\d{6}", p) and not REGION_RE.search(p)]

    def is_street(p: str) -> bool:
        return bool(re.search(r"\d", p) or ADDR_HINT_RE.search(" " + p + " "))

    city_idx = next((i for i, p in enumerate(parts) if CITY_PREFIX_RE.match(p)), None)  # «г. Омск», «город Омск»
    if city_idx is None and parts and not is_street(parts[0]) and len(parts) > 1:
        city_idx = 0                                                                      # город в начале
    if city_idx is None and len(parts) > 1 and not is_street(parts[-1]) and any(map(is_street, parts[:-1])):
        city_idx = len(parts) - 1                                                         # город в конце
    if city_idx is None:
        return None, ", ".join(parts) or addr.strip()
    city = CITY_PREFIX_RE.sub("", parts[city_idx]).strip()
    return city or None, ", ".join(p for i, p in enumerate(parts) if i != city_idx)


def parse_quick_request(text: str) -> dict:
    """Получатель одним сообщением: ФИО, адрес (ПВЗ или для курьера), телефон — в любом порядке."""
    phone = email = name = addr = None
    for raw in text.splitlines():
        line = LIST_NUM_RE.sub("", raw).strip()
        if not line:
            continue
        if phone is None:
            m = PHONE_RE.search(line)
            if m:
                phone = normalize_phone(m.group())
                line = re.sub(r"\(.*?\)", "", line[:m.start()] + line[m.end():]).strip(" ,;-–")
                if not line:
                    continue
        if email is None and re.fullmatch(r"\S+@\S+\.\S+", line):
            email = line
        elif addr is None and ("," in line or ADDR_HINT_RE.search(" " + line + " ")):
            addr = line
        elif name is None and NAME_RE.fullmatch(line):
            name = line
    missing = [t for t, v in (("ФИО", name), ("телефон", phone), ("адрес", addr)) if not v]
    if missing:
        raise ValueError("не нашёл: " + ", ".join(missing))
    city, street = split_address(addr)
    person = {"name": name[:255], "phone": phone}
    if email:
        person["email"] = email
    return {"recipient": person, "city": city, "address": street or addr, "full_address": addr}


def street_key(address: str) -> tuple[list[str], str | None]:
    """Слова названия улицы и номер дома: «ул. Адмирала Трибуца, 5, пом. 37-Н» → (["адмирала","трибуца"], "5")."""
    main = re.split(r",\s*(?:кв|пом|оф|офис|помещение|квартира|под|подъезд|эт|этаж)\b", address, flags=re.I)[0]
    segs = [s.strip() for s in main.split(",") if s.strip()]
    words, house, street_idx = [], None, None
    for i, s in enumerate(segs):
        w = [x for x in re.findall(r"[а-яёa-z]+", s.lower()) if len(x) >= 3 and x not in STREET_STOP]
        if w:
            words, street_idx = w, i
            break
    if street_idx is not None:
        for s in segs[street_idx + 1:]:
            m = re.search(r"\d+\s*[а-яa-z]?(?:/\d+)?", s.lower())
            if m:
                house = m.group().replace(" ", "")
                break
        if house is None:
            nums = re.findall(r"\d+[а-яa-z]?", segs[street_idx].lower())
            house = nums[-1] if nums else None
    return words, house


def match_points(items: list[dict], address: str) -> tuple[list[dict], list[dict]]:
    """Пункты, совпадающие с адресом: (точно — улица и дом, на той же улице)."""
    code = POINT_CODE_RE.search(address.upper())
    if code:
        exact = [it for it in items if it["code"].upper() == code.group()]
        if exact:
            return exact, exact
    words, house = street_key(address)
    if not words:
        return [], []
    street = [it for it in items if all(w in it["address"].lower().replace("ё", "е") for w in
                                        (x.replace("ё", "е") for x in words))]
    exact = []
    if house:
        rx = re.compile(rf"(?<![\d]){re.escape(house)}(?![\dа-яa-z])")
        exact = [it for it in street if rx.search(it["address"].lower().replace(" ", ""))
                 or rx.search(it["address"].lower())]
    return exact, street
