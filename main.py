import os
import datetime
import sqlite3
import pandas as pd
from dotenv import load_dotenv

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

# ------------------ تنظیمات ------------------
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", "8443"))
ADMIN_ID = 93543054

# اصلاح آدرس اتصال به PostgreSQL در Render
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# اطلاعات فروشگاه
SHOP_NAME = "🌸 فروشگاه عطر آرمان 🌸"
CARD_NUMBER = "6037-9975-1234-5678"   # شماره کارت فروشنده
CARD_HOLDER = "بانک ملی - به نام آرمان"
PERFUMES = {
    "p1": ("عطر Dior Sauvage", 2500000),
    "p2": ("عطر Bleu de Chanel", 3200000),
    "p3": ("عطر YSL L'Homme", 2800000),
}

# ------------------ استیت‌های مکالمه‌ها ------------------
# ثبت‌نام مشتری
REG_NAME, REG_PHONE, REG_ADDRESS = range(3)
# ثبت سفارش
ORDER_ADDRESS, ORDER_PHONE, WAIT_RECEIPT = range(3)

MAIN_KEYBOARD = [["🛍 خرید عطر", "👤 ثبت‌نام"]]
CANCEL_KEYBOARD = [["❌ انصراف"]]

# ------------------ اتصال دیتابیس ------------------
def get_db_connection():
    if DATABASE_URL:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect("bot_users.db")

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    if DATABASE_URL:
        # ساخت جدول‌ها برای PostgreSQL
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                name TEXT,
                phone TEXT,
                address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # اصلاح ستون‌ها در صورت وجود جدول قدیمی از پروژه‌های قبل
        alter_cols = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS user_id BIGINT;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS name TEXT;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS address TEXT;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;"
        ]
        for q in alter_cols:
            try:
                cur.execute(q)
            except Exception:
                pass

        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id BIGINT PRIMARY KEY,
                user_id BIGINT,
                perfume_name TEXT,
                price BIGINT,
                address TEXT,
                phone TEXT,
                receipt_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    else:
        # ساخت جدول‌ها برای SQLite محلی
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                name TEXT,
                phone TEXT,
                address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY,
                user_id INTEGER,
                perfume_name TEXT,
                price INTEGER,
                address TEXT,
                phone TEXT,
                receipt_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    conn.commit()
    cur.close()
    conn.close()

def db_execute(query, params=(), fetch=False):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall() if fetch else None
    conn.commit()
    cur.close()
    conn.close()
    return rows

# ------------------ دستورات عمومی ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text(
        f"سلام! به {SHOP_NAME} خوش آمدید 🤖\n\n"
        "🛍 «خرید عطر»: مشاهده محصولات و ثبت سفارش\n"
        "👤 «ثبت‌نام»: ثبت اطلاعات تماس شما",
        reply_markup=kb,
    )

