import os
import datetime
import random
import pandas as pd
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    filters,
)

# بارگذاری متغیرهای محیطی
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# اصلاح آدرس اتصال PostgreSQL برای Render
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

PORT = int(os.environ.get("PORT", "8443"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
ADMIN_ID = 93543054  # آیدی تلگرام ادمین

# حالت‌های مختلف مکالمهٔ ثبت‌نام
NAME, AGE = range(2)


# اتصال به دیتابیس
def get_db_connection():
    if DATABASE_URL:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)

    import sqlite3
    return sqlite3.connect("bot_users.db")


# ساخت جدول در صورت نبودن
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    if DATABASE_URL:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name TEXT,
                age TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                age TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    conn.commit()
    cursor.close()
    conn.close()


# منوی اصلی
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📝 ثبت‌نام"), KeyboardButton("⏰ ساعت فعلی")],
        [KeyboardButton("🎲 عدد شانس")],
    ]

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    welcome_text = (
        "سلام! به ربات من خوش آمدید.\n\n"
        "از دکمه‌های منو برای ثبت‌نام، دیدن ساعت ایران یا گرفتن عدد شانس استفاده کنید."
    )

    await update.message.reply_text(welcome_text, reply_markup=reply_markup)


async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await start(update, context)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ عملیات لغو شد.")
    await start(update, context)
    return ConversationHandler.END


# فرآیند ثبت‌نام
async def start_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لطفاً نام خود را وارد کنید:")
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.text.strip()
    context.user_data["name"] = user_name

    await update.message.reply_text(
        f"از آشنایی با شما خوشحالم {user_name}! چند سال دارید؟"
    )
    return AGE


async def get_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_age = update.message.text.strip()

    if not user_age.isdigit():
        await update.message.reply_text(
            "❌ لطفاً فقط عدد وارد کنید (مثلاً 25):"
        )
        return AGE

    user_name = context.user_data.get("name")

    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"

    cursor.execute(
        f"INSERT INTO users (name, age) VALUES ({placeholder}, {placeholder})",
        (user_name, user_age),
    )

    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(
        "✅ ثبت‌نام با موفقیت ذخیره شد!\n"
        f"نام: {user_name}\n"
        f"سن: {user_age}"
    )

    context.user_data.pop("name", None)
    return ConversationHandler.END


# خروجی گرفتن از فهرست کاربران؛ فقط برای ادمین
async def export_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != ADMIN_ID:
        return

    try:
        conn = get_db_connection()
        try:
            df = pd.read_sql_query(
                "SELECT id, name, age, created_at FROM users",
                conn,
            )
        finally:
            conn.close()

        if df.empty:
            await update.message.reply_text("دیتابیس خالی است!")
            return

        file_path = "bot_users.xlsx"
        df.to_excel(file_path, index=False)

        with open(file_path, "rb") as file:
            await context.bot.send_document(
                chat_id=user_id,
                document=file,
                filename="bot_users.xlsx",
            )

    except Exception as e:
        print(f"Export error: {e}")
        await update.message.reply_text(f"خطا در خروجی گرفتن: {e}")


# نمایش کاربران؛ فقط برای ادمین
async def show_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT name, age FROM users")
    users = cursor.fetchall()

    cursor.close()
    conn.close()

    if not users:
        await update.message.reply_text("کاربری ثبت نشده است.")
        return

    text = "کاربران:\n"
    for name, age in users:
        text += f"نام: {name} | سن: {age}\n"

    await update.message.reply_text(text)


# مدیریت پیام‌های منو
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "⏰ ساعت فعلی":
        iran_time = datetime.datetime.now(ZoneInfo("Asia/Tehran"))
        await update.message.reply_text(
            "⏰ ساعت رسمی ایران:\n"
            f"{iran_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    elif text == "🎲 عدد شانس":
        await update.message.reply_text(
            f"عدد شانس: {random.randint(1, 100)}"
        )

    elif text == "📝 ثبت‌نام":
        await start_register(update, context)

    else:
        await update.message.reply_text("متوجه نشدم! لطفاً از منو استفاده کنید.")


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("متغیر BOT_TOKEN تنظیم نشده است.")

    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    reg_handler = ConversationHandler(
        entry_points=[
            CommandHandler("register", start_register),
            MessageHandler(
                filters.Regex("^📝 ثبت‌نام$"),
                start_register,
            ),
        ],
        states={
            NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)
            ],
            AGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_age)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("restart", restart),
        ],
    )

    app.add_handler(reg_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("users", show_users))
    app.add_handler(CommandHandler("export", export_users))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    if WEBHOOK_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
        )
    else:
        app.run_polling()
