"""Распознавание штрихкода с фото и поиск товара по штрихкоду."""
import io
import logging
from dataclasses import dataclass, field
from datetime import date

import aiohttp
import zxingcpp
from PIL import Image

log = logging.getLogger(__name__)

OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
OFF_FIELDS = (
    "product_name,product_name_uk,product_name_ru,brands,quantity,countries_tags,"
    "image_front_url,image_url,ingredients_text_uk,ingredients_text"
)
USER_AGENT = "WarehouseTelegramBot/1.0"

GS = "\x1d"  # разделитель полей переменной длины в GS1
# AI с фиксированной длиной данных, которые нас интересуют
FIXED_AI = {"01": 14, "11": 6, "13": 6, "15": 6, "17": 6}
VARIABLE_AI = {"10", "21"}

COUNTRY_NAMES = {
    "ukraine": "Украина", "united-states": "США", "poland": "Польша", "germany": "Германия",
    "france": "Франция", "russia": "Россия", "italy": "Италия", "spain": "Испания",
    "united-kingdom": "Великобритания", "turkey": "Турция", "moldova": "Молдова",
}


@dataclass
class ScanResult:
    barcode: str  # EAN/GTIN для поиска
    production_date: date | None = None
    expiry_date: date | None = None
    batch: str | None = None


@dataclass
class ProductInfo:
    name: str
    brand: str | None
    weight: str | None
    description: str | None
    photo_url: str | None
    countries: list[str] = field(default_factory=list)  # для каких стран заведена запись: ["ukraine", ...]

    @property
    def for_ukraine(self) -> bool:
        """True, если запись заведена для Украины или страна не указана."""
        return not self.countries or "ukraine" in self.countries

    @property
    def countries_display(self) -> str:
        return ", ".join(COUNTRY_NAMES.get(c, c) for c in self.countries)


def decode_image(data: bytes) -> str | None:
    """Достаёт текст первого найденного штрихкода с картинки."""
    try:
        image = Image.open(io.BytesIO(data))
        results = zxingcpp.read_barcodes(image)
    except Exception:
        log.exception("Не удалось распознать картинку")
        return None
    return results[0].text if results else None


def _gs1_date(raw: str) -> date | None:
    try:
        yy, mm, dd = int(raw[:2]), int(raw[2:4]), int(raw[4:6])
        if dd == 0:  # «00» в GS1 = последний день месяца
            next_month = date(2000 + yy + mm // 12, mm % 12 + 1, 1)
            return date.fromordinal(next_month.toordinal() - 1)
        return date(2000 + yy, mm, dd)
    except ValueError:
        return None


def parse_code(text: str) -> ScanResult | None:
    """Понимает обычный EAN/UPC и GS1 (DataMatrix, GS1-128, «Честный ЗНАК»)."""
    text = text.strip().removeprefix("]d2").removeprefix("]C1").removeprefix("]Q3")
    if text.isdigit() and 8 <= len(text) <= 14:
        return ScanResult(barcode=text)

    if not text.startswith("01"):
        return None

    result = ScanResult(barcode="")
    pos = 0
    while pos < len(text):
        if text[pos] == GS:
            pos += 1
            continue
        ai = text[pos:pos + 2]
        pos += 2
        if ai in FIXED_AI:
            value = text[pos:pos + FIXED_AI[ai]]
            pos += FIXED_AI[ai]
        elif ai in VARIABLE_AI:
            end = text.find(GS, pos)
            end = len(text) if end == -1 else end
            value, pos = text[pos:end], end
        else:
            break  # криптохвост «Честного ЗНАКа» (91/92/93) и прочее нам не нужно

        if ai == "01":
            # GTIN-14 с ведущим нулём = обычный EAN-13
            result.barcode = value[1:] if value.startswith("0") else value
        elif ai == "11":
            result.production_date = _gs1_date(value)
        elif ai in ("15", "17"):
            result.expiry_date = result.expiry_date or _gs1_date(value)
        elif ai == "10":
            result.batch = value
    return result if result.barcode else None


async def lookup(barcode: str) -> ProductInfo | None:
    """Ищет товар в открытой базе Open Food Facts."""
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as http:
            async with http.get(
                OFF_URL.format(code=barcode),
                params={"fields": OFF_FIELDS},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except (aiohttp.ClientError, TimeoutError):
        log.warning("Open Food Facts недоступен для %s", barcode)
        return None

    if data.get("status") != 1:
        return None
    p = data.get("product") or {}
    # Товары для Украины: украинское название приоритетнее
    name = p.get("product_name_uk") or p.get("product_name") or p.get("product_name_ru")
    if not name:
        return None
    return ProductInfo(
        name=name.strip(),
        brand=(p.get("brands") or "").split(",")[0].strip() or None,
        weight=p.get("quantity") or None,
        description=p.get("ingredients_text_uk") or p.get("ingredients_text") or None,
        photo_url=p.get("image_front_url") or p.get("image_url"),
        countries=[c.removeprefix("en:") for c in p.get("countries_tags") or []],
    )
