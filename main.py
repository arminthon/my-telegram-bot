# main.py — فروشگاه عطر آرمین 🌸
# python-telegram-bot v20+ (webhooks-ready for Render)

import os
import logging
from datetime import datetime

import pytz
import pandas as pd
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# ----------------------------- Config -----------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT-YOUR-TOKEN-HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "93543054"))

DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL on Render
EXCEL_FILE = os.path.join(os.path.dirname(__file__), "products.xlsx")

TEHRAN = pytz.timezone("Asia/Tehran")
CARD_NUMBER = "6037-9975-1234-5678"
CARD_HOLDER = "بانک ملی - به نام آرمین"
SHOP_NAME = "🌸 فروشگاه عطر آرمین 🌸"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ----------------------------- Database -----------------------------
import sqlite3

def get_conn():
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        return conn, "pg"
    conn = sqlite3.connect("shop.db", check_same_thread=False)
    return conn, "sqlite"

def db_setup():
    conn, kind = get_conn()
    cur = conn.cursor()
    if kind == "pg":
        # sequence for order_id (fixes NOT NULL constraint error)
        cur.execute("CREATE SEQUENCE IF NOT EXISTS orders_order_id_seq OWNED BY orders.order_id;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id   BIGINT PRIMARY KEY DEFAULT nextval('orders_order_id_seq'),
                user_id    BIGINT NOT NULL,
                username   TEXT,
                sku        TEXT NOT NULL,
                product_name TEXT,
                qty        INT  NOT NULL DEFAULT 1,
                price_toman BIGINT,
                status     TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        cur.execute("SELECT setval('orders_order_id_seq', COALESCE((SELECT MAX(order_id) FROM orders), 1));")
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                username   TEXT,
                sku        TEXT NOT NULL,
                product_name TEXT,
                qty        INTEGER NOT NULL DEFAULT 1,
                price_toman INTEGER,
                status     TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            );
        """)
    conn.commit()
    cur.close()
    conn.close()

def record_order(user_id, username, sku, product_name, qty, price_toman):
    conn, kind = get_conn()
    cur = conn.cursor()
    if kind == "pg":
        cur.execute(
            """INSERT INTO orders (user_id, username, sku, product_name, qty, price_toman)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING order_id;""",
            (user_id, username, sku, product_name, qty, int(price_toman or 0)),
        )
        order_id = cur.fetchone()[0]
    else:
        cur.execute(
            """INSERT INTO orders (user_id, username, sku, product_name, qty, price_toman, created_at)
               VALUES (?,?,?,?,?,?,?);""",
            (user_id, username, sku, product_name, qty, int(price_toman or 0),
             datetime.now(TEHRAN).isoformat()),
        )
        order_id = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    return order_id

def fetch_orders(limit=50):
    conn, kind = get_conn()
    cur = conn.cursor()
    ph = "%s" if kind == "pg" else "?"
    cur.execute(f"SELECT * FROM orders ORDER BY order_id DESC LIMIT {int(limit)};")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close(); conn.close()
    return [dict(zip(cols, r)) for r in rows]

# ----------------------------- Excel Products -----------------------------
def get_products(only_active=True):
    """Reads the 10-column schema from products.xlsx."""
    if not os.path.exists(EXCEL_FILE):
        return []
    df = pd.read_excel(EXCEL_FILE)
    df.columns = [str(c).strip().lower() for c in df.columns]
    required = ["sku", "product_name", "brand", "volume_ml", "price_toman",
                "stock_qty", "active", "tags", "description", "photo_file_id"]
    for col in required:
        if col not in df.columns:
            df[col] = None
    if only_active:
        df = df[df["active"].astype(str).str.lower().isin(["true", "1", "yes", "فعال", "بله"])]
    df = df.dropna(subset=["sku", "product_name"])
    return df.to_dict("records")

def get_product_by_sku(sku):
    for p in get_products(only_active=False):
        if str(p.get("sku")).strip() == str(sku).strip():
            return p
    return None

def fmt_price(v):
    try:
        return f"{int(float(v)):,} تومان"
    except (TypeError, ValueError):
        return "نامشخص"

# ----------------------------- Keyboards -----------------------------
def main_menu():
    kb = [["🛍 مشاهده محصولات", "🧾 پیگیری سفارش"],
          ["💳 اطلاعات پرداخت", "📞 پشتیبانی"]]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

# ----------------------------- Handlers -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"سلام {update.effective_user.first_name} عزیز! 👋\n"
        f"به {SHOP_NAME} خوش آمدید.\n\nاز منوی زیر انتخاب کنید:",
        reply_markup=main_menu(),
    )

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == "🛍 مشاهده محصولات":
        products = get_products()
        if not products:
            await update.message.reply_text("فعلاً محصولی برای فروش موجود نیست. 🙏")
            return
        for p in products[:25]:
            caption = (
                f" parfum {p.get('product_name')}\n"
                f"🏷 برند: {p.get('brand') or '—'}\n"
                f"💧 حجم: {p.get('volume_ml') or '—'} میلی‌لیتر\n"
                f"💰 قیمت: {fmt_price(p.get('price_toman'))}\n"
                f"📦 موجودی: {p.get('stock_qty') or 0} عدد\n"
                f"🌸 رایحه: {p.get('tags') or '—'}\n\n"
                f"📝 {p.get('description') or ''}\n\n"
                f"برای خرید: /buy_{p.get('sku')}"
            )
            if p.get("photo_file_id"):
                await update.message.reply_photo(p["photo_file_id"], caption=caption)
            else:
                await update.message.reply_text(caption)

    elif text == "🧾 پیگیری سفارش":
        orders = [o for o in fetch_orders(200) if o["user_id"] == update.effective_user.id]
        if not orders:
            await update.message.reply_text("هنوز سفارشی ثبت نکرده‌اید.")
            return
        lines = [f"🧾 سفارش #{o['order_id']} | {o.get('product_name') or o['sku']} | {o['status']}"
                 for o in orders[:15]]
        await update.message.reply_text("\n".join(lines))

    elif text == "💳 اطلاعات پرداخت":
        await update.message.reply_text(
            f"💳 کارت به کارت:\n`{CARD_NUMBER}`\n👤 {CARD_HOLDER}\n\n"
            "پس از واریز، رسید را برای ادمین بفرستید."
        )

    elif text == "📞 پشتیبانی":
        await update.message.reply_text(f"برای پشتیبانی با ادمین در تماس باشید: @{os.getenv('SUPPORT_USERNAME', 'admin')}")

# ---------------- Buy (conversation) ----------------
BUY_SKU, BUY_QTY = range(2)

async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sku = update.message.text.replace("/buy_", "").strip()
    p = get_product_by_sku(sku)
    if not p:
        await update.message.reply_text("این محصول یافت نشد.")
        return ConversationHandler.END
    try:
        stock = int(float(p.get("stock_qty") or 0))
    except (TypeError, ValueError):
        stock = 0
    if stock <= 0:
        await update.message.reply_text("😢 این عطر فعلاً ناموجود است.")
        return ConversationHandler.END
    context.user_data["product"] = p
    await update.message.reply_text(f"چند عدد {p.get('product_name')} می‌خواهید؟ (موجودی: {stock})")
    return BUY_QTY

async def buy_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p = context.user_data.get("product")
    if not p:
        return ConversationHandler.END
    try:
        qty = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("لطفاً فقط عدد بفرستید.")
        return BUY_QTY
    stock = int(float(p.get("stock_qty") or 0))
    if qty < 1 or qty > stock:
        await update.message.reply_text(f"عدد بین ۱ تا {stock} وارد کنید.")
        return BUY_QTY

    price = p.get("price_toman")
    total = int(float(price or 0)) * qty
    order_id = record_order(
        update.effective_user.id, update.effective_user.username,
        p["sku"], p.get("product_name"), qty, price,
    )
    await update.message.reply_text(
        f"✅ سفارش #{order_id} ثبت شد!\n\n"
        f"{p.get('product_name')} × {qty}\n"
        f"مبلغ قابل پرداخت: {fmt_price(total)}\n\n"
        f"💳 کارت: `{CARD_NUMBER}`\n👤 {CARD_HOLDER}\n\n"
        f"پس از واریز، رسید را بفرستید. 🌸",
        reply_markup=main_menu(),
    )
    # notify admin
    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"🔔 سفارش جدید #{order_id}\n"
            f"👤 @{update.effective_user.username} ({update.effective_user.id})\n"
            f"🛍 {p.get('product_name')} × {qty} = {fmt_price(total)}",
        )
    except Exception:
        pass
    context.user_data.clear()
    return ConversationHandler.END

# ---------------- Admin: get file_id of forwarded photo ----------------
async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forward any photo (or send it as photo) to the bot → get its file_id."""
    if update.effective_user.id != ADMIN_ID:
        return
    msg = update.message
    fid = None
    if msg.photo:
        fid = msg.photo[-1].file_id          # highest resolution
    elif msg.reply_to_message and msg.reply_to_message.photo:
        fid = msg.reply_to_message.photo[-1].file_id
    if fid:
        await msg.reply_text(
            f"📸 file_id عکس:\n\n`{fid}`\n\n"
            "این مقدار را در ستون photo_file_id فایل products.xlsx کپی کنید.",
        )
    else:
        await msg.reply_text("عکس را به صورت Photo (نه فایل) بفرستید یا روی یک عکس ریپلای کنید.")

