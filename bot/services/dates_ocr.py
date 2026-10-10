"""Распознавание дат изготовления и годности с фото упаковки (локальный OCR, без внешних API)."""
import calendar
import io
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
from PIL import Image, ImageOps

log = logging.getLogger(__name__)

_engine = None
_engine_lock = threading.Lock()


def get_engine():
    """RapidOCR с моделью для восточнославянских языков (укр/рус). Грузится один раз."""
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

            _engine = RapidOCR(params={
                "Global.log_level": "error",
                # Встроенный определитель «текст вверх ногами» ошибается на точечной печати
                # («L070826» читается как «9280L07»), поэтому повороты перебираем сами
                "Global.use_cls": False,
                "Rec.lang_type": LangRec.ESLAV,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
            })
        return _engine


def warmup() -> None:
    """Загружает (и при первом запуске скачивает) все модели OCR.

    Модели грузятся лениво — на первом фото с текстом, поэтому прогоняем распознавание
    на картинке с датой. Вызывается при сборке Docker-образа и при старте бота."""
    from PIL import ImageDraw, ImageFont

    image = Image.new("RGB", (420, 90), "white")
    ImageDraw.Draw(image).text((15, 15), "12.08.26", fill="black", font=ImageFont.load_default(size=56))
    lines = _ocr_lines(image)
    log.info("OCR готов: %s", lines)


# ---------- поиск дат в тексте ----------

SEP = r"\s?[.,/\-]\s?"
FULL_DATE = re.compile(rf"(?<!\d)(\d{{1,2}}){SEP}(\d{{1,2}}){SEP}(\d{{4}}|\d{{2}})(?!\d)")
MONTH_YEAR = re.compile(r"(?<![\d.,/\-])(\d{1,2})\s?[./]\s?(20\d{2})(?![\d.,/\-])")
# Слитная дата на струйной печати: «L070826», «070826», «EXP07022027». Буквы впереди — код линии/партии или EXP.
# O вместо 0 — частая ошибка OCR на точечной печати.
COMPACT = re.compile(r"(?<![\w])([A-Za-zА-ЯЁІЇЄа-яёіїє]{0,3})([\dOoОо]{5,8})(?![\w])")
_O2ZERO = str.maketrans("OoОо", "0000")
PREFIX_EXP = ("exp", "bb", "bbe", "ex")
PREFIX_PROD = ("mfg", "mfd", "pd")

SHELF_LIFE = re.compile(
    r"(\d{1,3})\s*(міс|мес|month|діб|доб|днів|дні|дней|дня|дн|сут|day|рок|рік|рок|год|лет|year)", re.I
)

# Латинские буквы, которые OCR путает с кириллицей
_LAT2CYR = str.maketrans("aceiopxybkmht", "асеіорхувкмнт")

PROD_KW = ("виготов", "виг.", "виг:", "изготов", "изг.", "изг:", "произв", "вироб", "упаков", "пакув", "дата вир")
PROD_KW_LAT = ("mfg", "mfd", "prod", "p.d", "pd:")
EXP_KW = ("вжити", "спожити", "придатн", "кращ", "годен", "годн", "употреб", "кінцев", "конечн")
EXP_KW_LAT = ("exp", "best before", "bb:", "use by", "bbe")
SHELF_KW = ("термін", "срок", "зберіг", "хранен", "придатн", "годност")


@dataclass
class DateScan:
    production: date | None = None
    expiry: date | None = None
    shelf_life: str | None = None  # как написано: «6 міс»
    expiry_computed: bool = False  # срок посчитан как «изготовлен + срок хранения»
    year_guessed: bool = False  # последняя цифра года не прочиталась — год подобран по второй дате
    lines: list[str] = field(default_factory=list)


def _norm(text: str) -> str:
    return text.lower().translate(_LAT2CYR)


