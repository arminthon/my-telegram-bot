from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)
import datetime
import random
import sqlite3
import os

TOKEN = os.environ.get("TOKEN", "8738215541:AAHGTWFcyJDgN8UzP6h0nVHg_0PAr8lxuKc")

ADMIN_ID = 93543054  # آیدی تلگرام ادمین (خودت)

# حالت‌های ثبت‌نام
NAME, AGE = range(2)

# ساخت دیتابیس و جدول کاربران
def init_db():
    conn = sqlite3.connect("bot_users.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            age TEXT
        )
    """)
    conn.commit()
    conn.close()

# دستور شروع با دکمه‌های ثابت پایین صفحه
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📝 ثبت‌نام"), KeyboardButton("⏰ ساعت فعلی")],
        [KeyboardButton("🎲 عدد شانس")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    welcome_text = (
        "سلام! به ربات من خوش آمدید. 🤖\n\n"
        "✨ از دکمه‌های پایین صفحه می‌توانید استفاده کنید:"
    )
    
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)
async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # پاک کردن حافظه موقت کاربر
    context.user_data.clear()
    
    # اجرای دوباره تابع استارت برای شروع از اول
    await start(update, context)
    return ConversationHandler.END

# شروع فرآیند ثبت‌نام
async def start_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please enter your name:")
    return NAME

# دریافت نام و پرسیدن سن
async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.text
    context.user_data["name"] = user_name
    await update.message.reply_text(f"Nice to meet you {user_name}! How old are you?")
    return AGE

# دریافت سن، اعتبارسنجی و ذخیره در دیتابیس
async def get_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_age = update.message.text
    
    if not user_age.isdigit():
        await update.message.reply_text("❌ لطفاً فقط عدد وارد کنید (مثلاً 25):")
        return AGE

    user_name = context.user_data.get("name")
    
    conn = sqlite3.connect("bot_users.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (name, age) VALUES (?, ?)", (user_name, user_age))
    conn.commit()
    conn.close()
    
    await update.message.reply_text(
        f"Registration completed and saved to database!\nName: {user_name}\nAge: {user_age}"
    )
    return ConversationHandler.END

# نمایش لیست کاربران (فقط برای ادمین)
async def show_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ شما اجازه دسترسی به این دستور را ندارید!")
        return

    conn = sqlite3.connect("bot_users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, age FROM users")
    users = cursor.fetchall()
    conn.close()

    if not users:
        await update.message.reply_text("هنوز کاربری ثبت‌نام نکرده است.")
        return

    text = "Registered Users:\n"
    for name, age in users:
        text += f"Name: {name}, Age: {age}\n"

    await update.message.reply_text(text)

# هندلر مدیریت دکمه‌های متنی ثابت پایین صفحه و متن‌های متفرقه
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "📝 ثبت‌نام":
        # وقتی روی دکمه ثبت‌نام می‌زند، خودکار دستور /register رو براش اجرا می‌کنیم یا پیام میدیم
        await update.message.reply_text("برای شروع ثبت‌نام، لطفاً دستور /register را ارسال کنید.")
    elif text == "⏰ ساعت فعلی":
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await update.message.reply_text(f"Current Time: {current_time}")
    elif text == "🎲 عدد شانس":
        lucky_num = random.randint(1, 100)
        await update.message.reply_text(f"Your Lucky Number: {lucky_num}")
    else:
        await update.message.reply_text(text)

if __name__ == "__main__":
    init_db()
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("register", start_register)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_age)],
        },
        fallbacks=[],
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("users", show_users))
    app.add_handler(CommandHandler("restart", restart))

    # هندلر پیام‌های متنی برای مدیریت دکمه‌های ثابت پایین صفحه
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    print("Bot is running...")
    app.run_polling()
