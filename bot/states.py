from aiogram.fsm.state import State, StatesGroup


class WarehouseForm(StatesGroup):
    create_name = State()
    rename = State()


class AddProduct(StatesGroup):
    method = State()
    barcode = State()
    confirm = State()
    name = State()
    brand = State()
    weight = State()
    photo = State()
    quantity = State()
    expiry = State()


class ExpirySearch(StatesGroup):
    date = State()


class EditProduct(StatesGroup):
    name = State()
    brand = State()
    expiry = State()
