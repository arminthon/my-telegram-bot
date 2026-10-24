# main.py — فروشگاه عطر آرمین 🌸
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

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "93543054"))
DATABASE_URL = os.getenv("DATABASE_URL")
EXCEL_FILE = os.path.join(os.path.dirname(__file__), "products.xlsx")

TEHRAN = pytz.timezone("Asia/Tehran")
CARD_NUMBER = "6037-9975-1234-5678"
CARD_HOLDER = "بانک ملی - به نام آرمین"
SHOP_NAME = "🌸 فروشگاه عطر آرمین 🌸"

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ----------------- دیتابیس -----------------
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
        cur.execute("DROP TABLE IF EXISTS orders CASCADE;")
        cur.execute("""
            CREATE TABLE orders (
                order_id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT,
                sku TEXT,
                product_name TEXT,
                qty INT DEFAULT 1,
                price_toman BIGINT DEFAULT 0,
                status TEXT DEFAULT 'در انتظار پرداخت',
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)
    else:
        cur.execute("DROP TABLE IF EXISTS orders;")
        cur.execute("""
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                sku TEXT,
                product_name TEXT,
                qty INTEGER DEFAULT 1,
                price_toman INTEGER DEFAULT 0,
                status TEXT DEFAULT 'در انتظار پرداخت',
                created_at TEXT
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
            (user_id, str(username or ""), str(sku), str(product_name or ""), qty, safe_price)
        )
        order_id = cur.fetchone()[0]
    else:
        cur.execute(
            """INSERT INTO orders (user_id, username, sku, product_name, qty, price_toman, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?);""",
            (user_id, str(username or ""), str(sku), str(product_name or ""), qty, safe_price, datetime.now(TEHRAN).isoformat())
        )
        order_id = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    return order_id

# ----------------- اکسل محصولات -----------------
def get_products():
    if not os.path.exists(EXCEL_FILE):
        return []
    df = pd.read_excel(EXCEL_FILE)
    df.columns = [str(c).strip().lower() for c in df.columns]
    for c in ["sku", "product_name", "brand", "volume_ml", "price_toman", "stock_qty", "active", "tags", "description", "photo_file_id"]:
        if c not in df.columns:
            df[c] = None
    df = df[df["active"].astype(str).str.lower().isin(["true", "1", "yes", "فعال", "بله"])]
    df = df.dropna(subset=["sku", "product_name"])
    return df.to_dict("records")

def get_product(sku):
    for p in get_products():
        if str(p.get("sku")).strip() == str(sku).strip():
            return p
    return None

def fmt_price(v):
    try:
        return f"{int(float(v)):,} تومان"
    except:
        return "نامشخص"

# ----------------- کیبورد و منو -----------------
def main_kb():
    return ReplyKeyboardMarkup(
        [["🛍 مشاهده محصولات", "🧾 پیگیری سفارش"], ["💳 اطلاعات پرداخت", "📞 پشتیبانی"]],
        resize_keyboard=True
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"سلام خوش آمدید به {SHOP_NAME} 🌸", reply_markup=main_kb())

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    
    if txt == "🛍 مشاهده محصولات":
        prods = get_products()
        if not prods:
            await update.message.reply_text("هیچ عطری در سیستم ثبت نشده است.")
            return
        for p in prods:
            caption = (
                f"🌸 {p.get('product_name')}\n"
                f"🏷 برند: {p.get('brand') or '—'}\n"
                f"💧 حجم: {p.get('volume_ml') or '—'} میل\n"
                f"💰 قیمت: {fmt_price(p.get('price_toman'))}\n"
                f"📦 موجودی: {p.get('stock_qty') or 0} عدد\n"
                f"📝 توضیحات: {p.get('description') or '—'}\n\n"
                f"خرید این عطر: /buy_{p.get('sku')}"
            )
            photo = str(p.get("photo_file_id") or "").strip()
            if photo and photo.lower() not in ["none", "nan"]:
                try:
                    await update.message.reply_photo(photo, caption=caption)
                    continue
                except:
                    pass
            await update.message.reply_text(caption)

    elif txt == "🧾 پیگیری سفارش":
        conn, kind = get_conn()
        cur = conn.cursor()
        query = (
            "SELECT order_id, product_name, qty, price_toman, status FROM orders WHERE user_id = %s ORDER BY order_id DESC LIMIT 5;"
            if kind == "pg" else
            "SELECT order_id, product_name, qty, price_toman, status FROM orders WHERE user_id = ? ORDER BY order_id DESC LIMIT 5;"
        )
        cur.execute(query, (update.effective_user.id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            await update.message.reply_text("شما هنوز سفارشی ثبت نکرده‌اید.")
            return

        msg = "📋 سفارش‌های اخیر شما:\n\n"
        for r in rows:
            msg += f"شماره سفارش #{r[0]}: {r[1]} ({r[2]} عدد) - {fmt_price(r[3])} | وضعیت: {r[4]}\n"
        await update.message.reply_text(msg)

    elif txt == "💳 اطلاعات پرداخت":
        await update.message.reply_text(f"شماره کارت:\n`{CARD_NUMBER}`\n{CARD_HOLDER}")

    elif txt == "📞 پشتیبانی":
        await update.message.reply_text("جهت پشتیبانی با ادمین در ارتباط باشید: @Armin")

# ----------------- جریان خرید (Conversation) -----------------
BUY_QTY = 1

async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sku = update.message.text.replace("/buy_", "").strip()
    p = get_product(sku)
    if not p:
        await update.message.reply_text("این محصول یافت نشد.")
        return ConversationHandler.END
    context.user_data["p"] = p
    await update.message.reply_text(f"چند عدد «{p.get('product_name')}» می‌خواهید؟ (فقط عدد ارسال کنید):")
    return BUY_QTY

async def buy_qty_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p = context.user_data.get("p")
    try:
        qty = int(update.message.text.strip())
    except:
        await update.message.reply_text("لطفاً یک عدد معتبر ارسال کنید:")
        return BUY_QTY

    total = int(float(p.get("price_toman") or 0)) * qty
    oid = record_order(
        update.effective_user.id,
        update.effective_user.username,
        p["sku"],
        p.get("product_name"),
        qty,
        total
    )
    
    await update.message.reply_text(
        f"✅ سفارش #{oid} ثبت شد!\n"
        f"مبلغ کل: {fmt_price(total)}\n\n"
        f"کارت: `{CARD_NUMBER}` ({CARD_HOLDER})\n"
        "فیش واریزی را برای پشتیبانی ارسال فرمایید.",
        reply_markup=main_kb()
    )
    context.user_data.clear()
    return ConversationHandler.END

# ----------------- ابزار ادمین برای دریافت عکس -----------------
async def get_photo_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    fid = update.message.photo[-1].file_id
    await update.message.reply_text(f"شناسه عکس شما:\n\n`{fid}`", parse_mode="Markdown")

# ----------------- اجرای ربات -----------------
def main():
    db_setup()
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^/buy_"), buy_start)],
        states={BUY_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_qty_step)]},
        fallbacks=[]
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.PHOTO, get_photo_id))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    port = int(os.getenv("PORT", "10000"))
    if os.getenv("RENDER"):
        host = os.getenv("RENDER_EXTERNAL_HOST") or "my-telegram-bot-8k9h.onrender.com"
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="telegram",
            webhook_url=f"https://{host}/telegram"
        )
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
