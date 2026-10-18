"""Группы и каналы: /sklad — выбрать склад и посмотреть товары. Менять товары — в личке с ботом."""
from datetime import date
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Chat, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.db import repo
from bot.db.models import Product, Warehouse
from bot.handlers.common import product_caption

router = Router()
GROUP_CHATS = {"group", "supergroup"}
router.message.filter(F.chat.type.in_(GROUP_CHATS))
router.callback_query.filter(F.message.chat.type.in_(GROUP_CHATS | {"channel"}))

PAGE_SIZE = 10


async def bot_link(bot: Bot) -> tuple[str, str]:
    me = await bot.me()
    return f"@{me.username}", f"https://t.me/{me.username}"


async def channel_admin_ids(bot: Bot, chat_id: int) -> list[int]:
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except TelegramAPIError:
        return []
    return [a.user.id for a in admins if not a.user.is_bot]


async def chat_warehouses(session: AsyncSession, bot: Bot, chat: Chat, by: int) -> list[Warehouse]:
    """В группе — склады того, кто вызвал /sklad. В канале (by=0) — склады админов канала."""
    if by:
        return await repo.list_warehouses(session, by)
    return await repo.list_warehouses_for_many(session, await channel_admin_ids(bot, chat.id))


async def is_in_chat(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except TelegramAPIError:
        return False
    return member.status in {"creator", "administrator", "member", "restricted"}


async def allowed_warehouse(session: AsyncSession, bot: Bot, chat: Chat, wh_id: int, by: int) -> Warehouse | None:
    """Склад можно показать в этом чате, только если он доступен тому, кто открыл меню (и тот в чате)."""
    if by and not await is_in_chat(bot, chat.id, by):
        return None
    return next((wh for wh in await chat_warehouses(session, bot, chat, by) if wh.id == wh_id), None)


async def warehouses_screen(session: AsyncSession, bot: Bot, chat: Chat, by: int):
    mention, _ = await bot_link(bot)
    warehouses = await chat_warehouses(session, bot, chat, by)
    if not warehouses:
        return f"Складов пока нет. Создать склад и добавить товары — в боте {mention}", None
    return "🏬 Выбери склад:", kb.chat_warehouses_kb(warehouses, by)


def mark(expiry: date | None, today: date) -> str:
    if expiry is None:
        return "▫️"
    days = (expiry - today).days
    return "❌" if days < 0 else "⚠️" if days <= 7 else "✅"


async def products_screen(session: AsyncSession, bot: Bot, wh: Warehouse, page: int, by: int):
    mention, url = await bot_link(bot)
    products = await repo.list_all_products(session, wh.id)
    today = date.today()
    pages = max(1, (len(products) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chunk = products[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    expired = sum(1 for p in products if p.expiry_date and p.expiry_date < today)
    soon = sum(1 for p in products if p.expiry_date and 0 <= (p.expiry_date - today).days <= 7)
    lines = [f"🏬 <b>{escape(wh.name)}</b>",
             f"Позиций: {len(products)} · всего {sum(p.quantity for p in products)} шт"]
    if expired or soon:
        lines.append(f"❌ просрочено: {expired} · ⚠️ истекает за 7 дн.: {soon}")
    if not products:
        lines.append("\nСклад пустой.")
    elif pages > 1:
        lines.append(f"Стр. {page + 1}/{pages}")
    lines.append(f"\nНажми на товар — подробности. Добавить или изменить — в боте {mention}")
    markup = kb.chat_products_kb(chunk, [mark(p.expiry_date, today) for p in chunk],
                                 wh.id, page, page < pages - 1, by, url)
    return "\n".join(lines), markup


async def replace(message: Message) -> None:
    """Telegram не умеет превращать текст в фото и обратно — удаляем старое сообщение, шлём новое."""
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


async def show(callback: CallbackQuery, text: str, markup) -> None:
    message = callback.message
    if message.text is None:
        await replace(message)
        await message.answer(text, reply_markup=markup)
        return
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def show_card(callback: CallbackQuery, product: Product, markup) -> None:
    caption = product_caption(product)
    photo = product.photo_file_id or product.photo_url
    if photo:
        try:
            await callback.message.answer_photo(photo, caption=caption, reply_markup=markup)
            await replace(callback.message)
            return
        except TelegramBadRequest:
            pass
    await show(callback, caption, markup)


@router.message(Command("sklad", "stock"))
async def group_sklad(message: Message, session: AsyncSession, bot: Bot):
    text, markup = await warehouses_screen(session, bot, message.chat, message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.message(CommandStart())
async def group_start(message: Message):
    await message.answer("Привет! Склады и товары — команда /sklad")


@router.channel_post(Command("sklad", "stock"))
async def channel_sklad(message: Message, session: AsyncSession, bot: Bot):
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    text, markup = await warehouses_screen(session, bot, message.chat, by=0)
    await message.answer(text, reply_markup=markup)


@router.callback_query(kb.ChatCb.filter())
async def cb_chat(callback: CallbackQuery, callback_data: kb.ChatCb, session: AsyncSession, bot: Bot):
    chat, by = callback.message.chat, callback_data.by

    if callback_data.action == "list":
        text, markup = await warehouses_screen(session, bot, chat, by)
        await show(callback, text, markup)
        await callback.answer()
        return

    wh = await allowed_warehouse(session, bot, chat, callback_data.wh, by)
    if wh is None:
        await callback.answer("Склад недоступен", show_alert=True)
        return

    if callback_data.action == "wh":
        text, markup = await products_screen(session, bot, wh, callback_data.page, by)
        await show(callback, text, markup)
        await callback.answer()
        return

    product = await session.get(Product, callback_data.pid)
    if product is None or product.warehouse_id != wh.id:
        await callback.answer("Товар не найден — возможно, его уже удалили", show_alert=True)
        return
    _, url = await bot_link(bot)
    await show_card(callback, product, kb.chat_product_kb(wh.id, callback_data.page, by, url))
    await callback.answer()
