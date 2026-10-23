import os
import datetime
import random
import asyncio
import pandas as pd
from dotenv import load_dotenv
from groq import Groq
from duckduckgo_search import DDGS

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
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
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# اصلاح پروتکل اتصال به PostgreSQL در Render
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ساخت کلاینت هوش مصنوعی Groq
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# استفاده از مدل دقیق‌تر و بدون توهم برای فاکت‌ها
GROQ_MODEL = (os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip() or "llama-3.3-70b-versatile")

PORT = int(os.environ.get("PORT", "8443"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
ADMIN_ID = 93543054  # آیدی تلگرام ادمین

# حالت‌های مختلف مکالمه ثبت‌نام
NAME, AGE = range(2)


# تکه‌تکه کردن متن‌های طولانی برای عبور از سقف کاراکتر تلگرام
def chunk_text(text: str, chunk_size: int = 3500):
    if not text:
        return []
    return [text[i: i + chunk_size] for i in range(0, len(text), chunk_size)]


def _ddg_text_search(query: str, max_results: int = 6) -> list[dict]:
    with DDGS() as ddgs:
        return list(
            ddgs.text(
                query,
                max_results=max_results,
                region="wt-wt",
                backend="html",
            )
        )


# جستجو در وب و آماده‌کردن خلاصه نتایج
def web_search(query: str, max_results: int = 6) -> str:
    try:
        raw_results = _ddg_text_search(query, max_results=max_results)

        if not raw_results:
            raw_results = _ddg_text_search(f"{query} official source", max_results=max_results)

        if not raw_results:
            return ""

        results = []
        for result in raw_results:
            title = (result.get("title") or "").strip()
            body = (result.get("body") or result.get("snippet") or result.get("description") or "").strip()
            link = (result.get("href") or result.get("url") or "").strip()

            parts = []
            if title:
                parts.append(f"عنوان: {title}")
            if body:
                parts.append(f"خلاصه: {body}")
            if link:
                parts.append(f"لینک: {link}")

            if parts:
                results.append("\n".join(parts))

        return "\n\n---\n\n".join(results).strip()

    except Exception as e:
        print(f"Web search error: {e}")
        return ""


# اتصال به دیتابیس:
def get_db_connection():
    if DATABASE_URL:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)

    import sqlite3
    return sqlite3.connect("bot_users.db")


# ساخت جدول در صورت عدم وجود
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
    context.user_data["in_ai_mode"] = False

    keyboard = [
        [KeyboardButton("🤖 پرسش از هوش مصنوعی")],
        [KeyboardButton("📝 ثبت‌نام"), KeyboardButton("⏰ ساعت فعلی")],
        [KeyboardButton("🎲 عدد شانس")],
    ]

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    welcome_text = (
        "سلام! به ربات من خوش آمدید.\n\n"
        "برای صحبت پیوسته با هوش مصنوعی روی دکمه "
        "«🤖 پرسش از هوش مصنوعی» بزنید."
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
    context.user_data["in_ai_mode"] = False
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

    cursor.execute(
        f"INSERT INTO users (name, age) VALUES ({placeholder}, {placeholder})",
        (user_name, user_age),
    )

    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(
        f"✅ ثبت‌نام با موفقیت ذخیره شد!\n"
        f"نام: {user_name}\n"
        f"سن: {user_age}"
    )
    return ConversationHandler.END


# --- پرامپت سیستم هوش مصنوعی ---
AI_SYSTEM_PROMPT = (
    "شما یک دستیار هوشمند، بسیار دقیق و مسلط به زبان فارسی هستید.\n"
    "قوانین مهم:\n"
    "1) فقط پاسخ متنی تولید کن.\n"
    "2) هیچ ابزار، تابع یا اکشنی را فراخوانی نکن.\n"
    "3) اطلاعات مربوط به جوایز سینمایی (مثل اسکار ۲۰۲۴ که فیلم اوپنهایمر Oppenheimer برنده بهترین فیلم شد) یا وقایع تاریخی را با دقت کامل و صحیح پاسخ بده.\n"
    "4) پاسخ را به همان زبانی بده که کاربر استفاده کرده است.\n"
)


async def start_ai_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["in_ai_mode"] = True
    context.user_data["ai_history"] = [{"role": "system", "content": AI_SYSTEM_PROMPT}]

    exit_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("❌ پایان گفتگو با هوش مصنوعی")]],
        resize_keyboard=True,
    )

    await update.message.reply_text(
        "🧠 وارد بخش گفتگوی هوش مصنوعی شدید!\n\n"
        "سؤالت را بنویس.\n\n"
        "برای خروج، دکمه «❌ پایان گفتگو با هوش مصنوعی» را بزن.",
        reply_markup=exit_keyboard,
    )


