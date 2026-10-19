# ProductSklad — база знаний

Бот @product_sklad_bot: учёт товаров на складах со сроками годности. Склады с совместным доступом по
инвайт-ссылке, добавление товара по штрихкоду (фото или текст) или вручную, распознавание дат с фото упаковки,
поиск по сроку годности, просмотр в группах и каналах через /sklad.

## Устройство
- `main.py` — запуск aiogram, middleware: `aiogram_middleware` (контекст логов) → `DbSessionMiddleware` (сессия БД).
- `bot/db` — SQLAlchemy async + SQLite (`DATABASE_URL`, по умолчанию `sqlite+aiosqlite:///warehouse.db`, на сервере в `/app/data`).
  Модели: User, Warehouse, WarehouseMember, Product. `_add_missing_columns` досоздаёт новые колонки в старой базе (миграций нет).
  `change_quantity` атомарный.
- `bot/handlers` — start, warehouses, products (добавление/редактирование, FSM в `states.py`), expiry, group.
  Хаб — `common.replace_message` (текст редактирует, фото заменяет новым сообщением).
- `bot/services/barcode.py` — штрихкоды (EAN/UPC, GS1/DataMatrix/«Честный ЗНАК»), поиск в Open Food Facts.
- `bot/services/dates_ocr.py` — RapidOCR (укр/рус), даты изготовления/годности; `warmup` грузит модели при старте.
- FSM в `MemoryStorage` — незаконченные диалоги теряются при перезапуске.

## Деплой
- Стек Portainer `productsklad`, файлы: `/var/lib/docker/volumes/portainer_data/_data/compose/3`, секреты в `stack.env`.
- Том `sklad_data:/app/data` — база `warehouse.db`.
- Dockerfile копирует файлы выборочно (`bot/`, `main.py`); новые файлы в корне нужно добавить в COPY. Поэтому `logsetup.py` лежит в `bot/`.
- Сборка скачивает модели OCR (~20 МБ) с modelscope.cn.

## Логи
- JSON (`bot/logsetup.py`), bot=sklad-bot. При старте строка `OCR готов: [...]` — признак, что модели загрузились.

## Инциденты
- **2026-10-18** — сборка образа упала: `ReadTimeoutError` при скачивании моделей с `cdn-lfs-cn-1.modelscope.cn`.
  Работающий контейнер не пострадал. Решение: повторить сборку (со второй попытки прошла).
  Если повторится часто — кэшировать модели в отдельном образе или скачивать при старте с ретраями.
