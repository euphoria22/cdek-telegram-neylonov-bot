"""Вес и габариты по названию товара: правила и укладка вещей стопкой в коробки.

Правило: слова — начала слов в названии («куртк» подходит к «Куртка» и «Куртки»),
вес одной вещи и её габариты в упаковке. Правила проверяются сверху вниз, срабатывает первое.
Несколько вещей кладутся стопкой: длина и ширина — по самой большой, высоты складываются.
Вещь без правила — коробка по умолчанию (Д×Ш, высота на 1 вещь, вес по умолчанию).
Высота места не меньше «мин.» и не больше «макс.»: что не влезло — в следующее место.
"""
import re
from dataclasses import dataclass, field

from .catalog import BOXES, Box

DEFAULT_RULES_TEXT = """\
Пуховик: пуховик, парка, пальто, дублёнк, дубленк = 1000 55x41x15
Куртка: куртк, бомбер, ветровк, жилет, анорак = 700 37x35x10
Обувь: кроссовк, кед, ботин, сапог, туфл, лофер, сандал, шлёп, шлеп, эспадрил, обув = 900 35x24x12
Худи/свитер: худи, свитшот, свитер, кардиган, толстовк, лонгслив = 950 41x33x7
Джинсы/брюки: джинс, брюк, штан, джоггер = 850 30x37x5
Футболка/рубашка: рубашк, поло, футболк, майк, топ = 270 25x30x5
Шорты: шорт = 600 39x40x7
Рюкзак/сумка: рюкзак, сумк = 1500 47x35x20
Аксессуары: шапк, кепк, бейсболк, панам, балаклав, шарф, перчат, носк, трус, ремен, ремн, кошел, картхолдер = 150 27x25x7
По умолчанию: 700 35x25x5, мин 10, макс 60"""


@dataclass
class Rule:
    label: str
    keywords: list[str]
    weight: int  # г
    length: int
    width: int
    height: int


@dataclass
class Rules:
    rules: list[Rule] = field(default_factory=list)
    default_weight: int = 700
    box_length: int = 35
    box_width: int = 25
    item_height: int = 5
    min_height: int = 10
    max_height: int = 60

    def match(self, name: str) -> Rule | None:
        words = re.findall(r"[а-яёa-z]+", name.lower().replace("ё", "е"))
        for r in self.rules:
            for kw in r.keywords:
                kw = kw.replace("ё", "е")
                if any(w.startswith(kw) for w in words):
                    return r
        return None

    def item_dims(self, name: str) -> tuple[int, int, int, int, str]:
        """(вес г, Д, Ш, В, подпись) для одной вещи."""
        r = self.match(name)
        if r:
            return r.weight, r.length, r.width, r.height, r.label
        return self.default_weight, self.box_length, self.box_width, self.item_height, ""


DIMS_RE = r"(\d+)\s*[xх×*]\s*(\d+)\s*[xх×*]\s*(\d+)"


def parse_rules(text: str) -> Rules:
    res = Rules()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("по умолчанию"):
            m = re.search(rf"(\d+)\s*(?:г\s*)?{DIMS_RE}", line)
            if not m:
                raise ValueError(f"Строка {n}: «По умолчанию: вес ДxШxВысота_на_1_вещь, мин N, макс N»")
            res.default_weight, res.box_length, res.box_width, res.item_height = map(int, m.groups())
            if mn := re.search(r"мин\D*(\d+)", line, re.I):
                res.min_height = int(mn.group(1))
            if mx := re.search(r"макс\D*(\d+)", line, re.I):
                res.max_height = int(mx.group(1))
            continue
        if "=" not in line:
            raise ValueError(f"Строка {n}: нет «=» (формат: Название: слова = вес ДxШxВ)")
        left, right = line.rsplit("=", 1)
        m = re.fullmatch(rf"\s*(\d+)\s*(?:г)?\s+{DIMS_RE}\s*", right)
        if not m:
            raise ValueError(f"Строка {n}: после «=» нужно «вес_г ДxШxВ», например 700 37x35x10")
        label, _, words = left.partition(":") if ":" in left else ("", "", left)
        keywords = [w.strip().lower() for w in words.split(",") if w.strip()]
        if not keywords:
            raise ValueError(f"Строка {n}: не указаны слова")
        w, l, wd, h = map(int, m.groups())
        res.rules.append(Rule(label.strip() or keywords[0].capitalize(), keywords, w, l, wd, h))
    if min(res.box_length, res.box_width, res.item_height, res.default_weight) <= 0 or res.max_height < res.min_height:
        raise ValueError("Проверьте значения по умолчанию")
    return res