def _has(text: str, cyr: tuple[str, ...], lat: tuple[str, ...]) -> bool:
    low = text.lower()
    norm = _norm(text)
    return any(k in norm for k in cyr) or any(k in low for k in lat)


def _plausible(d: date) -> bool:
    today = date.today()
    return today.replace(year=today.year - 3) <= d <= today.replace(year=today.year + 6)


def _make_date(dd: int, mm: int, yy: int) -> date | None:
    if yy < 100:
        yy += 2000
    try:
        d = date(yy, mm, dd)
    except ValueError:
        return None
    return d if _plausible(d) else None


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _shelf_life(lines: list[str]) -> tuple[str, int, str] | None:
    """Ищет «термін придатності 6 міс» → ('6 міс', 6, 'month')."""
    for i, line in enumerate(lines):
        context = _norm(line + " " + (lines[i - 1] if i else ""))
        if not any(k in context for k in SHELF_KW):
            continue
        m = SHELF_LIFE.search(line)
        if not m:
            continue
        n, unit = int(m.group(1)), _norm(m.group(2))
        if unit.startswith(("міс", "мес", "month")):
            kind = "month"
        elif unit.startswith(("рок", "рік", "год", "лет", "year")):
            kind = "year"
        else:
            kind = "day"
        if n > 0:
            return m.group(0), n, kind
    return None


def _compact_dates(line: str):
    """Слитные даты ДДММГГ / ДДММГГГГ. Возвращает (start, end, date, label) и обрезанные ДДММГ."""
    full, partial = [], []
    for m in COMPACT.finditer(line):
        prefix, body = m.group(1).lower(), m.group(2).translate(_O2ZERO)
        if sum(ch.isdigit() for ch in m.group(2)) < 4:
            continue  # это слово, а не число
        label = "exp" if prefix in PREFIX_EXP else "prod" if prefix in PREFIX_PROD else None
        dd, mm = int(body[:2]), int(body[2:4])
        if len(body) == 6:
            d = _make_date(dd, mm, int(body[4:]))
        elif len(body) == 8:
            d = _make_date(dd, mm, int(body[4:]))
        elif len(body) == 5 and prefix and 1 <= dd <= 31 and 1 <= mm <= 12:
            # «B07022» — последняя цифра года потерялась; год подберём по второй дате
            partial.append((dd, mm, int(body[4])))
            continue
        else:
            continue
        if d:
            full.append((m.start(), m.end(), d, label))
    return full, partial


def _restore_year(dd: int, mm: int, decade: int, after: date | None = None, before: date | None = None) -> date | None:
    """Ближайшая дата ДД.ММ.20{decade}X, которая позже `after` (или раньше `before`), в пределах 5 лет."""
    candidates = []
    for digit in range(10):
        try:
            d = date(2000 + decade * 10 + digit, mm, dd)
        except ValueError:
            continue
        if after and after < d <= after.replace(year=after.year + 5):
            candidates.append(d)
        if before and before.replace(year=before.year - 5) <= d < before:
            candidates.append(d)
    if not candidates:
        return None
    return min(candidates) if after else max(candidates)


