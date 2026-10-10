from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    """Пользователь Telegram и выбранный им склад."""

    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    current_warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True
    )


class Warehouse(Base):
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_warehouse_owner_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(100))
    invite_token: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)  # для ссылки-приглашения
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    members: Mapped[list["WarehouseMember"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    products: Mapped[list["Product"]] = relationship(
        back_populates="warehouse", cascade="all, delete-orphan", passive_deletes=True
    )


class WarehouseMember(Base):
    """Пользователь, которому владелец открыл доступ к складу."""

    __tablename__ = "warehouse_members"
    __table_args__ = (UniqueConstraint("warehouse_id", "tg_id", name="uq_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="CASCADE"), index=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(200))  # имя в Telegram — чтобы владелец видел, кто есть
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="CASCADE"), index=True)
    barcode: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(200))
    weight: Mapped[str | None] = mapped_column(String(50))  # «150 г», «1 л» — как написано на упаковке
    description: Mapped[str | None] = mapped_column(Text)
    photo_file_id: Mapped[str | None] = mapped_column(String(255))  # фото, загруженное в Telegram
    photo_url: Mapped[str | None] = mapped_column(String(500))  # фото из Open Food Facts
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    production_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    warehouse: Mapped[Warehouse] = relationship(back_populates="products")
