from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import Product, Warehouse

BTN_WAREHOUSES = "🏬 Склады"
BTN_PRODUCTS = "📦 Товары"
BTN_ADD = "➕ Добавить товар"
BTN_EXPIRY = "⏰ Сроки годности"


class WhCb(CallbackData, prefix="wh"):
    action: str  # list | open | new | settings | rename | delete | delete_ok | access | regen | kick | leave | leave_ok
    id: int = 0
    uid: int = 0  # tg_id участника (для kick)


class ProdCb(CallbackData, prefix="p"):
    action: str  # list | view | inc | dec | edit | delete | delete_ok | add
    id: int = 0
    page: int = 0


class FormCb(CallbackData, prefix="f"):
    action: str  # barcode | manual | skip | confirm | date_ok | date_manual | qty_manual | cancel


class ExpCb(CallbackData, prefix="exp"):
    mode: str  # menu | expired | soon | date
    days: int = 0


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_WAREHOUSES), KeyboardButton(text=BTN_PRODUCTS)],
                  [KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_EXPIRY)]],
        resize_keyboard=True,
    )


def warehouses_kb(warehouses: list[Warehouse], current_id: int | None, user_id: int | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for wh in warehouses:
        mark = "✅ " if wh.id == current_id else ""
        shared = " 👥" if user_id is not None and wh.owner_id != user_id else ""  # чужой склад, к которому дали доступ
        kb.button(text=f"{mark}{wh.name}{shared}", callback_data=WhCb(action="open", id=wh.id))
    kb.button(text="➕ Новый склад", callback_data=WhCb(action="new"))
    kb.adjust(1)
    return kb.as_markup()


def warehouse_settings_kb(wh_id: int, is_owner: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Переименовать", callback_data=WhCb(action="rename", id=wh_id))
    if is_owner:
        kb.button(text="👥 Доступ для других", callback_data=WhCb(action="access", id=wh_id))
        kb.button(text="🗑 Удалить склад", callback_data=WhCb(action="delete", id=wh_id))
    else:
        kb.button(text="🚪 Выйти со склада", callback_data=WhCb(action="leave", id=wh_id))
    kb.button(text="⬅️ К товарам", callback_data=ProdCb(action="list"))
    kb.adjust(1)
    return kb.as_markup()


def access_kb(wh_id: int, members: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for m in members:
        kb.button(text=f"❌ Убрать: {m.name}", callback_data=WhCb(action="kick", id=wh_id, uid=m.tg_id))
    kb.button(text="🔄 Новая ссылка (старая перестанет работать)", callback_data=WhCb(action="regen", id=wh_id))
    kb.button(text="⬅️ Назад", callback_data=WhCb(action="settings", id=wh_id))
    kb.adjust(1)
    return kb.as_markup()


class ChatCb(CallbackData, prefix="ch"):
    """Кнопки в группе/канале. by — кто открыл меню (0 в канале): по нему проверяем доступ к складу."""
    action: str  # list | wh | prod
    wh: int = 0
    pid: int = 0
    page: int = 0
    by: int = 0


def chat_warehouses_kb(warehouses: list[Warehouse], by: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for wh in warehouses:
        kb.button(text=f"🏬 {wh.name}", callback_data=ChatCb(action="wh", wh=wh.id, by=by))
    kb.adjust(1)
    return kb.as_markup()


def chat_products_kb(
    products: list[Product], marks: list[str], wh_id: int, page: int, has_next: bool, by: int, bot_url: str
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p, mark in zip(products, marks):
        kb.button(text=f"{mark} {p.name} · {p.quantity} шт",
                  callback_data=ChatCb(action="prod", wh=wh_id, pid=p.id, page=page, by=by))
    kb.adjust(1)
    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="◀️", callback_data=ChatCb(action="wh", wh=wh_id, page=page - 1, by=by))
    nav.button(text="🔄", callback_data=ChatCb(action="wh", wh=wh_id, page=page, by=by))
    if has_next:
        nav.button(text="▶️", callback_data=ChatCb(action="wh", wh=wh_id, page=page + 1, by=by))
    kb.attach(nav)
    footer = InlineKeyboardBuilder()
    footer.button(text="⬅️ Склады", callback_data=ChatCb(action="list", by=by))
    footer.button(text="🤖 Добавить товары", url=bot_url)
    footer.adjust(2)
    kb.attach(footer)
    return kb.as_markup()


def chat_product_kb(wh_id: int, page: int, by: int, bot_url: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ К списку", callback_data=ChatCb(action="wh", wh=wh_id, page=page, by=by))
    kb.button(text="🤖 Изменить в боте", url=bot_url)
    kb.adjust(2)
    return kb.as_markup()


def confirm_leave_kb(wh_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, выйти", callback_data=WhCb(action="leave_ok", id=wh_id))
    kb.button(text="Отмена", callback_data=WhCb(action="settings", id=wh_id))
    kb.adjust(2)
    return kb.as_markup()


def confirm_delete_warehouse_kb(wh_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, удалить всё", callback_data=WhCb(action="delete_ok", id=wh_id))
    kb.button(text="Отмена", callback_data=WhCb(action="settings", id=wh_id))
    kb.adjust(2)
    return kb.as_markup()


def products_kb(products: list[Product], wh_id: int, page: int, has_next: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in products:
        kb.button(text=f"{p.name} · {p.quantity} шт", callback_data=ProdCb(action="view", id=p.id, page=page))
    kb.adjust(1)

    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text="◀️", callback_data=ProdCb(action="list", page=page - 1))
    if has_next:
        nav.button(text="▶️", callback_data=ProdCb(action="list", page=page + 1))
    kb.attach(nav)

    footer = InlineKeyboardBuilder()
    footer.button(text="➕ Добавить товар", callback_data=ProdCb(action="add"))
    footer.button(text="⏰ Сроки", callback_data=ExpCb(mode="menu"))
    footer.button(text="⚙️ Склад", callback_data=WhCb(action="settings", id=wh_id))
    footer.button(text="⬅️ Склады", callback_data=WhCb(action="list"))
    footer.adjust(1, 3)
    kb.attach(footer)
    return kb.as_markup()


def product_card_kb(product_id: int, page: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➖ 1", callback_data=ProdCb(action="dec", id=product_id, page=page))
    kb.button(text="➕ 1", callback_data=ProdCb(action="inc", id=product_id, page=page))
    kb.button(text="✏️ Изменить", callback_data=ProdCb(action="edit", id=product_id, page=page))
    kb.button(text="🗑 Удалить", callback_data=ProdCb(action="delete", id=product_id, page=page))
    kb.button(text="⬅️ К списку", callback_data=ProdCb(action="list", page=page))
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def confirm_delete_product_kb(product_id: int, page: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, удалить", callback_data=ProdCb(action="delete_ok", id=product_id, page=page))
    kb.button(text="Отмена", callback_data=ProdCb(action="view", id=product_id, page=page))
    kb.adjust(2)
    return kb.as_markup()


def skip_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Пропустить ⏭", callback_data=FormCb(action="skip"))
    kb.button(text="Отмена", callback_data=FormCb(action="cancel"))
    kb.adjust(2)
    return kb.as_markup()


def add_method_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📷 По штрихкоду", callback_data=FormCb(action="barcode"))
    kb.button(text="✍️ Вручную", callback_data=FormCb(action="manual"))
    kb.button(text="Отмена", callback_data=FormCb(action="cancel"))
    kb.adjust(2, 1)
    return kb.as_markup()


def barcode_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✍️ Ввести вручную", callback_data=FormCb(action="manual"))
    kb.button(text="Отмена", callback_data=FormCb(action="cancel"))
    kb.adjust(2)
    return kb.as_markup()


def confirm_found_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, это он", callback_data=FormCb(action="confirm"))
    kb.button(text="✏️ Нет, ввести вручную", callback_data=FormCb(action="manual"))
    kb.button(text="Отмена", callback_data=FormCb(action="cancel"))
    kb.adjust(1)
    return kb.as_markup()


def expiry_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Просроченные", callback_data=ExpCb(mode="expired"))
    kb.button(text="⚠️ Истекают за 3 дня", callback_data=ExpCb(mode="soon", days=3))
    kb.button(text="За 7 дней", callback_data=ExpCb(mode="soon", days=7))
    kb.button(text="За 30 дней", callback_data=ExpCb(mode="soon", days=30))
    kb.button(text="📅 Годные до даты…", callback_data=ExpCb(mode="date"))
    kb.button(text="⬅️ К товарам", callback_data=ProdCb(action="list"))
    kb.adjust(1, 1, 2, 1, 1)
    return kb.as_markup()


def expiry_results_kb(products: list[Product], marks: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p, mark in zip(products, marks):
        kb.button(
            text=f"{mark} {p.name} · до {p.expiry_date:%d.%m.%y} · {p.quantity} шт",
            callback_data=ProdCb(action="view", id=p.id),
        )
    kb.button(text="⬅️ Другой фильтр", callback_data=ExpCb(mode="menu"))
    kb.adjust(1)
    return kb.as_markup()


def date_confirm_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Верно", callback_data=FormCb(action="date_ok"))
    kb.button(text="✍️ Ввести вручную", callback_data=FormCb(action="date_manual"))
    kb.button(text="Отмена", callback_data=FormCb(action="cancel"))
    kb.adjust(2, 1)
    return kb.as_markup()


class QtyCb(CallbackData, prefix="q"):
    n: int


def quantity_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for n in (1, 2, 3):
        kb.button(text=f"+{n}", callback_data=QtyCb(n=n))
    kb.button(text="✍️ Ввести вручную", callback_data=FormCb(action="qty_manual"))
    kb.button(text="Отмена", callback_data=FormCb(action="cancel"))
    kb.adjust(3, 2)
    return kb.as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Отмена", callback_data=FormCb(action="cancel"))
    return kb.as_markup()


MENU_BUTTONS = {BTN_WAREHOUSES, BTN_PRODUCTS, BTN_ADD, BTN_EXPIRY}
