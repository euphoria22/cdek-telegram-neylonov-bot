import pytest

from bot import packing as pk

R = pk.parse_rules(pk.DEFAULT_RULES_TEXT)


def test_rules_roundtrip_and_match():
    assert len(R.rules) == 9 and (R.default_weight, R.box_length, R.box_width, R.item_height) == (700, 35, 25, 5)
    assert pk.parse_rules(pk.rules_to_text(R)) == R
    assert R.match("Дублёнка овечья").label == "Пуховик"
    assert R.match("Куртки зимние").label == "Куртка"
    assert R.match("Ваза") is None
    with pytest.raises(ValueError):
        pk.parse_rules("куртк 700 37x35x10")


def test_parse_names():
    assert pk.parse_names("пуховик, футболка 2\n3 шт носки, Кроссовки Nike x2") == [
        ("пуховик", 1), ("футболка", 2), ("носки", 3), ("Кроссовки Nike", 2)]


def test_pack_stack_like_site():
    # пуховик 55×41×15 + футболка 25×30×5 → 55×41×20, 1270 г
    [p] = pk.pack(R, [("пуховик", 1), ("футболка", 1)])
    assert (p["weight"], p["length"], p["width"], p["height"]) == (1270, 55, 41, 20)
    assert p["comment"] == "Пуховик, Футболка"
    [p] = pk.pack(R, [("ваза", 1)])  # по умолчанию, высота не меньше минимума
    assert (p["weight"], p["length"], p["width"], p["height"]) == (700, 35, 25, 10)
    pkgs = pk.pack(R, [("рюкзак", 4)])  # 4×20 = 80 > 60 → два места
    assert [p["height"] for p in pkgs] == [60, 20] and sum(p["weight"] for p in pkgs) == 6000


@pytest.mark.parametrize("pkg,code", [
    (dict(weight=400, length=15, width=10, height=8), "CARTON_BOX_XS"),
    (dict(weight=270, length=25, width=30, height=5), "CARTON_BOX_M"),
    (dict(weight=1500, length=10, width=30, height=20), "CARTON_BOX_2KG"),   # поворот: 30×20×10
    (dict(weight=2500, length=30, width=30, height=44), "CARTON_BOX_20KG"),
    (dict(weight=40000, length=10, width=10, height=10), None),             # тяжелее 30 кг
    (dict(weight=1000, length=80, width=10, height=10), None),              # длиннее любой коробки
])
def test_recommend_box(pkg, code):
    b = pk.recommend_box(pkg)
    assert (b.code if b else None) == code
