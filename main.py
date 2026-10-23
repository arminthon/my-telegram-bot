import os
import datetime
import random
import pandas as pd
from dotenv import load_dotenv
from groq import Groq

# لود کردن متغیرهای محیطی
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# اصلاح پروتکل برای اتصال مطمئن به پستگرس در Render
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ساخت کلاینت هوش مصنوعی Groq
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    filters,
)

PORT = int(os.environ.get("PORT", "8443"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "") 
ADMIN_ID = 93543054  # آیدی تلگرام ادمین

# حالت‌های مختلف مکالمه
NAME, AGE = range(2)
ASK_AI = 2

# اتصال چندمنظوره به دیتابیس (پستگرس در رندر / اس‌کیولایت لوکال)
def get_db_connection():
    if DATABASE_URL:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    else:
        import sqlite3
        return sqlite3.connect("bot_users.db")

# ساخت جدول در صورت عدم وجود
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    if DATABASE_URL:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name TEXT,
                age TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                age TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.commit()
    cursor.close()
    conn.close()

# منوی اصلی
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("🤖 پرسش از هوش مصنوعی")],
        [KeyboardButton("📝 ثبت‌نام"), KeyboardButton("⏰ ساعت فعلی")],
        [KeyboardButton("🎲 عدد شانس")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    welcome_text = (
        "سلام! به ربات من خوش آمدید. 🤖✨\n\n"
        "برای صحبت پیوسته با هوش مصنوعی روی دکمه «🤖 پرسش از هوش مصنوعی» بزنید."
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

# --- فرآیند ثبت‌نام ---
async def start_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لطفاً نام خود را وارد کنید:")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.message.text
    context.user_data["name"] = user_name
    await update.message.reply_text(f"از آشنایی با شما خوشحالم {user_name}! چند سال دارید؟")
    return AGE

async def get_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_age = update.message.text
    
    if not user_age.isdigit():
        await update.message.reply_text("❌ لطفاً فقط عدد وارد کنید (مثلاً 25):")
        return AGE

    user_name = context.user_data.get("name")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    placeholder = "%s" if DATABASE_URL else "?"
    cursor.execute(f"INSERT INTO users (name, age) VALUES ({placeholder}, {placeholder})", (user_name, user_age))
    conn.commit()
    cursor.close()
    conn.close()
    
    await update.message.reply_text(
        f"✅ ثبت‌نام با موفقیت ذخیره شد!\nنام: {user_name}\nسن: {user_age}"
    )
    return ConversationHandler.END

# --- فرآیند هوش مصنوعی با حافظه و مکالمه پیوسته ---
async def start_ai_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # مقداردهی اولیه حافظه گفتگو برای سشن جدید
    context.user_data["ai_history"] = [
        {"role": "system", "content": "You are a friendly, helpful, and smart AI assistant. Answer in the same language the user uses."}
    ]
    
    exit_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("❌ پایان گفتگو با هوش مصنوعی")]],
        resize_keyboard=True
    )
    
    await update.message.reply_text(
        "🧠 وارد بخش گفتگوی هوش مصنوعی شدید!\n\n"
        "هر سوالی دارید بنویسید؛ گفتگو پیوسته است و ربات پیام‌های قبلی شما را به یاد دارد.\n"
        "هر زمان کارتان تمام شد، دکمه «❌ پایان گفتگو با هوش مصنوعی» را بزنید.",
        reply_markup=exit_keyboard
    )
    return ASK_AI

async def handle_ai_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_question = update.message.text
    
    # خروج از حالت گفتگو
    if user_question == "❌ پایان گفتگو با هوش مصنوعی":
        context.user_data.pop("ai_history", None)
        await update.message.reply_text("👋 گفتگو با هوش مصنوعی پایان یافت.")
        await start(update, context)
        return ConversationHandler.END

    if not groq_client:
        await update.message.reply_text("⚠️ سرویس هوش مصنوعی در دسترس نیست.")
        await start(update, context)
        return ConversationHandler.END

    status_msg = await update.message.reply_text("⏳ در حال پردازش...")

    # تاریخچه مکالمه
    history = context.user_data.get("ai_history", [
        {"role": "system", "content": "You are a friendly, helpful, and smart AI assistant. Answer in the same language the user uses."}
    ])
    history.append({"role": "user", "content": user_question})

    try:
        completion = groq_client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=history,
            temperature=0.7,
            max_tokens=1024,
        )
        ai_reply = completion.choices[0].message.content
        
        # ذخیره پاسخ بات در تاریخچه
        history.append({"role": "assistant", "content": ai_reply})
        
        # نگه داشتن نهایتاً ۱۰ پیام اخیر برای بهینه‌ماندن مصرف توکن
        if len(history) > 11:
            history = [history[0]] + history[-10:]
        context.user_data["ai_history"] = history

        await status_msg.edit_text(ai_reply)

    except Exception as e:
        await status_msg.edit_text(f"❌ خطا در پردازش هوش مصنوعی: {e}")

    # برگرداندن ASK_AI برای ادامه داشتن چت
    return ASK_AI

# --- گزارش‌گیری ادمین ---
async def export_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("شما دسترسی ندارید.")
        return

    try:
        conn = get_db_connection()
        df = pd.read_sql_query("SELECT id, name, age, created_at FROM users", conn)
        conn.close()

        if df.empty:
            await update.message.reply_text("دیتابیس کاربران خالی است!")
            return

        file_path = "bot_users.xlsx"
        df.to_excel(file_path, index=False)

        with open(file_path, "rb") as excel_file:
            await context.bot.send_document(
                chat_id=user_id,
                document=excel_file,
                filename="bot_users.xlsx",
                caption=f"📊 خروجی اکسل ({len(df)} کاربر ذخیره‌شده):"
            )

    except Exception as e:
        await update.message.reply_text(f"خطا در ایجاد فایل اکسل: {e}")

async def show_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ شما اجازه دسترسی به این دستور را ندارید!")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, age FROM users")
    users = cursor.fetchall()
    cursor.close()
    conn.close()

    if not users:
        await update.message.reply_text("هنوز کاربری ثبت‌نام نکرده است.")
        return

    text = "کاربران ثبت‌نام‌شده:\n"
    for name, age in users:
        text += f"نام: {name} | سن: {age}\n"

    await update.message.reply_text(text)

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "⏰ ساعت فعلی":
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await update.message.reply_text(f"ساعت فعلی: {current_time}")
    elif text == "🎲 عدد شانس":
        lucky_num = random.randint(1, 100)
        await update.message.reply_text(f"عدد شانس شما: {lucky_num}")
    else:
        await update.message.reply_text(text)

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")

    app = ApplicationBuilder().token(TOKEN).build()
    
    # هندلر ثبت‌نام
    reg_handler = ConversationHandler(
        entry_points=[
            CommandHandler("register", start_register),
            MessageHandler(filters.Regex("^📝 ثبت‌نام$"), start_register),
        ],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_age)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("restart", restart)],
    )

    # هندلر هوش مصنوعی
    ai_handler = ConversationHandler(
        entry_points=[
            CommandHandler("ai", start_ai_prompt),
            MessageHandler(filters.Regex("^🤖 پرسش از هوش مصنوعی$"), start_ai_prompt),
        ],
        states={
            ASK_AI: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_response)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("restart", restart)],
    )

    app.add_handler(reg_handler)
    app.add_handler(ai_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("users", show_users))
    app.add_handler(CommandHandler("export", export_users))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    if WEBHOOK_URL:
        print(f"Starting webhook on port {PORT}...")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
        )
    else:
        print("Starting polling...")
        app.run_polling()
