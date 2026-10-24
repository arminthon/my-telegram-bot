# main.py — فروشگاه عطر آرمین 🌸
# python-telegram-bot v20+ (Render Webhook)

import os
import logging
from datetime import datetime

import pytz
import pandas as pd
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

# ----------------------------- Config -----------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT-YOUR-TOKEN-HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "93543054"))

DATABASE_URL = os.getenv("DATABASE_URL")
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
        return psycopg2.connect(DATABASE_URL, sslmode="require"), "pg"
    return sqlite3.connect("shop.db", check_same_thread=False), "sqlite"

def db_setup():
    conn, kind = get_conn()
    cur = conn.cursor()
    if kind == "pg":
        # 1. ساخت جدول اولیه در صورت نبودن
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL
            );
        """)
        # 2. حل خطای ستون‌ها: اضافه کردن خودکار تمام ستون‌های لازم به جدول رندر
        columns_to_ensure = [
            ("username", "TEXT"),
            ("sku", "TEXT"),
            ("product_name", "TEXT"),
            ("qty", "INT DEFAULT 1"),
            ("price_toman", "BIGINT DEFAULT 0"),
            ("status", "TEXT DEFAULT 'در انتظار پرداخت'"),
            ("created_at", "TIMESTAMPTZ DEFAULT now()")
        ]
        for col_name, col_type in columns_to_ensure:
            cur.execute(f"ALTER TABLE orders ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
        
        # اطمینان از مقداردهی پیش‌فرض sequence برای order_id
        cur.execute("CREATE SEQUENCE IF NOT EXISTS orders_order_id_seq;")
        cur.execute("ALTER TABLE orders ALTER COLUMN order_id SET DEFAULT nextval('orders_order_id_seq');")
        cur.execute("SELECT setval('orders_order_id_seq', COALESCE((SELECT MAX(order_id) FROM orders), 1));")
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                username     TEXT,
                sku          TEXT NOT NULL,
                product_name TEXT,
                qty          INTEGER NOT NULL DEFAULT 1,
                price_toman  INTEGER,
                status       TEXT NOT NULL DEFAULT 'در انتظار پرداخت',
                created_at   TEXT NOT NULL
            );
        """)
    conn.commit()
    cur.close()
    conn.close()

def record_order(user_id, username, sku, product_name, qty, price_toman):
    conn, kind = get_conn()
    cur = conn.cursor()
    safe_price = int(float(price_toman or 0))
    if kind == "pg":
        cur.execute(
            """INSERT INTO orders (user_id, username, sku, product_name, qty, price_toman)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING order_id;""",
            (user_id, str(username or ""), str(sku), str(product_name or ""), qty, safe_price),
        )
        order_id = cur.fetchone()[0]
    else:
        cur.execute(
            """INSERT INTO orders (user_id, username, sku, product_name, qty, price_toman, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?);""",
            (user_id, str(username or ""), str(sku), str(product_name or ""), qty, safe_price, datetime.now(TEHRAN).isoformat()),
        )
        order_id = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    return order_id

def fetch_orders(limit=50):
    conn, kind = get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM orders ORDER BY order_id DESC LIMIT {int(limit)};")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close()
    conn.close()
    return [dict(zip(cols, r)) for r in rows]

# ----------------------------- Excel Products -----------------------------
def get_products(only_active=True):
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
    kb = [
        ["🛍 مشاهده محصولات", "🧾 پیگیری سفارش"],
        ["💳 اطلاعات پرداخت", "📞 پشتیبانی"]
    ]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

# ----------------------------- Handlers -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name or "عزیز"
    await update.message.reply_text(
        f"سلام {user_name} خوش آمدید! 👋\n"
        f"به {SHOP_NAME} خوش آمدید.\n\n"
        "از منوی زیر استفاده کنید:",
        reply_markup=main_menu(),
    )

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == "🛍 مشاهده محصولات":
        products = get_products()
        if not products:
            await update.message.reply_text("فعلاً محصولی در انبار تعریف نشده یا موجود نیست. 🙏")
            return

        for p in products[:25]:
            caption = (
                f"🌸 {p.get('product_name')}\n"
                f"🏷 برند: {p.get('brand') or '—'}\n"
                f"💧 حجم: {p.get('volume_ml') or '—'} میل\n"
                f"💰 قیمت: {fmt_price(p.get('price_toman'))}\n"
                f"📦 موجودی انبار: {p.get('stock_qty') or 0} عدد\n"
                f"✨ رایحه و مشخصات: {p.get('tags') or '—'}\n\n"
                f"📝 {p.get('description') or ''}\n\n"
                f"🛒 ثبت سفارش: /buy_{p.get('sku')}"
            )
            photo = str(p.get("photo_file_id") or "").strip()
            if photo and photo.lower() not in ["nan", "none"]:
                try:
                    await update.message.reply_photo(photo, caption=caption)
                except Exception:
                    await update.message.reply_text(caption)
            else:
                await update.message.reply_text(caption)

    elif text == "🧾 پیگیری سفارش":
        user_orders = [o for o in fetch_orders(100) if o.get("user_id") == update.effective_user.id]
        if not user_orders:
            await update.message.reply_text("شما هنوز هیچ سفارشی ثبت نکرده‌اید.")
            return

        lines = ["📋 آخرین سفارش‌های شما:\n"]
        for o in user_orders[:10]:
            p_price = fmt_price(o.get('price_toman'))
            lines.append(
                f"🧾 شماره سفارش: #{o.get('order_id')}\n"
                f"🛍 محصول: {o.get('product_name') or o.get('sku')}\n"
                f"🔢 تعداد: {o.get('qty')} عدد | مبلغ: {p_price}\n"
                f"📌 وضعیت: {o.get('status')}\n"
                f"──────────────"
            )
        await update.message.reply_text("\n".join(lines))

    elif text == "💳 اطلاعات پرداخت":
        await update.message.reply_text(
            f"💳 شماره کارت جهت پرداخت:\n`{CARD_NUMBER}`\n"
            f"👤 صاحب حساب: {CARD_HOLDER}\n\n"
            "لطفاً پس از واریز، تصویر فیش را ارسال فرمایید."
        )

    elif text == "📞 پشتیبانی":
        await update.message.reply_text("جهت پشتیبانی:\n@Armin (یا ارسال پیام به ادمین)")