async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    conn, kind = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM orders ORDER BY order_id DESC LIMIT 100;")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close(); conn.close()
    orders = [dict(zip(cols, r)) for r in rows]
    if not orders:
        await update.message.reply_text("سفارشی ثبت نشده است.")
        return
    df = pd.DataFrame(orders)
    path = os.path.join(os.path.dirname(__file__), "orders_report.xlsx")
    df.to_excel(path, index=False)
    with open(path, "rb") as f:
        await update.message.reply_document(f, filename="orders_report.xlsx")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("دستور نامفهوم است. از منوی اصلی استفاده کنید. 🌸", reply_markup=main_menu())

# ----------------------------- Main -----------------------------
def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    buy_conv = ConversationHandler(
        entry_points=[CommandHandler(lambda c: c.startswith("buy_"), buy_start)],
        states={BUY_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_qty)]},
        fallbacks=[],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("getid", getid))
    app.add_handler(CommandHandler("orders", admin_orders))
    app.add_handler(buy_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    return app

def main():
    db_setup()
    app = build_app()
    # Render: webhook mode
    if os.getenv("RENDER"):
        port = int(os.getenv("PORT", "8443"))
        app.run_webhook(
            listen="0.0.0.0", port=port,
            url_path=BOT_TOKEN.split(":")[0],
            webhook_url=f"https://{os.getenv('RENDER_EXTERNAL_HOST')}/{BOT_TOKEN.split(':')[0]}",
        )
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
