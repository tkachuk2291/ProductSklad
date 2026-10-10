from aiogram import F, Router
from aiogram import Bot
from aiogram.filters import Command, CommandObject, CommandStart
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import keyboards as kb
from bot.config import Config
from bot.handlers.common import replace_message
from bot.handlers.warehouses import INVITE_PREFIX, join_by_invite

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

HELP = (
    "Я помогаю вести учёт товаров на складах.\n\n"
    "/sklad — выбрать, создать, переименовать или удалить склад\n"
    "/items — товары выбранного склада\n"
    "/add — добавить товар (по штрихкоду или вручную)\n"
    "/expiry — поиск по сроку годности (просроченные, скоро истекают, до даты)\n"
    "/cancel — отменить текущее действие\n\n"
    "👥 Работать со складом вдвоём: ⚙️ Склад → «Доступ для других» → отправь ссылку."
)


@router.message(CommandStart(deep_link=True, magic=F.args.startswith(INVITE_PREFIX)))
async def cmd_start_invite(
    message: Message, command: CommandObject, state: FSMContext, session: AsyncSession, bot: Bot, config: Config
):
    await state.clear()
    await message.answer("Привет! 👋", reply_markup=kb.main_menu())
    await join_by_invite(message, command.args.removeprefix(INVITE_PREFIX), session, bot, config)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(f"Привет! {HELP}", reply_markup=kb.main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP, reply_markup=kb.main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.", reply_markup=kb.main_menu())


@router.callback_query(kb.FormCb.filter(F.action == "cancel"))
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await replace_message(callback.message, "Отменено.")
    await callback.answer()