# ----------------------------- Buy Flow -----------------------------
BUY_QTY = 1

async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    sku = text.replace("/buy_", "").strip()
    p = get_product_by_sku(sku)
    if not p:
        await update.message.reply_text("محصول مورد نظر یافت نشد.")
        return ConversationHandler.END

    try:
        stock = int(float(p.get("stock_qty") or 0))
    except (TypeError, ValueError):
        stock = 0

    if stock <= 0:
        await update.message.reply_text("متأسفانه موجودی این محصول در انبار به اتمام رسیده است.")
        return ConversationHandler.END

    context.user_data["product"] = p
    await update.message.reply_text(
        f"شما در حال خرید «{p.get('product_name')}» هستید.\n"
        f"موجودی انبار: {stock} عدد\n\n"
        "لطفاً تعداد درخواستی خود را به عدد (مثلاً 1) ارسال کنید:"
    )
    return BUY_QTY

async def buy_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p = context.user_data.get("product")
    if not p:
        await update.message.reply_text("خطایی در اطلاعات پیش آمد. مجدداً محصول را انتخاب کنید.")
        return ConversationHandler.END

    try:
        qty = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("لطفاً تعداد را فقط به صورت عدد وارد کنید:")
        return BUY_QTY

    stock = int(float(p.get("stock_qty") or 0))
    if qty < 1 or qty > stock:
        await update.message.reply_text(f"تعداد نامعتبر است. می‌توانید بین ۱ تا {stock} عدد انتخاب کنید:")
        return BUY_QTY

    price = p.get("price_toman") or 0
    total = int(float(price)) * qty

    order_id = record_order(
        user_id=update.effective_user.id,
        username=update.effective_user.username or update.effective_user.first_name,
        sku=p["sku"],
        product_name=p.get("product_name"),
        qty=qty,
        price_toman=total,
    )

    await update.message.reply_text(
        f"🎉 سفارش شما با موفقیت ثبت شد!\n\n"
        f"🧾 شماره پیگیری: #{order_id}\n"
        f"🛍 عطر: {p.get('product_name')}\n"
        f"🔢 تعداد: {qty} عدد\n"
        f"💵 مبلغ کل: {fmt_price(total)}\n\n"
        f"💳 شماره کارت جهت واریز:\n`{CARD_NUMBER}`\n"
        f"👤 {CARD_HOLDER}\n\n"
        "لطفاً فیش واریزی را برای پشتیبانی ارسال فرمایید.",
        reply_markup=main_menu(),
    )

    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"🔔 سفارش جدید ثبت شد!\n"
            f"شماره سفارش: #{order_id}\n"
            f"کاربر: @{update.effective_user.username} (آیدی: {update.effective_user.id})\n"
            f"عطر: {p.get('product_name')} ({p.get('sku')})\n"
            f"تعداد: {qty} عدد\n"
            f"مبلغ کل: {fmt_price(total)}"
        )
    except Exception as e:
        logging.warning(f"Could not alert admin: {e}")

    context.user_data.clear()
    return ConversationHandler.END

# ----------------------------- Admin Tools -----------------------------
async def getid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "📸 عکس عطر را همینجا بفرست تا file_id آن را برای اکسل دریافت کنی."
    )

async def catch_photo_for_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    msg = update.message
    if not msg:
        return

    fid = None
    if msg.photo:
        fid = msg.photo[-1].file_id
    elif msg.reply_to_message and msg.reply_to_message.photo:
        fid = msg.reply_to_message.photo[-1].file_id

    if fid:
        await msg.reply_text(
            f"✅ شناسه عکس (photo_file_id):\n\n`{fid}`\n\n"
            "روی متن بالا بزن کپی بشه، بذار تو ستون photo_file_id اکسل.",
            parse_mode="Markdown"
        )

async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    orders = fetch_orders(100)
    if not orders:
        await update.message.reply_text("هیچ سفارشی ثبت نشده است.")
        return
    df = pd.DataFrame(orders)
    path = os.path.join(os.path.dirname(__file__), "orders_report.xlsx")
    df.to_excel(path, index=False)
    with open(path, "rb") as f:
        await update.message.reply_document(f, filename="orders_report.xlsx", caption="گزارش سفارشات")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("عملیات لغو شد.", reply_markup=main_menu())
    return ConversationHandler.END

# ----------------------------- Main -----------------------------
def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    buy_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^/buy_"), buy_start)],
        states={
            BUY_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_qty)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("getid", getid_command))
    app.add_handler(CommandHandler("orders", admin_orders))
    app.add_handler(buy_conv)

    # هندل عکس برای file_id
    app.add_handler(MessageHandler(filters.PHOTO, catch_photo_for_id))

    # دکمه‌های متنی
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    return app

def main():
    db_setup()
    app = build_app()

    port = int(os.getenv("PORT", "10000"))
    if os.getenv("RENDER"):
        host = os.getenv("RENDER_EXTERNAL_HOST") or "my-telegram-bot-8k9h.onrender.com"
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="telegram",
            webhook_url=f"https://{host}/telegram",
        )
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
