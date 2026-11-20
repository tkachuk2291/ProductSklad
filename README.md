# ProductSklad

Telegram bot for tracking products in warehouses with expiry dates. Warehouses can be shared with other people through an invite link.

## Features

- **Warehouses** with shared access via invite link.
- **Add a product** by barcode (photo or text) or manually. Barcodes are decoded locally (EAN/UPC, GS1 and DataMatrix codes) and product data is looked up in Open Food Facts.
- **Expiry dates from a photo.** Local OCR (RapidOCR with a Ukrainian/Russian model, no external API) reads production and expiry dates from the package photo.
- **Expiry search:** expired items, items expiring soon, or items expiring by a chosen date.
- **Quantity** changes with +/- buttons (atomic updates), edit and delete.
- **Groups and channels:** `/sklad` shows a warehouse inside a group or channel.

Commands: `/add`, `/items`, `/expiry`, `/sklad`.

## Stack

Python 3.12, aiogram 3 (FSM, callback factories, middlewares), SQLAlchemy 2 (async) + SQLite (aiosqlite), zxing-cpp, RapidOCR + onnxruntime, Pillow, numpy, aiohttp, Docker Compose.

## Run

1. Create a bot with @BotFather.
2. Copy `.env.example` to `.env`:

   | Variable | Purpose |
   | --- | --- |
   | `BOT_TOKEN` | Telegram bot token |
   | `DATABASE_URL` | default `sqlite+aiosqlite:///warehouse.db` |
   | `PAGE_SIZE` | items per page, default 10 |

3. Start:

   ```bash
   pip install -r requirements.txt
   python main.py
   ```

   or with Docker: `docker compose up -d --build` (the database lives in the `sklad_data` volume).

The OCR models are downloaded on the first build or start. There are no database migrations: new columns are added on startup (`_add_missing_columns`). Dialog state is kept in memory, so unfinished dialogs are lost on restart.

## Project layout

```
main.py              entry point, middlewares
bot/config.py        settings
bot/db/              SQLAlchemy models, repository, session
bot/handlers/        start, warehouses, products, expiry, group
bot/services/        barcode.py (barcode + Open Food Facts), dates_ocr.py (date OCR)
bot/states.py        FSM states
bot/keyboards.py     inline keyboards and callback data
```
