"""Поиск товаров по сроку годности на выбранном складе."""
from datetime import date, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.db import repo
from bot.db.models import Product
from bot.handlers.common import replace_message
from bot.handlers.products import parse_date
from bot.states import ExpirySearch

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

plain_text = F.text & ~F.text.startswith("/") & ~F.text.in_(kb.MENU_BUTTONS)
LIMIT = 50


def mark(p: Product, today: date) -> str:
    days = (p.expiry_date - today).days
    if days < 0:
        return "❌"
    if days <= 7:
        return "⚠️"
    return "✅"


async def show_results(
    message: Message, session: AsyncSession, user_id: int, title: str,
    date_from: date | None, date_to: date | None, edit: bool,
):
    wh = await repo.get_current_warehouse(session, user_id)
    if wh is None:
        warehouses = await repo.list_warehouses(session, user_id)
        text, markup = "Сначала выбери склад:", kb.warehouses_kb(warehouses, None, user_id)
    else:
        products = await repo.list_by_expiry(session, wh.id, date_from=date_from, date_to=date_to, limit=LIMIT + 1)
        more = len(products) > LIMIT
        products = products[:LIMIT]
        today = date.today()
        text = f"🏬 <b>{escape(wh.name)}</b>\n{title}\n"
        if not products:
            text += "\nНичего не найдено 👍"
        else:
            total_qty = sum(p.quantity for p in products)
            text += f"\nНайдено позиций: {len(products)}{'+' if more else ''}, всего {total_qty} шт."
            if more:
                text += f"\nПоказаны первые {LIMIT}, сначала с ближайшим сроком."
        markup = kb.expiry_results_kb(products, [mark(p, today) for p in products])

    if edit:
        await replace_message(message, text, markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(Command("expiry"))
@router.message(F.text == kb.BTN_EXPIRY)
async def cmd_expiry(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("⏰ Что ищем?", reply_markup=kb.expiry_menu_kb())


@router.callback_query(kb.ExpCb.filter(F.mode == "menu"))
async def cb_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await replace_message(callback.message, "⏰ Что ищем?", kb.expiry_menu_kb())
    await callback.answer()


@router.callback_query(kb.ExpCb.filter(F.mode == "expired"))
async def cb_expired(callback: CallbackQuery, session: AsyncSession):
    yesterday = date.today() - timedelta(days=1)
    await show_results(callback.message, session, callback.from_user.id,
                       "❌ Просроченные товары", None, yesterday, edit=True)
    await callback.answer()


@router.callback_query(kb.ExpCb.filter(F.mode == "soon"))
async def cb_soon(callback: CallbackQuery, callback_data: kb.ExpCb, session: AsyncSession):
    today = date.today()
    until = today + timedelta(days=callback_data.days)
    await show_results(callback.message, session, callback.from_user.id,
                       f"⚠️ Истекают в ближайшие {callback_data.days} дн. (до {until:%d.%m.%Y})",
                       today, until, edit=True)
    await callback.answer()


@router.callback_query(kb.ExpCb.filter(F.mode == "date"))
async def cb_date(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ExpirySearch.date)
    await replace_message(
        callback.message,
        "Введи дату (ДД.ММ.ГГГГ) — покажу всё, что годно до неё включительно, вместе с просроченным.\n"
        "Пример: 31.12.2026",
        kb.cancel_kb(),
    )
    await callback.answer()


@router.message(ExpirySearch.date, plain_text)
async def enter_date(message: Message, session: AsyncSession, state: FSMContext):
    until = parse_date(message.text.strip())
    if until is None:
        await message.answer("Не понял дату. Пример: 31.12.2026", reply_markup=kb.cancel_kb())
        return
    await state.clear()
    await show_results(message, session, message.from_user.id,
                       f"📅 Срок годности до {until:%d.%m.%Y}", None, until, edit=False)
