import asyncio
from datetime import date, datetime
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import keyboards as kb
from bot.config import Config
from bot.db import repo
from bot.handlers.common import (
    render_products,
    replace_message,
    send_product_card,
    update_product_card,
)
from bot.services import barcode as bc
from bot.services import dates_ocr
from bot.states import AddProduct, EditProduct

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

plain_text = F.text & ~F.text.startswith("/") & ~F.text.in_(kb.MENU_BUTTONS)
DATE_FORMATS = ("%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d")


@router.message(Command("items"))
@router.message(F.text == kb.BTN_PRODUCTS)
async def cmd_items(message: Message, session: AsyncSession, state: FSMContext, config: Config):
    await state.clear()
    text, markup = await render_products(session, message.from_user.id, config.page_size)
    await message.answer(text, reply_markup=markup)


@router.callback_query(kb.ProdCb.filter(F.action == "list"))
async def cb_list(callback: CallbackQuery, callback_data: kb.ProdCb, session: AsyncSession, config: Config):
    text, markup = await render_products(session, callback.from_user.id, config.page_size, callback_data.page)
    await replace_message(callback.message, text, markup)
    await callback.answer()


@router.callback_query(kb.ProdCb.filter(F.action == "view"))
async def cb_view(callback: CallbackQuery, callback_data: kb.ProdCb, session: AsyncSession):
    product = await repo.get_product(session, callback.from_user.id, callback_data.id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    await callback.message.delete()
    await send_product_card(callback.message, product, callback_data.page)
    await callback.answer()


@router.callback_query(kb.ProdCb.filter(F.action.in_({"inc", "dec"})))
async def cb_change_qty(callback: CallbackQuery, callback_data: kb.ProdCb, session: AsyncSession):
    product = await repo.get_product(session, callback.from_user.id, callback_data.id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    if callback_data.action == "dec" and product.quantity <= 0:
        await callback.answer("Уже 0 шт")
        return
    await repo.change_quantity(session, product, 1 if callback_data.action == "inc" else -1)
    await update_product_card(callback.message, product, callback_data.page)
    await callback.answer(f"{product.quantity} шт")


@router.callback_query(kb.ProdCb.filter(F.action == "delete"))
async def cb_delete(callback: CallbackQuery, callback_data: kb.ProdCb):
    markup = kb.confirm_delete_product_kb(callback_data.id, callback_data.page)
    await callback.message.edit_reply_markup(reply_markup=markup)
    await callback.answer("Точно удалить?")


@router.callback_query(kb.ProdCb.filter(F.action == "delete_ok"))
async def cb_delete_ok(callback: CallbackQuery, callback_data: kb.ProdCb, session: AsyncSession, config: Config):
    product = await repo.get_product(session, callback.from_user.id, callback_data.id)
    if product is not None:
        await repo.delete_product(session, product)
    text, markup = await render_products(session, callback.from_user.id, config.page_size, callback_data.page)
    await replace_message(callback.message, f"🗑 Товар удалён.\n\n{text}", markup)
    await callback.answer()


async def start_add(message: Message, user_id: int, session: AsyncSession, state: FSMContext, edit: bool = False):
    wh = await repo.get_current_warehouse(session, user_id)
    if wh is None:
        await state.clear()
        warehouses = await repo.list_warehouses(session, user_id)
        text = "Сначала выбери склад, куда добавляем товар:"
        markup = kb.warehouses_kb(warehouses, None, user_id)
    else:
        await state.clear()
        await state.set_state(AddProduct.method)
        await state.update_data(warehouse_id=wh.id)
        text = f"➕ Добавление на склад <b>{escape(wh.name)}</b>\n\nКак добавляем товар?"
        markup = kb.add_method_kb()
    if edit:
        await replace_message(message, text, markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(Command("add"))
@router.message(F.text == kb.BTN_ADD)
async def cmd_add(message: Message, session: AsyncSession, state: FSMContext):
    await start_add(message, message.from_user.id, session, state)


@router.callback_query(kb.ProdCb.filter(F.action == "add"))
async def cb_add(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    await start_add(callback.message, callback.from_user.id, session, state, edit=True)
    await callback.answer()


@router.callback_query(AddProduct.method, kb.FormCb.filter(F.action == "barcode"))
async def cb_by_barcode(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddProduct.barcode)
    await replace_message(
        callback.message,
        "📷 Пришли <b>фото штрихкода</b> или введи цифры под ним.\n"
        "Подойдут и квадратные коды DataMatrix — из них бот возьмёт ещё и срок годности.",
        kb.barcode_kb(),
    )
    await callback.answer()


@router.message(StateFilter(AddProduct.method, AddProduct.barcode), F.photo)
async def barcode_photo(message: Message, bot: Bot, session: AsyncSession, state: FSMContext):
    file = await bot.download(message.photo[-1])
    raw = await asyncio.to_thread(bc.decode_image, file.read())
    if raw is None:
        await message.answer(
            "Не смог прочитать штрихкод 😕 Сфоткай ближе и ровнее, пришли цифрами или введи вручную.",
            reply_markup=kb.barcode_kb(),
        )
        return
    await process_barcode(message, raw, session, state)


@router.message(StateFilter(AddProduct.method, AddProduct.barcode), plain_text)
async def barcode_text(message: Message, session: AsyncSession, state: FSMContext):
    await process_barcode(message, message.text, session, state)


async def process_barcode(message: Message, raw: str, session: AsyncSession, state: FSMContext):
    scan = bc.parse_code(raw)
    if scan is None:
        await message.answer(
            "Это не похоже на штрихкод товара. Пришли 8–14 цифр, фото или введи вручную.",
            reply_markup=kb.barcode_kb(),
        )
        return

    data = await state.get_data()
    await state.update_data(
        barcode=scan.barcode,
        expiry=scan.expiry_date.isoformat() if scan.expiry_date else None,
        production=scan.production_date.isoformat() if scan.production_date else None,
    )
    found_dates = ""
    if scan.production_date:
        found_dates += f"\nИзготовлен: {scan.production_date:%d.%m.%Y}"
    if scan.expiry_date:
        found_dates += f"\nГоден до: {scan.expiry_date:%d.%m.%Y}"

    existing = await repo.find_by_barcode(session, data["warehouse_id"], scan.barcode)
    if existing:
        await state.update_data(existing_id=existing.id)
        await state.set_state(AddProduct.quantity)
        await message.answer(
            f"📦 «{escape(existing.name)}» уже есть на складе: {existing.quantity} шт.{found_dates}\n\n"
            "Сколько добавить?",
            reply_markup=kb.quantity_kb(),
        )
        return

    await message.answer(f"🔎 Ищу товар <code>{escape(scan.barcode)}</code>…")

    known = await repo.find_by_barcode_anywhere(session, message.from_user.id, scan.barcode)
    if known:
        await state.update_data(
            name=known.name, brand=known.brand, weight=known.weight, description=known.description,
            photo_file_id=known.photo_file_id, photo_url=known.photo_url,
        )
        details = "\n".join(x for x in (known.brand, known.weight) if x)
        await state.set_state(AddProduct.quantity)
        await message.answer(
            f"✅ Знаю этот товар: <b>{escape(known.name)}</b>\n{escape(details)}{found_dates}\n\n"
            "Сколько штук добавить?",
            reply_markup=kb.quantity_kb(),
        )
        return

    info = await bc.lookup(scan.barcode)
    if info is None:
        await state.set_state(AddProduct.name)
        await message.answer(
            f"В базе такого штрихкода нет — заполним вручную.{found_dates}\n\nВведи <b>название</b> товара:",
            reply_markup=kb.cancel_kb(),
        )
        return

    await state.update_data(
        name=info.name, brand=info.brand, weight=info.weight,
        description=info.description, photo_url=info.photo_url,
    )
    await state.set_state(AddProduct.confirm)
    details = "\n".join(x for x in (info.brand, info.weight) if x)
    warning = ""
    if not info.for_ukraine:
        warning = (
            f"\n\n⚠️ Запись заведена не для Украины ({escape(info.countries_display)}) — "
            "данные могут не совпадать с товаром у тебя в руках."
        )
    text = (
        f"🌐 В открытой базе Open Food Facts штрихкод <code>{escape(scan.barcode)}</code> записан как:\n\n"
        f"<b>{escape(info.name)}</b>\n{escape(details)}{found_dates}{warning}\n\n"
        "Это тот товар? Если нет — введём вручную, и дальше бот будет узнавать его по твоим данным."
    )
    if info.photo_url:
        try:
            await message.answer_photo(info.photo_url, caption=text, reply_markup=kb.confirm_found_kb())
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, reply_markup=kb.confirm_found_kb())


@router.callback_query(AddProduct.confirm, kb.FormCb.filter(F.action == "confirm"))
async def cb_confirm_found(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await ask_quantity(callback.message, state)


@router.callback_query(
    StateFilter(AddProduct.method, AddProduct.barcode, AddProduct.confirm), kb.FormCb.filter(F.action == "manual")
)
async def cb_manual(callback: CallbackQuery, state: FSMContext):
    await state.update_data(name=None, brand=None, weight=None, description=None, photo_url=None)
    await state.set_state(AddProduct.name)
    await replace_message(callback.message, "Введи <b>название</b> товара:", kb.cancel_kb())
    await callback.answer()


async def ask_brand(message: Message, state: FSMContext):
    await state.set_state(AddProduct.brand)
    await message.answer("Бренд / производитель?", reply_markup=kb.skip_kb())


async def ask_weight(message: Message, state: FSMContext):
    await state.set_state(AddProduct.weight)
    await message.answer("Вес или объём (например «150 г», «1 л»)?", reply_markup=kb.skip_kb())


async def ask_photo(message: Message, state: FSMContext):
    await state.set_state(AddProduct.photo)
    await message.answer("Пришли фото товара 📷", reply_markup=kb.skip_kb())


async def ask_quantity(message: Message, state: FSMContext):
    await state.set_state(AddProduct.quantity)
    await message.answer("Сколько штук добавить?", reply_markup=kb.quantity_kb())


EXPIRY_PROMPT = (
    "Срок годности: пришли 📷 <b>фото даты на упаковке</b> — распознаю сам,\n"
    "или введи «годен до» вручную (ДД.ММ.ГГГГ)."
)


async def ask_expiry(message: Message, state: FSMContext):
    await state.set_state(AddProduct.expiry)
    await message.answer(EXPIRY_PROMPT, reply_markup=kb.skip_kb())


@router.message(AddProduct.name, plain_text)
async def manual_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip()[:200])
    await ask_brand(message, state)


@router.message(AddProduct.brand, plain_text)
async def manual_brand(message: Message, state: FSMContext):
    await state.update_data(brand=message.text.strip()[:200])
    await ask_weight(message, state)


@router.message(AddProduct.weight, plain_text)
async def manual_weight(message: Message, state: FSMContext):
    await state.update_data(weight=message.text.strip()[:50])
    await ask_photo(message, state)


@router.message(AddProduct.photo, F.photo)
async def manual_photo(message: Message, state: FSMContext):
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await ask_quantity(message, state)


@router.message(AddProduct.photo)
async def manual_photo_wrong(message: Message):
    await message.answer("Нужна именно фотография (или нажми «Пропустить»).", reply_markup=kb.skip_kb())


@router.message(AddProduct.quantity, plain_text)
async def enter_quantity(message: Message, session: AsyncSession, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or not 0 < int(text) <= 100_000:
        await message.answer("Введи целое число больше нуля:", reply_markup=kb.quantity_kb())
        return
    await apply_quantity(message, session, state, int(text), message.from_user.id)


@router.callback_query(AddProduct.quantity, kb.QtyCb.filter())
async def cb_quantity(callback: CallbackQuery, callback_data: kb.QtyCb, session: AsyncSession, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer(f"+{callback_data.n}")
    await apply_quantity(callback.message, session, state, callback_data.n, callback.from_user.id)


@router.callback_query(AddProduct.quantity, kb.FormCb.filter(F.action == "qty_manual"))
async def cb_quantity_manual(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await callback.message.answer("Введи количество числом:", reply_markup=kb.cancel_kb())


async def apply_quantity(message: Message, session: AsyncSession, state: FSMContext, n: int, user_id: int):
    data = await state.get_data()
    if data.get("existing_id"):
        await add_to_existing(message, session, state, n, user_id)
        return
    await state.update_data(quantity=n)
    if data.get("expiry"):
        await save_product(message, session, state)
    else:
        await ask_expiry(message, state)


async def add_to_existing(message: Message, session: AsyncSession, state: FSMContext, n: int, user_id: int):
    data = await state.get_data()
    await state.clear()
    product = await repo.get_product(session, user_id, data["existing_id"])
    if product is None:
        await message.answer("Товар пропал со склада, попробуй добавить заново: /add")
        return
    await repo.change_quantity(session, product, n)
    if data.get("expiry"):
        product.expiry_date = date.fromisoformat(data["expiry"])
    if data.get("production"):
        product.production_date = date.fromisoformat(data["production"])
    await session.commit()
    await message.answer(f"➕ Добавлено {n} шт. — теперь {product.quantity} шт. Следующий товар — /add")
    await send_product_card(message, product)


@router.message(AddProduct.expiry, plain_text)
async def enter_expiry(message: Message, session: AsyncSession, state: FSMContext):
    parsed = parse_date(message.text.strip())
    if parsed is None:
        await message.answer("Не понял дату. Пример: 15.03.2027", reply_markup=kb.skip_kb())
        return
    await state.update_data(expiry=parsed.isoformat())
    await save_product(message, session, state)


@router.callback_query(kb.FormCb.filter(F.action == "skip"))
async def cb_skip(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    current = await state.get_state()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    message = callback.message
    if current == AddProduct.brand.state:
        await ask_weight(message, state)
    elif current == AddProduct.weight.state:
        await ask_photo(message, state)
    elif current == AddProduct.photo.state:
        await ask_quantity(message, state)
    elif current == AddProduct.expiry.state:
        await save_product(message, session, state)
    elif current == EditProduct.name.state:
        await edit_ask_brand(message, state)
    elif current == EditProduct.brand.state:
        await edit_ask_expiry(message, state)
    elif current == EditProduct.expiry.state:
        await edit_finish(message, session, state, callback.from_user.id)


def parse_date(text: str) -> date | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


async def save_product(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    expiry = date.fromisoformat(data["expiry"]) if data.get("expiry") else None
    production = date.fromisoformat(data["production"]) if data.get("production") else None

    product = await repo.create_product(
        session,
        data["warehouse_id"],
        name=data["name"],
        barcode=data.get("barcode"),
        brand=data.get("brand"),
        weight=data.get("weight"),
        description=data.get("description"),
        photo_file_id=data.get("photo_file_id"),
        photo_url=data.get("photo_url"),
        quantity=data["quantity"],
        production_date=production,
        expiry_date=expiry,
    )
    await message.answer("✅ Товар добавлен на склад!")
    await send_product_card(message, product)


@router.callback_query(kb.ProdCb.filter(F.action == "edit"))
async def cb_edit(callback: CallbackQuery, callback_data: kb.ProdCb, session: AsyncSession, state: FSMContext):
    product = await repo.get_product(session, callback.from_user.id, callback_data.id)
    if product is None:
        await callback.answer("Товар не найден", show_alert=True)
        return
    await state.clear()
    await state.set_state(EditProduct.name)
    await state.update_data(product_id=product.id, page=callback_data.page)
    await callback.answer()
    await callback.message.answer(
        f"Сейчас название: <b>{escape(product.name)}</b>\nВведи новое:", reply_markup=kb.skip_kb()
    )


async def edit_ask_brand(message: Message, state: FSMContext):
    await state.set_state(EditProduct.brand)
    await message.answer("Бренд / производитель? (например «Люкс»)", reply_markup=kb.skip_kb())


async def edit_ask_expiry(message: Message, state: FSMContext):
    await state.set_state(EditProduct.expiry)
    await message.answer(EXPIRY_PROMPT, reply_markup=kb.skip_kb())


async def edit_finish(message: Message, session: AsyncSession, state: FSMContext, user_id: int):
    data = await state.get_data()
    await state.clear()
    product = await repo.get_product(session, user_id, data["product_id"])
    if product is None:
        await message.answer("Товар не найден.")
        return
    if data.get("name"):
        product.name = data["name"]
    if data.get("brand"):
        product.brand = data["brand"]
    if data.get("expiry"):
        product.expiry_date = date.fromisoformat(data["expiry"])
    if data.get("production"):
        product.production_date = date.fromisoformat(data["production"])
    await session.commit()
    await message.answer("✅ Сохранено")
    await send_product_card(message, product, data.get("page", 0))


@router.message(EditProduct.name, plain_text)
async def edit_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip()[:200])
    await edit_ask_brand(message, state)


@router.message(EditProduct.brand, plain_text)
async def edit_brand(message: Message, state: FSMContext):
    await state.update_data(brand=message.text.strip()[:200])
    await edit_ask_expiry(message, state)


@router.message(EditProduct.expiry, plain_text)
async def edit_expiry(message: Message, session: AsyncSession, state: FSMContext):
    parsed = parse_date(message.text.strip())
    if parsed is None:
        await message.answer("Не понял дату. Пример: 15.03.2027", reply_markup=kb.skip_kb())
        return
    await state.update_data(expiry=parsed.isoformat())
    await edit_finish(message, session, state, message.from_user.id)


@router.message(StateFilter(AddProduct.expiry, EditProduct.expiry), F.photo)
async def expiry_photo(message: Message, bot: Bot, state: FSMContext):
    wait = await message.answer("🔎 Распознаю даты на фото…")
    file = await bot.download(message.photo[-1])
    scan = await asyncio.to_thread(dates_ocr.recognize, file.read())
    await wait.delete()

    if not scan.expiry and not scan.production:
        seen = escape(" | ".join(scan.lines[:8])) if scan.lines else "текст не найден"
        await message.answer(
            "Не нашёл дат 😕 Сфоткай дату крупно, ровно и без бликов — или введи вручную (ДД.ММ.ГГГГ).\n\n"
            f"<i>Прочитал: {seen}</i>",
            reply_markup=kb.skip_kb(),
        )
        return

    await state.update_data(
        production=scan.production.isoformat() if scan.production else None,
        pending_expiry=scan.expiry.isoformat() if scan.expiry else None,
    )
    lines = ["📅 Распознал:"]
    if scan.production:
        lines.append(f"Изготовлен: <b>{scan.production:%d.%m.%Y}</b>")
    if scan.expiry:
        note = f" (посчитал: изготовлен + {escape(scan.shelf_life)})" if scan.expiry_computed else ""
        lines.append(f"Годен до: <b>{scan.expiry:%d.%m.%Y}</b>{note}")
        if scan.year_guessed:
            lines.append("⚠️ Одна дата пропечаталась не полностью — год подобрал по второй дате, проверь.")
        lines.append("\nВсё верно?")
        await message.answer("\n".join(lines), reply_markup=kb.date_confirm_kb())
    else:
        lines.append("\nА вот «годен до» не нашёл. Введи его вручную (ДД.ММ.ГГГГ) или пришли фото ещё раз:")
        await message.answer("\n".join(lines), reply_markup=kb.skip_kb())


@router.callback_query(StateFilter(AddProduct.expiry, EditProduct.expiry), kb.FormCb.filter(F.action == "date_ok"))
async def cb_date_ok(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    await state.update_data(expiry=data.get("pending_expiry"))
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    if await state.get_state() == AddProduct.expiry.state:
        await save_product(callback.message, session, state)
    else:
        await edit_finish(callback.message, session, state, callback.from_user.id)


@router.callback_query(StateFilter(AddProduct.expiry, EditProduct.expiry), kb.FormCb.filter(F.action == "date_manual"))
async def cb_date_manual(callback: CallbackQuery, state: FSMContext):
    await state.update_data(production=None, pending_expiry=None)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await callback.message.answer("Введи «годен до» (ДД.ММ.ГГГГ):", reply_markup=kb.skip_kb())
