import secrets
from datetime import date

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Product, User, Warehouse, WarehouseMember


def accessible_ids(tg_id: int):
    """Подзапрос: id складов, где пользователь владелец или участник."""
    member_of = select(WarehouseMember.warehouse_id).where(WarehouseMember.tg_id == tg_id)
    return select(Warehouse.id).where(or_(Warehouse.owner_id == tg_id, Warehouse.id.in_(member_of)))


async def has_access(session: AsyncSession, tg_id: int, wh: Warehouse) -> bool:
    if wh.owner_id == tg_id:
        return True
    return bool(await session.scalar(
        select(func.count()).select_from(WarehouseMember)
        .where(WarehouseMember.warehouse_id == wh.id, WarehouseMember.tg_id == tg_id)
    ))


async def get_current_warehouse(session: AsyncSession, tg_id: int) -> Warehouse | None:
    user = await session.get(User, tg_id)
    if not user or not user.current_warehouse_id:
        return None
    return await get_warehouse(session, tg_id, user.current_warehouse_id)


async def set_current_warehouse(session: AsyncSession, tg_id: int, warehouse_id: int | None) -> None:
    user = await session.get(User, tg_id)
    if user is None:
        session.add(User(tg_id=tg_id, current_warehouse_id=warehouse_id))
    else:
        user.current_warehouse_id = warehouse_id
    await session.commit()


async def list_warehouses(session: AsyncSession, tg_id: int) -> list[Warehouse]:
    res = await session.scalars(
        select(Warehouse).where(Warehouse.id.in_(accessible_ids(tg_id))).order_by(Warehouse.name)
    )
    return list(res)


async def get_warehouse(session: AsyncSession, tg_id: int, warehouse_id: int) -> Warehouse | None:
    wh = await session.get(Warehouse, warehouse_id)
    return wh if wh and await has_access(session, tg_id, wh) else None


async def warehouse_name_taken(session: AsyncSession, owner_id: int, name: str) -> bool:
    res = await session.scalar(
        select(func.count()).select_from(Warehouse).where(Warehouse.owner_id == owner_id, Warehouse.name == name)
    )
    return bool(res)


async def create_warehouse(session: AsyncSession, owner_id: int, name: str) -> Warehouse:
    wh = Warehouse(owner_id=owner_id, name=name)
    session.add(wh)
    await session.commit()
    return wh


async def rename_warehouse(session: AsyncSession, wh: Warehouse, name: str) -> None:
    wh.name = name
    await session.commit()


async def delete_warehouse(session: AsyncSession, wh: Warehouse) -> None:
    await session.delete(wh)
    await session.commit()


async def get_or_create_invite(session: AsyncSession, wh: Warehouse, regenerate: bool = False) -> str:
    if regenerate or not wh.invite_token:
        wh.invite_token = secrets.token_urlsafe(12)
        await session.commit()
    return wh.invite_token


async def find_by_invite(session: AsyncSession, token: str) -> Warehouse | None:
    return await session.scalar(select(Warehouse).where(Warehouse.invite_token == token))


async def add_member(session: AsyncSession, wh: Warehouse, tg_id: int, name: str) -> bool:
    """Добавляет участника. False — если он уже был."""
    exists = await session.scalar(
        select(WarehouseMember).where(WarehouseMember.warehouse_id == wh.id, WarehouseMember.tg_id == tg_id)
    )
    if exists:
        return False
    session.add(WarehouseMember(warehouse_id=wh.id, tg_id=tg_id, name=name))
    await session.commit()
    return True


async def list_members(session: AsyncSession, warehouse_id: int) -> list[WarehouseMember]:
    res = await session.scalars(
        select(WarehouseMember).where(WarehouseMember.warehouse_id == warehouse_id).order_by(WarehouseMember.created_at)
    )
    return list(res)


async def remove_member(session: AsyncSession, warehouse_id: int, tg_id: int) -> WarehouseMember | None:
    member = await session.scalar(
        select(WarehouseMember).where(WarehouseMember.warehouse_id == warehouse_id, WarehouseMember.tg_id == tg_id)
    )
    if member:
        await session.delete(member)
        await session.commit()
    return member