async def ask_groq_ai(update: Update, context: ContextTypes.DEFAULT_TYPE, user_question: str):
    if not groq_client:
        await update.message.reply_text("⚠️ سرویس هوش مصنوعی در دسترس نیست.")
        return

    status_msg = await update.message.reply_text("🔍 در حال پردازش پرسش...")

    try:
        search_info = await asyncio.to_thread(web_search, user_question, 6)

        if search_info:
            model_input = f"نتایج جستجو از وب:\n{search_info}\n\nپرسش کاربر:\n{user_question}"
        else:
            model_input = f"پرسش کاربر:\n{user_question}"

        history = list(
            context.user_data.get(
                "ai_history",
                [{"role": "system", "content": AI_SYSTEM_PROMPT}],
            )
        )
        history.append({"role": "user", "content": model_input})

        completion = await asyncio.to_thread(
            lambda: groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=history,
                temperature=0.1,
                max_tokens=4096,
            )
        )

        ai_reply = completion.choices[0].message.content or "پاسخی دریافت نشد."
        history.append({"role": "assistant", "content": ai_reply})

        if len(history) > 11:
            history = [history[0]] + history[-10:]

        context.user_data["ai_history"] = history

        parts = chunk_text(ai_reply, 3500)

        if parts:
            await status_msg.edit_text(parts[0])
            for part in parts[1:]:
                await update.message.reply_text(part)
        else:
            await status_msg.edit_text("پاسخی داده نشد.")

    except Exception as e:
        print(f"Groq error: {e}")
        await status_msg.edit_text(f"❌ خطا در هوش مصنوعی: {e}")


async def export_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    try:
        conn = get_db_connection()
        df = pd.read_sql_query("SELECT id, name, age, created_at FROM users", conn)
        conn.close()
        if df.empty:
            await update.message.reply_text("دیتابیس خالی است!")
            return
        file_path = "bot_users.xlsx"
        df.to_excel(file_path, index=False)
        with open(file_path, "rb") as f:
            await context.bot.send_document(chat_id=user_id, document=f, filename="bot_users.xlsx")
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")


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
        await update.message.reply_text("کاربری نیست.")
        return
    text = "کاربران:\n"
    for n, a in users:
        text += f"نام: {n} | سن: {a}\n"
    await update.message.reply_text(text)


async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "❌ پایان گفتگو با هوش مصنوعی":
        context.user_data["in_ai_mode"] = False
        context.user_data.pop("ai_history", None)
        await update.message.reply_text("👋 گفتگو پایان یافت.")
        await start(update, context)
        return

    if context.user_data.get("in_ai_mode", False):
        await ask_groq_ai(update, context, text)
        return

    if text == "⏰ ساعت فعلی":
        await update.message.reply_text(f"ساعت فعلی: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    elif text == "🎲 عدد شانس":
        await update.message.reply_text(f"عدد شانس: {random.randint(1, 100)}")
    elif text == "🤖 پرسش از هوش مصنوعی":
        await start_ai_prompt(update, context)
    else:
        await update.message.reply_text("متوجه نشدم! از منو استفاده کنید.")


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("متغیر BOT_TOKEN تنظیم نشده است.")

    print("Using Groq model:", GROQ_MODEL)
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

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

    app.add_handler(reg_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ai", start_ai_prompt))
    app.add_handler(CommandHandler("users", show_users))
    app.add_handler(CommandHandler("export", export_users))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    if WEBHOOK_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"{WEBHOOK_URL}/{TOKEN}")
    else:
        app.run_polling()