def extract_dates(lines: list[str]) -> DateScan:
    scan = DateScan(lines=lines)
    found: list[tuple[date, str | None]] = []  # (дата, 'prod' | 'exp' | None)
    partials: list[tuple[int, int, int]] = []

    for i, line in enumerate(lines):
        dates = []
        for m in FULL_DATE.finditer(line):
            d = _make_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if d:
                dates.append((m.start(), m.end(), d, None))
        for m in MONTH_YEAR.finditer(line):
            mm, yy = int(m.group(1)), int(m.group(2))
            if 1 <= mm <= 12:
                d = _make_date(calendar.monthrange(yy, mm)[1], mm, yy)  # «до 03.2027» = до конца месяца
                if d:
                    dates.append((m.start(), m.end(), d, None))
        compact, partial = _compact_dates(line)
        dates += compact
        partials += partial

        prev_end = 0
        for start, end, d, prefix_label in sorted(dates, key=lambda x: x[0]):
            # Подпись ищем между предыдущей датой и этой, а если её нет — в предыдущей строке
            before = line[prev_end:start].strip() or (lines[i - 1] if i and prev_end == 0 else "")
            prev_end = end
            label = prefix_label
            if label:
                pass
            elif _has(before, EXP_KW, EXP_KW_LAT):
                label = "exp"
            elif _has(before, PROD_KW, PROD_KW_LAT):
                label = "prod"
            found.append((d, label))

    for d, label in found:
        if label == "prod" and scan.production is None:
            scan.production = d
        elif label == "exp" and scan.expiry is None:
            scan.expiry = d

    # Даты без подписи: как на струйной печати «12.08.26  12.02.27» — ранняя = изготовлена, поздняя = годен до
    rest = sorted({d for d, label in found if label is None} - {scan.production, scan.expiry})
    if len(rest) >= 2:
        scan.production = scan.production or rest[0]
        scan.expiry = scan.expiry or rest[-1]
    elif len(rest) == 1:
        d = rest[0]
        if scan.production and not scan.expiry:
            scan.expiry = d
        elif scan.expiry and not scan.production:
            scan.production = d
        elif not scan.production and not scan.expiry:
            if d > date.today():
                scan.expiry = d
            else:
                scan.production = d

    if scan.production and scan.expiry and scan.production > scan.expiry:
        scan.production, scan.expiry = scan.expiry, scan.production

    # Обрезанная дата («B07022»): год восстанавливаем по второй, целой дате
    for dd, mm, decade in partials:
        if scan.production and not scan.expiry:
            scan.expiry = _restore_year(dd, mm, decade, after=scan.production)
            scan.year_guessed = scan.expiry is not None
        elif scan.expiry and not scan.production:
            scan.production = _restore_year(dd, mm, decade, before=scan.expiry)
            scan.year_guessed = scan.production is not None

    shelf = _shelf_life(lines)
    if shelf:
        scan.shelf_life = shelf[0]
        if scan.production and not scan.expiry:
            n, kind = shelf[1], shelf[2]
            if kind == "month":
                scan.expiry = _add_months(scan.production, n)
            elif kind == "year":
                scan.expiry = _add_months(scan.production, 12 * n)
            else:
                scan.expiry = scan.production + timedelta(days=n)
            scan.expiry_computed = True
    return scan


# ---------- распознавание с картинки ----------

def _ocr_lines(image: Image.Image) -> list[str]:
    # RapidOCR (как и OpenCV) ждёт каналы в порядке BGR — с RGB точечная печать почти не читается
    bgr = np.ascontiguousarray(np.array(image)[:, :, ::-1])
    result = get_engine()(bgr)
    return list(result.txts or [])


def _variants(image: Image.Image):
    """Исходное фото, затем контрастная ч/б версия (помогает со струйной печатью на цветной фольге)."""
    yield image
    yield ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=2).convert("RGB")


def recognize(data: bytes) -> DateScan:
    """Читает текст с фото и достаёт даты. Если «годен до» не нашли — пробуем контраст и повороты."""
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    best = DateScan()
    for angle in (0, 180, 90, 270):
        rotated = image if angle == 0 else image.rotate(angle, expand=True)
        for variant in _variants(rotated):
            try:
                scan = extract_dates(_ocr_lines(variant))
            except Exception:
                log.exception("Ошибка OCR")
                continue
            if scan.expiry and not scan.year_guessed:
                return scan
            if _better(scan, best):
                best = scan
    return best


def _better(a: DateScan, b: DateScan) -> bool:
    """Что лучше: найден срок > найдена хоть одна дата > больше текста."""
    def rank(s: DateScan):
        return (s.expiry is not None, s.production is not None, len(s.lines))
    return rank(a) > rank(b)
