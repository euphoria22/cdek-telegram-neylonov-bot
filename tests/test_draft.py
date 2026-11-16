import pytest

from bot import draft as dr


def test_parse_packages():
    pkgs = dr.parse_packages("1,5 30x20x10\n0.4кг 20х15х5.2 см 3 Документы")
    assert len(pkgs) == 4
    assert pkgs[0] == {"weight": 1500, "length": 30, "width": 20, "height": 10, "comment": ""}
    assert pkgs[1]["height"] == 6 and pkgs[1]["comment"] == "Документы"
    with pytest.raises(ValueError):
        dr.parse_packages("abc")


def test_parse_items_and_person():
    items = dr.parse_items("Футболка + 1490 + 0.25 + 2 + TSH\nКружка; 590,5; 0.4")
    assert items[0]["weight"] == 250 and items[0]["amount"] == 2 and items[0]["ware_key"] == "TSH"
    assert items[1]["cost"] == 590.5 and items[1]["amount"] == 1
    p = dr.parse_person("Иванов Иван\n8 (999) 123-45-67\nООО Ромашка\nivan@example.com")
    assert p == {"name": "Иванов Иван", "phone": "+79991234567", "company": "ООО Ромашка", "email": "ivan@example.com"}
    with pytest.raises(ValueError):
        dr.parse_person("Без телефона")


def test_template_partial_flow_and_normalize():
    city = {"code": 44, "name": "Москва", "label": "Москва"}
    tpl = {"from_point": {"kind": "pvz", "code": "MSK1", "address": "x", "city_code": 44, "city": city},
           "tariff": {"code": 136, "name": "Посылка склад-склад", "mode": 4}}
    d = dr.normalize(dict(tpl))
    assert d["from_city"] == city  # город восстановлен из пункта
    assert dr.next_step(d) == "order_type"
    d["order_type"] = 2
    assert dr.next_step(d) == "to_city"
    # тариф «дверь-…» несовместим с ПВЗ отправки → пункт сбрасывается
    d["tariff"] = {"code": 139, "name": "Посылка дверь-дверь", "mode": 1}
    dr.normalize(d)
    assert "from_point" not in d
    # смена города сбрасывает пункт
    d["from_point"] = tpl["from_point"]
    d["from_city"] = {"code": 137, "name": "СПб", "label": "СПб"}
    d["tariff"] = tpl["tariff"]
    dr.normalize(d)
    assert "from_point" not in d


def test_build_order_im():
    city = {"code": 44, "name": "Москва", "label": "Москва"}
    d = {
        "order_type": 1, "number": "A1", "from_city": city, "to_city": city,
        "packages": [{"weight": 500, "length": 10, "width": 10, "height": 10, "comment": "",
                      "items": [{"name": "X", "cost": 100.0, "weight": 500, "amount": 2, "ware_key": "X1"}]}],
        "tariff": {"code": 137, "name": "", "mode": 3},
        "from_point": {"kind": "pvz", "code": "MSK1", "address": "", "city_code": 44},
        "to_point": {"kind": "door", "address": "ул. Ленина, 1", "city_code": 44},
        "cod": {"mode": "all", "delivery": 300}, "services": {"CARTON_BOX_S": 2, "SMS": True},
        "sender": {}, "recipient": {"name": "Петров", "phone": "+79990000000"}, "comment": "",
    }
    assert dr.next_step(d) is None
    o = dr.build_order(d)
    assert o["shipment_point"] == "MSK1" and o["to_location"] == {"code": 44, "address": "ул. Ленина, 1"}
    assert o["delivery_recipient_cost"] == {"value": 300}
    assert o["packages"][0]["items"][0]["payment"] == {"value": 100.0}
    assert {"code": "CARTON_BOX_S", "parameter": "2"} in o["services"] and {"code": "SMS"} in o["services"]
    assert not any(s["code"] == "INSURANCE" for s in o["services"])  # для ИМ — по стоимости товаров
    assert "sender" not in o
    calc = dr.build_calc(d)
    assert {"code": "INSURANCE", "parameter": "200.0"} in calc["services"]