def rules_to_text(r: Rules) -> str:
    lines = [f"{x.label}: {', '.join(x.keywords)} = {x.weight} {x.length}x{x.width}x{x.height}" for x in r.rules]
    lines.append(f"По умолчанию: {r.default_weight} {r.box_length}x{r.box_width}x{r.item_height}, "
                 f"мин {r.min_height}, макс {r.max_height}")
    return "\n".join(lines)


QTY_RE = re.compile(r"^\s*(?:(\d+)\s*(?:шт\.?|x|х|×)?\s+)?(.*?)(?:\s*(?:[xх×*]\s*)?(\d+)\s*(?:шт\.?)?)?\s*$", re.I)


def parse_names(text: str) -> list[tuple[str, int]]:
    """«пуховик, футболка 2, 3 шт носки» → [("пуховик",1), ("футболка",2), ("носки",3)]."""
    out = []
    for chunk in re.split(r"[\n,;]+", text):
        if not chunk.strip():
            continue
        m = QTY_RE.match(chunk)
        lead, name, tail = m.groups()
        name = name.strip(" -–:")
        if not re.search(r"[а-яёa-z]", name, re.I):
            raise ValueError(f"Не понял «{chunk.strip()}»")
        qty = int(lead or tail or 1)
        if not 1 <= qty <= 200:
            raise ValueError(f"Странное количество в «{chunk.strip()}»")
        out.append((name, qty))
    if not out:
        raise ValueError("Не указано ни одной вещи")
    return out


def pack(rules: Rules, names: list[tuple[str, int]]) -> list[dict]:
    """Укладывает вещи стопкой в места. Возвращает места в формате черновика (вес в г, см)."""
    things = []
    for name, qty in names:
        w, l, wd, h, _ = rules.item_dims(name)
        things += [(w, l, wd, h, name.strip()[:1].upper() + name.strip()[1:])] * qty
    things.sort(key=lambda t: -(t[1] * t[2]))  # крупные снизу
    packages: list[dict] = []
    cur: list = []

    def flush():
        if not cur:
            return
        height = max(rules.min_height, sum(t[3] for t in cur))
        counts: dict[str, int] = {}
        weights: dict[str, int] = {}
        for t in cur:
            counts[t[4]] = counts.get(t[4], 0) + 1
            weights[t[4]] = t[0]
        packages.append({
            "weight": sum(t[0] for t in cur),
            "length": max(t[1] for t in cur), "width": max(t[2] for t in cur),
            "height": min(height, max(rules.max_height, max(t[3] for t in cur))),
            "comment": ", ".join(f"{k} ×{v}" if v > 1 else k for k, v in counts.items())[:255],
            # что лежит в месте — для шага «Товары» (ИМ): останется спросить только цены
            "_things": [{"name": k, "qty": v, "weight": weights[k]} for k, v in counts.items()],
        })
        cur.clear()

    for t in things:
        if cur and sum(x[3] for x in cur) + t[3] > rules.max_height:
            flush()
        cur.append(t)
    flush()
    return packages


def recommend_box(pkg: dict) -> Box | None:
    """Самая маленькая коробка СДЭК, куда влезает место (с поворотом) по размерам и весу."""
    need = sorted((pkg["length"], pkg["width"], pkg["height"]), reverse=True)
    fits = [b for b in BOXES if pkg["weight"] <= b.max_weight
            and all(n <= h for n, h in zip(need, sorted((b.length, b.width, b.height), reverse=True)))]
    return min(fits, key=lambda b: (b.length * b.width * b.height, b.max_weight)) if fits else None


def box_str(b: Box) -> str:
    return f"{b.title} {b.length}×{b.width}×{b.height} см, до {b.max_weight / 1000:g} кг"
