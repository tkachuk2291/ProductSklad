"""Общие функции отрисовки: список товаров и карточка товара."""
from datetime import date
from html import escape

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.db import repo
from bot.db.models import Product

CAPTION_LIMIT = 1024


async def replace_message(message: Message, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Редактирует текстовое сообщение, а сообщение с фото заменяет новым."""
    if message.text is not None:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await message.answer(text, reply_markup=markup)


async def render_products(
    session: AsyncSession, user_id: int, page_size: int, page: int = 0
) -> tuple[str, InlineKeyboardMarkup]:
    wh = await repo.get_current_warehouse(session, user_id)
    if wh is None:
        warehouses = await repo.list_warehouses(session, user_id)
        text = "Сначала выбери склад:" if warehouses else "Складов пока нет — создай первый:"
        return text, kb.warehouses_kb(warehouses, None, user_id)

    total = await repo.count_products(session, wh.id)
    page = max(0, min(page, (total - 1) // page_size if total else 0))
    products = await repo.list_products(session, wh.id, page * page_size, page_size)
    has_next = (page + 1) * page_size < total

    text = f"🏬 <b>{escape(wh.name)}</b>\nТоваров: {total}"
    if total == 0:
        text += "\n\nСклад пустой. Нажми «➕ Добавить товар»."
    elif total > page_size:
        text += f" · стр. {page + 1}/{(total - 1) // page_size + 1}"
    return text, kb.products_kb(products, wh.id, page, has_next)


def expiry_status(expiry: date) -> str:
    days = (expiry - date.today()).days
    if days < 0:
        return f"❌ просрочен {-days} дн. назад"
    if days == 0:
        return "⚠️ истекает сегодня"
    if days <= 7:
        return f"⚠️ осталось {days} дн."
    return f"✅ осталось {days} дн."


def product_caption(p: Product) -> str:
    lines = [f"<b>{escape(p.name)}</b>"]
    if p.brand:
        lines.append(f"Бренд: {escape(p.brand)}")
    if p.weight:
        lines.append(f"Вес/объём: {escape(p.weight)}")
    if p.barcode:
        lines.append(f"Штрихкод: <code>{escape(p.barcode)}</code>")
    lines.append(f"Количество: <b>{p.quantity} шт</b>")
    if p.production_date:
        lines.append(f"Изготовлен: {p.production_date:%d.%m.%Y}")
    if p.expiry_date:
        lines.append(f"Годен до: {p.expiry_date:%d.%m.%Y} — {expiry_status(p.expiry_date)}")
    else:
        lines.append("Срок годности: не указан")
    text = "\n".join(lines)

    if p.description:
        room = CAPTION_LIMIT - len(text) - 10
        if room > 50:
            desc = p.description if len(p.description) <= room else p.description[: room - 1] + "…"
            text += f"\n\n<i>{escape(desc)}</i>"
    return text


async def send_product_card(message: Message, product: Product, page: int = 0) -> None:
    """Отправляет карточку товара новым сообщением (с фото, если оно есть)."""
    caption = product_caption(product)
    markup = kb.product_card_kb(product.id, page)
    photo = product.photo_file_id or product.photo_url
    if photo:
        try:
            await message.answer_photo(photo, caption=caption, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass
    await message.answer(caption, reply_markup=markup)


async def update_product_card(message: Message, product: Product, page: int) -> None:
    caption = product_caption(product)
    markup = kb.product_card_kb(product.id, page)
    try:
        if message.photo:
            await message.edit_caption(caption=caption, reply_markup=markup)
        else:
            await message.edit_text(caption, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