# ------------------ ثبت‌نام مشتری ------------------
async def start_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup(CANCEL_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text("لطفاً نام و نام خانوادگی خود را وارد کنید:", reply_markup=kb)
    return REG_NAME

async def get_reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    kb = ReplyKeyboardMarkup(CANCEL_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text("📱 شماره تماس خود را وارد کنید (مثلاً 09121234567):", reply_markup=kb)
    return REG_PHONE

async def get_reg_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not (text.isdigit() and len(text) == 11 and text.startswith("09")):
        await update.message.reply_text("❌ شماره تماس معتبر نیست. دوباره وارد کنید (مثلاً 09121234567):")
        return REG_PHONE
    context.user_data["phone"] = text
    kb = ReplyKeyboardMarkup(CANCEL_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text("🏠 آدرس پستی خود را وارد کنید:", reply_markup=kb)
    return REG_ADDRESS

async def get_reg_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_execute(
        """INSERT INTO users (user_id, username, name, phone, address)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (user_id) DO UPDATE
           SET name = EXCLUDED.name, phone = EXCLUDED.phone, address = EXCLUDED.address"""
        if DATABASE_URL else
        """INSERT INTO users (user_id, username, name, phone, address)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (user_id) DO UPDATE
           SET name = excluded.name, phone = excluded.phone, address = excluded.address""",
        (user.id, user.username or "-", context.user_data["name"],
         context.user_data["phone"], update.message.text.strip()),
    )
    kb = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text("✅ ثبت‌نام شما با موفقیت انجام شد! حالا می‌توانید خرید کنید 🛍", reply_markup=kb)
    context.user_data.clear()
    return ConversationHandler.END

# ------------------ خرید عطر ------------------
async def shop_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton(f"{name} - {price:,} تومان", callback_data=key)]
        for key, (name, price) in PERFUMES.items()
    ]
    await update.message.reply_text(
        "🛍 لیست عطرهای موجود:\n\nیکی را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

async def choose_perfume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data
    name, price = PERFUMES[key]
    context.user_data["perfume"] = (name, price)

    kb = ReplyKeyboardMarkup(CANCEL_KEYBOARD, resize_keyboard=True)
    await query.message.reply_text(
        f"✅ انتخاب شما: {name}\n💰 قیمت: {price:,} تومان\n\n"
        "🏠 لطفاً آدرس پستی خود را بفرستید:",
        reply_markup=kb,
    )
    return ORDER_ADDRESS

async def get_order_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "❌ انصراف":
        return await cancel(update, context)
    context.user_data["order_address"] = update.message.text.strip()
    kb = ReplyKeyboardMarkup(CANCEL_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text("📱 شماره تماس گیرنده را بفرستید:", reply_markup=kb)
    return ORDER_PHONE

async def get_order_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ انصراف":
        return await cancel(update, context)
    if not (text.isdigit() and len(text) == 11 and text.startswith("09")):
        await update.message.reply_text("❌ شماره معتبر نیست. دوباره بفرستید:")
        return ORDER_PHONE
    context.user_data["order_phone"] = text

    name, price = context.user_data["perfume"]
    kb = ReplyKeyboardRemove()
    await update.message.reply_text(
        f"🧾 فاکتور سفارش شما:\n\n"
        f"عطر: {name}\nمبلغ: {price:,} تومان\n\n"
        f"💳 جهت پرداخت، مبلغ را واریز کنید به:\n"
        f"شماره کارت: {CARD_NUMBER}\n{CARD_HOLDER}\n\n"
        "📸 سپس **عکس فیش واریزی** را همینجا بفرستید:",
        reply_markup=kb,
        parse_mode="Markdown",
    )
    return WAIT_RECEIPT

async def get_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo
    if not photo:
        await update.message.reply_text("❌ لطفاً فقط عکس فیش واریزی را بفرستید:")
        return WAIT_RECEIPT

    user = update.effective_user
    name, price = context.user_data["perfume"]
    file_id = photo[-1].file_id

    row = db_execute(
        "SELECT COALESCE(MAX(order_id), 1000) + 1 FROM orders",
        fetch=True,
    )[0][0]
    order_id = row

    db_execute(
        """INSERT INTO orders (order_id, user_id, perfume_name, price, address, phone, receipt_file_id, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')"""
        if DATABASE_URL else
        """INSERT INTO orders (order_id, user_id, perfume_name, price, address, phone, receipt_file_id, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
        (order_id, user.id, name, price,
         context.user_data["order_address"], context.user_data["order_phone"], file_id),
    )

    kb = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text(
        f"✅ سفارش شما ثبت شد!\n🔖 کد پیگیری: {order_id}\n\n"
        "پس از تایید فیش توسط فروشنده، خبر داده می‌شود. متشکریم 🌹",
        reply_markup=kb,
    )

    # ارسال به ادمین برای تایید
    admin_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید", callback_data=f"approve:{order_id}"),
         InlineKeyboardButton("❌ رد", callback_data=f"reject:{order_id}")],
    ])
    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=file_id,
        caption=(
            f"🛎 سفارش جدید!\nکد: {order_id}\n"
            f"کاربر: {user.first_name} (@{user.username}) - {user.id}\n"
            f"عطر: {name}\nمبلغ: {price:,} تومان"
        ),
        reply_markup=admin_kb,
    )
    context.user_data.clear()
    return ConversationHandler.END

# ------------------ پنل ادمین ------------------
async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, order_id = query.data.split(":")
    status = "approved" if action == "approve" else "rejected"

    db_execute("UPDATE orders SET status = %s WHERE order_id = %s" if DATABASE_URL
               else "UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id))

    user_row = db_execute("SELECT user_id FROM orders WHERE order_id = %s" if DATABASE_URL
                          else "SELECT user_id FROM orders WHERE order_id = ?", (order_id,), fetch=True)
    if user_row:
        msg = (f"✅ پرداخت سفارش {order_id} تایید شد! به‌زودی ارسال می‌شود 🌹"
               if status == "approved" else
               f"❌ فیش سفارش {order_id} تایید نشد. لطفاً با پشتیبانی تماس بگیرید.")
        try:
            await context.bot.send_message(chat_id=user_row[0][0], text=msg)
        except Exception:
            pass

    await query.edit_message_reply_markup(reply_markup=None)
    await query.edit_message_caption(
        caption=(query.message.caption or "") + f"\n\n➡️ وضعیت: {'تایید شد ✅' if status == 'approved' else 'رد شد ❌'}"
    )

async def export_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔️ دسترسی فقط برای ادمین.")
        return
    df = pd.read_sql_query(
        "SELECT * FROM orders ORDER BY created_at DESC",
        get_db_connection(),
    )
    if df.empty:
        await update.message.reply_text("هنوز سفارشی ثبت نشده.")
        return
    df.to_excel("orders.xlsx", index=False)
    with open("orders.xlsx", "rb") as f:
        await update.message.reply_document(document=f, caption="📦 لیست سفارش‌ها")
    os.remove("orders.xlsx")

# ------------------ انصراف ------------------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    kb = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text("عملیات لغو شد. به منوی اصلی بازگشتید.", reply_markup=kb)
    return ConversationHandler.END

# ------------------ راه‌اندازی ------------------
def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    register_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^👤 ثبت‌نام$"), start_register),
            CommandHandler("register", start_register),
        ],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_reg_name)],
            REG_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_reg_phone)],
            REG_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_reg_address)],
        },
        fallbacks=[CommandHandler("cancel", cancel),
                   MessageHandler(filters.Regex("^❌ انصراف$"), cancel)],
    )

    order_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(choose_perfume, pattern="^p[0-9]+$")],
        states={
            ORDER_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_order_address)],
            ORDER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_order_phone)],
            WAIT_RECEIPT: [MessageHandler(filters.PHOTO, get_receipt)],
        },
        fallbacks=[CommandHandler("cancel", cancel),
                   MessageHandler(filters.Regex("^❌ انصراف$"), cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("orders", export_orders))
    app.add_handler(MessageHandler(filters.Regex("^🛍 خرید عطر$"), shop_menu))
    app.add_handler(register_conv)
    app.add_handler(order_conv)
    app.add_handler(CallbackQueryHandler(admin_decision, pattern="^(approve|reject):"))

    # انتخاب هوشمند بین وب‌هوک (سرور Render) یا پولینگ (سیستم لوکال)
    if WEBHOOK_URL:
        print("Starting webhook on Render...")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
        )
    else:
        print("Starting polling on local machine...")
        app.run_polling()

if __name__ == "__main__":
    main()
