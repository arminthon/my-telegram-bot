import os
import datetime
from zoneinfo import ZoneInfo
import sqlite3
import psycopg2
import pandas as pd
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    filters,
)

# لود کردن متغیرهای محیطی
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = 93543054  # آیدی عددی ادمین

# اصلاح آدرس اتصال به PostgreSQL در Render
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# استیت‌های مربوط به ثبت‌نام
NAME, AGE, CITY = range(3)

# کیبوردهای استاندارد
MAIN_KEYBOARD = [
    ["📝 ثبت‌نام", "⏰ ساعت فعلی"],
]
CANCEL_KEYBOARD = [
    ["❌ انصراف"]
]

MENU_BUTTONS = ["📝 ثبت‌نام", "⏰ ساعت فعلی", "❌ انصراف", "/start", "/cancel", "/export"]


# ------------------ توابع دیتابیس ------------------
def get_db_connection():
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect("bot_users.db")


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            name TEXT,
            age INTEGER,
            city TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()


def save_user(user_id, username, name, age, city):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if DATABASE_URL:
        query = """
            INSERT INTO users (user_id, username, name, age, city)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET username = EXCLUDED.username,
                          name = EXCLUDED.name,
                          age = EXCLUDED.age,
                          city = EXCLUDED.city;
        """
        cursor.execute(query, (user_id, username, name, age, city))
    else:
        query = """
            INSERT INTO users (user_id, username, name, age, city)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) 
            DO UPDATE SET username = excluded.username,
                          name = excluded.name,
                          age = excluded.age,
                          city = excluded.city;
        """
        cursor.execute(query, (user_id, username, name, age, city))

    conn.commit()
    cursor.close()
    conn.close()


# ------------------ دستورات عمومی ربات ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_markup = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    welcome_text = (
        "سلام! به ربات خوش آمدید.\n"
        "برای ثبت مشخصات روی «📝 ثبت‌نام» و برای دیدن ساعت روی «⏰ ساعت فعلی» بزنید."
    )
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)


async def show_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # تنظیم ساعت دقیق ایران
    tehran_tz = ZoneInfo("Asia/Tehran")
    now_tehran = datetime.datetime.now(tehran_tz)
    
    time_str = now_tehran.strftime("%H:%M:%S")
    date_str = now_tehran.strftime("%Y/%m/%d")
    
    reply_markup = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    msg = f"⏰ ساعت رسمی ایران: {time_str}\n📅 تاریخ: {date_str}"
    await update.message.reply_text(msg, reply_markup=reply_markup)


# ------------------ مکالمه ثبت‌نام (Conversation) ------------------
async def start_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_markup = ReplyKeyboardMarkup(CANCEL_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text(
        "لطفاً نام خود را وارد کنید:\n(برای انصراف دکمه «❌ انصراف» را بزنید)",
        reply_markup=reply_markup
    )
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    # اگر کاربر وسط ثبت‌نام دکمه ساعت یا لغو یا منو رو زد
    if text in MENU_BUTTONS:
        return await cancel_to_menu(update, context, text)
        
    context.user_data["name"] = text
    reply_markup = ReplyKeyboardMarkup(CANCEL_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text(
        f"از آشنایی با شما خوشحالم {text}! چند سال دارید؟ (لطفاً عدد وارد کنید)",
        reply_markup=reply_markup
    )
    return AGE


async def get_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if text in MENU_BUTTONS:
        return await cancel_to_menu(update, context, text)
        
    if not text.isdigit():
        await update.message.reply_text("لطفاً سن را فقط به صورت عدد وارد کنید:")
        return AGE

    context.user_data["age"] = int(text)
    reply_markup = ReplyKeyboardMarkup(CANCEL_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text("در کدام شهر سکونت دارید؟", reply_markup=reply_markup)
    return CITY


async def get_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if text in MENU_BUTTONS:
        return await cancel_to_menu(update, context, text)
        
    user = update.effective_user
    name = context.user_data.get("name")
    age = context.user_data.get("age")
    city = text

    # ذخیره در دیتابیس
    save_user(
        user_id=user.id,
        username=user.username if user.username else "بدون یوزرنیم",
        name=name,
        age=age,
        city=city
    )

    reply_markup = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    await update.message.reply_text(
        f"✅ اطلاعات شما با موفقیت ثبت شد!\n\n"
        f"👤 نام: {name}\n"
        f"🎂 سن: {age}\n"
        f"🏙 شهر: {city}",
        reply_markup=reply_markup
    )
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, triggered_button=None):
    context.user_data.clear()
    reply_markup = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
    
    if triggered_button == "⏰ ساعت فعلی":
        await update.message.reply_text("فرآیند ثبت‌نام لغو شد.")
        await show_time(update, context)
    else:
        await update.message.reply_text("ثبت‌نام لغو شد. به منوی اصلی بازگشتید.", reply_markup=reply_markup)
        
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await cancel_to_menu(update, context)


# ------------------ پنل خروجی اکسل برای ادمین ------------------
async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔️ شما اجازه دسترسی به این دستور را ندارید.")
        return

    conn = get_db_connection()
    query = "SELECT user_id, username, name, age, city, created_at FROM users;"
    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        await update.message.reply_text("هنوز هیچ کاربری در دیتابیس ثبت نشده است.")
        return

    file_name = "bot_users.xlsx"
    df.to_excel(file_name, index=False)

    with open(file_name, "rb") as f:
        await update.message.reply_document(document=f, caption="📊 لیست کاربران ثبت‌نام شده")

    if os.path.exists(file_name):
        os.remove(file_name)


# ------------------ تابع اصلی راه‌اندازی ------------------
def main():
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    # تعریف هندلر مکالمه ثبت‌نام
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📝 ثبت‌نام$"), start_register),
            CommandHandler("register", start_register)
        ],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_age)],
            CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_city)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^❌ انصراف$"), cancel),
            MessageHandler(filters.Regex("^⏰ ساعت فعلی$"), cancel),
        ],
    )

    # اتصال هندلرها به اپلیکیشن
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("export", export_excel))
    app.add_handler(MessageHandler(filters.Regex("^⏰ ساعت فعلی$"), show_time))
    app.add_handler(conv_handler)

    print("ربات با موفقیت روشن شد و آماده به کار است...")
    app.run_polling()


if __name__ == "__main__":
    main()