async def list_all_products(session: AsyncSession, warehouse_id: int) -> list[Product]:
    """Все товары: сначала с ближайшим сроком, без срока — в конце."""
    res = await session.scalars(
        select(Product)
        .where(Product.warehouse_id == warehouse_id)
        .order_by(Product.expiry_date.is_(None), Product.expiry_date, Product.name)
    )
    return list(res)


async def list_warehouses_for_many(session: AsyncSession, tg_ids: list[int]) -> list[Warehouse]:
    """Склады, доступные хотя бы одному из пользователей (для канала — его админам)."""
    if not tg_ids:
        return []
    member_of = select(WarehouseMember.warehouse_id).where(WarehouseMember.tg_id.in_(tg_ids))
    res = await session.scalars(
        select(Warehouse)
        .where(or_(Warehouse.owner_id.in_(tg_ids), Warehouse.id.in_(member_of)))
        .order_by(Warehouse.name)
    )
    return list(res)


async def count_products(session: AsyncSession, warehouse_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(Product).where(Product.warehouse_id == warehouse_id)
    ) or 0


async def list_products(session: AsyncSession, warehouse_id: int, offset: int, limit: int) -> list[Product]:
    res = await session.scalars(
        select(Product)
        .where(Product.warehouse_id == warehouse_id)
        .order_by(Product.name)
        .offset(offset)
        .limit(limit)
    )
    return list(res)


async def get_product(session: AsyncSession, tg_id: int, product_id: int) -> Product | None:
    product = await session.get(Product, product_id)
    if product is None:
        return None
    wh = await get_warehouse(session, tg_id, product.warehouse_id)
    return product if wh else None


async def change_quantity(session: AsyncSession, product: Product, delta: int) -> None:
    """Атомарно: два человека могут жать «+1» одновременно, и ни одно нажатие не потеряется."""
    await session.execute(
        update(Product)
        .where(Product.id == product.id)
        .values(quantity=func.max(Product.quantity + delta, 0))
    )
    await session.commit()
    await session.refresh(product)


async def find_by_barcode(session: AsyncSession, warehouse_id: int, barcode: str) -> Product | None:
    return await session.scalar(
        select(Product).where(Product.warehouse_id == warehouse_id, Product.barcode == barcode).limit(1)
    )


async def find_by_barcode_anywhere(session: AsyncSession, tg_id: int, barcode: str) -> Product | None:
    """Ищем товар с таким штрихкодом на других доступных складах — чтобы не вводить данные заново."""
    return await session.scalar(
        select(Product)
        .where(Product.warehouse_id.in_(accessible_ids(tg_id)), Product.barcode == barcode)
        .order_by(Product.created_at.desc())
        .limit(1)
    )


async def create_product(
    session: AsyncSession,
    warehouse_id: int,
    *,
    name: str,
    barcode: str | None = None,
    brand: str | None = None,
    weight: str | None = None,
    description: str | None = None,
    photo_file_id: str | None = None,
    photo_url: str | None = None,
    quantity: int = 1,
    production_date: date | None = None,
    expiry_date: date | None = None,
) -> Product:
    product = Product(
        warehouse_id=warehouse_id,
        name=name,
        barcode=barcode,
        brand=brand,
        weight=weight,
        description=description,
        photo_file_id=photo_file_id,
        photo_url=photo_url,
        quantity=quantity,
        production_date=production_date,
        expiry_date=expiry_date,
    )
    session.add(product)
    await session.commit()
    return product


async def list_by_expiry(
    session: AsyncSession,
    warehouse_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
) -> list[Product]:
    """Товары со сроком годности в диапазоне [date_from, date_to], сначала те, что истекают раньше."""
    query = select(Product).where(Product.warehouse_id == warehouse_id, Product.expiry_date.is_not(None))
    if date_from is not None:
        query = query.where(Product.expiry_date >= date_from)
    if date_to is not None:
        query = query.where(Product.expiry_date <= date_to)
    res = await session.scalars(query.order_by(Product.expiry_date, Product.name).limit(limit))
    return list(res)


async def delete_product(session: AsyncSession, product: Product) -> None:
    await session.delete(product)
    await session.commit()
