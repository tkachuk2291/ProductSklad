from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.config import Config
from bot.db import repo
from bot.handlers.common import render_products, replace_message
from bot.states import WarehouseForm

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

MAX_NAME = 100
# Обычный текст, а не команда и не кнопка главного меню
plain_text = F.text & ~F.text.startswith("/") & ~F.text.in_(kb.MENU_BUTTONS)


async def warehouses_view(session: AsyncSession, user_id: int):
    warehouses = await repo.list_warehouses(session, user_id)
    current = await repo.get_current_warehouse(session, user_id)
    if warehouses:
        text = "Выбери склад, с которым работаем:"
    else:
        text = "Складов пока нет. Создай первый 👇"
    return text, kb.warehouses_kb(warehouses, current.id if current else None, user_id)


# ---------- список и выбор ----------

@router.message(Command("sklad"))
@router.message(F.text == kb.BTN_WAREHOUSES)
async def cmd_warehouses(message: Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    text, markup = await warehouses_view(session, message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.callback_query(kb.WhCb.filter(F.action == "list"))
async def cb_list(callback: CallbackQuery, session: AsyncSession):
    text, markup = await warehouses_view(session, callback.from_user.id)
    await replace_message(callback.message, text, markup)
    await callback.answer()


@router.callback_query(kb.WhCb.filter(F.action == "open"))
async def cb_open(callback: CallbackQuery, callback_data: kb.WhCb, session: AsyncSession, config: Config):
    wh = await repo.get_warehouse(session, callback.from_user.id, callback_data.id)
    if wh is None:
        await callback.answer("Склад не найден", show_alert=True)
        return
    await repo.set_current_warehouse(session, callback.from_user.id, wh.id)
    text, markup = await render_products(session, callback.from_user.id, config.page_size)
    await replace_message(callback.message, text, markup)
    await callback.answer(f"Выбран склад «{wh.name}»")


# ---------- создание ----------

@router.callback_query(kb.WhCb.filter(F.action == "new"))
async def cb_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(WarehouseForm.create_name)
    await replace_message(callback.message, "Как назовём новый склад?", kb.cancel_kb())
    await callback.answer()


@router.message(WarehouseForm.create_name, plain_text)
async def create_name(message: Message, session: AsyncSession, state: FSMContext, config: Config):
    name = message.text.strip()[:MAX_NAME]
    if await repo.warehouse_name_taken(session, message.from_user.id, name):
        await message.answer("Склад с таким названием уже есть. Введи другое:", reply_markup=kb.cancel_kb())
        return
    wh = await repo.create_warehouse(session, message.from_user.id, name)
    await repo.set_current_warehouse(session, message.from_user.id, wh.id)
    await state.clear()
    await message.answer(f"Склад «{escape(name)}» создан и выбран ✅")
    text, markup = await render_products(session, message.from_user.id, config.page_size)
    await message.answer(text, reply_markup=markup)


# ---------- настройки: переименование / удаление ----------

@router.callback_query(kb.WhCb.filter(F.action == "settings"))
async def cb_settings(callback: CallbackQuery, callback_data: kb.WhCb, session: AsyncSession):
    wh = await repo.get_warehouse(session, callback.from_user.id, callback_data.id)
    if wh is None:
        await callback.answer("Склад не найден", show_alert=True)
        return
    count = await repo.count_products(session, wh.id)
    is_owner = wh.owner_id == callback.from_user.id
    text = f"⚙️ Склад <b>{escape(wh.name)}</b>\nТоваров: {count}"
    if is_owner:
        members = await repo.list_members(session, wh.id)
        text += f"\nДоступ есть ещё у: {len(members)} чел." if members else "\nДоступ только у тебя."
    else:
        text += "\n👥 Это общий склад — тебе дали к нему доступ."
    await replace_message(callback.message, text, kb.warehouse_settings_kb(wh.id, is_owner))
    await callback.answer()


@router.callback_query(kb.WhCb.filter(F.action == "rename"))
async def cb_rename(callback: CallbackQuery, callback_data: kb.WhCb, state: FSMContext):
    await state.set_state(WarehouseForm.rename)
    await state.update_data(warehouse_id=callback_data.id)
    await replace_message(callback.message, "Введи новое название склада:", kb.cancel_kb())
    await callback.answer()


@router.message(WarehouseForm.rename, plain_text)
async def rename_name(message: Message, session: AsyncSession, state: FSMContext, config: Config):
    data = await state.get_data()
    wh = await repo.get_warehouse(session, message.from_user.id, data["warehouse_id"])
    if wh is None:
        await state.clear()
        await message.answer("Склад не найден.")
        return
    name = message.text.strip()[:MAX_NAME]
    if name != wh.name and await repo.warehouse_name_taken(session, wh.owner_id, name):
        await message.answer("Склад с таким названием уже есть. Введи другое:", reply_markup=kb.cancel_kb())
        return
    await repo.rename_warehouse(session, wh, name)
    await state.clear()
    await message.answer(f"Переименовано в «{escape(name)}» ✅")
    text, markup = await render_products(session, message.from_user.id, config.page_size)
    await message.answer(text, reply_markup=markup)


@router.callback_query(kb.WhCb.filter(F.action == "delete"))
async def cb_delete(callback: CallbackQuery, callback_data: kb.WhCb, session: AsyncSession):
    wh = await repo.get_warehouse(session, callback.from_user.id, callback_data.id)
    if wh is None or wh.owner_id != callback.from_user.id:
        await callback.answer("Склад не найден", show_alert=True)
        return
    count = await repo.count_products(session, wh.id)
    await replace_message(
        callback.message,
        f"Удалить склад <b>{escape(wh.name)}</b> вместе со всеми товарами (позиций: {count})?",
        kb.confirm_delete_warehouse_kb(wh.id),
    )
    await callback.answer()


@router.callback_query(kb.WhCb.filter(F.action == "delete_ok"))
async def cb_delete_ok(callback: CallbackQuery, callback_data: kb.WhCb, session: AsyncSession):
    wh = await repo.get_warehouse(session, callback.from_user.id, callback_data.id)
    if wh is not None and wh.owner_id == callback.from_user.id:
        await repo.delete_warehouse(session, wh)
    text, markup = await warehouses_view(session, callback.from_user.id)
    await replace_message(callback.message, f"Склад удалён.\n\n{text}", markup)
    await callback.answer()


# ---------- совместный доступ ----------

INVITE_PREFIX = "inv_"


async def invite_link(bot: Bot, token: str) -> str:
    me = await bot.me()
    return f"https://t.me/{me.username}?start={INVITE_PREFIX}{token}"


async def owned_warehouse(session: AsyncSession, user_id: int, wh_id: int):
    wh = await repo.get_warehouse(session, user_id, wh_id)
    return wh if wh and wh.owner_id == user_id else None


async def access_view(session: AsyncSession, bot: Bot, wh, regenerate: bool = False):
    token = await repo.get_or_create_invite(session, wh, regenerate=regenerate)
    link = await invite_link(bot, token)
    members = await repo.list_members(session, wh.id)
    text = (
        f"👥 Доступ к складу <b>{escape(wh.name)}</b>\n\n"
        "Отправь эту ссылку человеку — он откроет её в Telegram, нажмёт «Старт» "
        "и сможет делать со складом всё то же, что и ты: смотреть, добавлять, менять и удалять товары.\n"
        "Удалить склад и управлять доступом можешь только ты.\n\n"
        f"🔗 {link}\n\n"
    )
    if members:
        text += "Сейчас доступ есть у:\n" + "\n".join(f"• {escape(m.name)}" for m in members)
    else:
        text += "Пока доступ только у тебя."
    return text, kb.access_kb(wh.id, members)


@router.callback_query(kb.WhCb.filter(F.action.in_({"access", "regen"})))
async def cb_access(callback: CallbackQuery, callback_data: kb.WhCb, session: AsyncSession, bot: Bot):
    wh = await owned_warehouse(session, callback.from_user.id, callback_data.id)
    if wh is None:
        await callback.answer("Доступом управляет только владелец склада", show_alert=True)
        return
    regenerate = callback_data.action == "regen"
    text, markup = await access_view(session, bot, wh, regenerate=regenerate)
    await replace_message(callback.message, text, markup)
    await callback.answer("Новая ссылка создана, старая больше не работает" if regenerate else None)


@router.callback_query(kb.WhCb.filter(F.action == "kick"))
async def cb_kick(callback: CallbackQuery, callback_data: kb.WhCb, session: AsyncSession, bot: Bot):
    wh = await owned_warehouse(session, callback.from_user.id, callback_data.id)
    if wh is None:
        await callback.answer("Доступом управляет только владелец склада", show_alert=True)
        return
    member = await repo.remove_member(session, wh.id, callback_data.uid)
    if member:
        try:
            await bot.send_message(member.tg_id, f"🚫 Тебе закрыли доступ к складу «{escape(wh.name)}».")
        except TelegramAPIError:
            pass
    text, markup = await access_view(session, bot, wh)
    await replace_message(callback.message, text, markup)
    await callback.answer(f"{member.name} больше не имеет доступа" if member else None)


@router.callback_query(kb.WhCb.filter(F.action == "leave"))
async def cb_leave(callback: CallbackQuery, callback_data: kb.WhCb, session: AsyncSession):
    wh = await repo.get_warehouse(session, callback.from_user.id, callback_data.id)
    if wh is None:
        await callback.answer("Склад не найден", show_alert=True)
        return
    await replace_message(
        callback.message,
        f"Выйти со склада <b>{escape(wh.name)}</b>? Товары останутся у владельца, "
        "а вернуться можно будет только по новой ссылке.",
        kb.confirm_leave_kb(wh.id),
    )
    await callback.answer()


@router.callback_query(kb.WhCb.filter(F.action == "leave_ok"))
async def cb_leave_ok(callback: CallbackQuery, callback_data: kb.WhCb, session: AsyncSession):
    await repo.remove_member(session, callback_data.id, callback.from_user.id)
    text, markup = await warehouses_view(session, callback.from_user.id)
    await replace_message(callback.message, f"Доступ к складу закрыт.\n\n{text}", markup)
    await callback.answer()


async def join_by_invite(message: Message, token: str, session: AsyncSession, bot: Bot, config: Config):
    """Вызывается из /start inv_<токен>."""
    user = message.from_user
    wh = await repo.find_by_invite(session, token)
    if wh is None:
        await message.answer("Ссылка недействительна — возможно, владелец создал новую. Попроси прислать свежую.")
        return
    if wh.owner_id == user.id:
        await message.answer("Это твой собственный склад 🙂 Ссылку нужно отправить другому человеку.")
    else:
        added = await repo.add_member(session, wh, user.id, user.full_name)
        await message.answer(
            f"✅ Тебе открыт доступ к складу «{escape(wh.name)}»" if added
            else f"У тебя уже есть доступ к складу «{escape(wh.name)}»."
        )
        if added:
            try:
                await bot.send_message(wh.owner_id, f"👤 {escape(user.full_name)} теперь имеет доступ к складу «{escape(wh.name)}».")
            except TelegramAPIError:
                pass
    await repo.set_current_warehouse(session, user.id, wh.id)
    text, markup = await render_products(session, user.id, config.page_size)
    await message.answer(text, reply_markup=markup)


