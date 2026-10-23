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

# نام مدل را می‌توان در Environment Variables رندر هم تنظیم کرد.
GROQ_MODEL = (os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip() or "openai/gpt-oss-120b")

PORT = int(os.environ.get("PORT", "8443"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
ADMIN_ID = 93543054  # آیدی تلگرام ادمین

# حالت‌های مختلف مکالمه ثبت‌نام
NAME, AGE = range(2)


# تکه‌تکه کردن متن‌های طولانی برای عبور از سقف کاراکتر تلگرام
def chunk_text(text: str, chunk_size: int = 3500):
    if not text:
        return []
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


# جستجو در وب و آماده‌کردن خلاصه نتایج
def web_search(query: str, max_results: int = 6) -> str:
    """
    جستجوی وب با خروجی پایدارتر برای فارسی.
    نکته: DDGS فقط snippet برمی‌گرداند و صفحه را کامل fetch نمی‌کند.
    """
    try:
        with DDGS() as ddgs:
            raw_results = list(
                ddgs.text(
                    query,
                    max_results=max_results,
                    region="wt-wt",      # نتایج جهانی
                    backend="html",      # معمولاً برای خروجی پایدارتر
                )
            )

        if not raw_results:
            return ""

        results = []
        for result in raw_results:
            title = (result.get("title") or "").strip()
            body = (
                result.get("body")
                or result.get("snippet")
                or result.get("description")
                or ""
            ).strip()
            link = (result.get("href") or result.get("url") or "").strip()

            # حتی اگر body خالی بود، title/link را نگه می‌داریم
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
# PostgreSQL در Render و SQLite در اجرای محلی
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
    "شما یک دستیار هوشمند هستید.\n"
    "قوانین مهم:\n"
    "1) فقط پاسخ متنی تولید کن.\n"
    "2) هیچ ابزار، تابع یا اکشنی را فراخوانی نکن.\n"
    "3) اگر در پیام کاربر بخشی با عنوان «نتایج جستجو از وب» وجود داشت، "
    "اولویت با همان بخش است.\n"
    "4) اگر نتایج وب شامل لینک بودند، در صورت مرتبط بودن، لینک یا عنوان منبع را در پاسخ بیاور.\n"
    "5) اگر سؤال درباره اطلاعات لحظه‌ای/جدید (مثل قیمت امروز، نرخ ارز امروز، خبر فوری) بود و "
    "نتایج وب شواهد کافی نداشتند، صادقانه بگو نمی‌توانی آن را قطعی تأیید کنی و عدد/خبر نساز.\n"
    "6) اگر سؤال دانش عمومی بود (مثل اسکار، تاریخ، مفاهیم علمی) و نتایج وب ناکافی بودند، "
    "می‌توانی از دانش عمومی خودت پاسخ بده؛ اما اگر مطمئن نیستی، میزان اطمینان را بیان کن.\n"
    "7) پاسخ را به همان زبانی بده که کاربر استفاده کرده است.\n"
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
        "قبل از پاسخ، یک جستجوی کوتاه در وب انجام می‌دهم.\n"
        "اگر جستجو برای جواب قطعی کافی نبود، شفاف می‌گویم.\n\n"
        "سؤالت را بنویس.\n\n"
        "برای خروج، دکمه «❌ پایان گفتگو با هوش مصنوعی» را بزن.",
        reply_markup=exit_keyboard,
    )


async def ask_groq_ai(update: Update, context: ContextTypes.DEFAULT_TYPE, user_question: str):
    if not groq_client:
        await update.message.reply_text(
            "⚠️ سرویس هوش مصنوعی در دسترس نیست؛ "
            "متغیر GROQ_API_KEY تنظیم نشده است."
        )
        return

    status_msg = await update.message.reply_text("🔍 در حال جستجو در اینترنت و بررسی پاسخ...")

    try:
        # جستجو در وب خارج از مدل هوش مصنوعی انجام می‌شود.
        search_info = await asyncio.to_thread(web_search, user_question, 6)

        if search_info:
            model_input = (
                "نتایج جستجو از وب:\n"
                f"{search_info}\n\n"
                "پرسش کاربر:\n"
                f"{user_question}\n\n"
                "با اولویت نتایج وب پاسخ بده. اگر نتایج برای پاسخ قطعی کافی نیستند، "
                "شفاف توضیح بده چه چیزی قطعی نیست."
            )
        else:
            model_input = (
                "جستجوی وب انجام شد، اما نتیجه قابل استفاده‌ای برنگشت.\n"
                "اگر پرسش به اطلاعات لحظه‌ای یا جدید نیاز دارد، این محدودیت را صریح بگو و حدس نزن.\n"
                "اگر پرسش دانش عمومی است، می‌توانی از دانش عمومی خودت کمک بگیری.\n\n"
                f"پرسش کاربر:\n{user_question}"
            )

        history = list(
            context.user_data.get(
                "ai_history",
                [{"role": "system", "content": AI_SYSTEM_PROMPT}],
            )
        )
        history.append({"role": "user", "content": model_input})

        # درخواست همگام Groq در thread اجرا می‌شود تا پاسخ‌گویی بات متوقف نشود.
        completion = await asyncio.to_thread(
            lambda: groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=history,
                temperature=0.6,
                max_tokens=4096,
            )
        )

        ai_reply = completion.choices[0].message.content or "پاسخی دریافت نشد."

        history.append({"role": "assistant", "content": ai_reply})

        # محدود نگه‌داشتن طول تاریخچه مکالمه
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
        print(f"Groq or AI response error: {e}")
        await status_msg.edit_text(f"❌ خطا در هوش مصنوعی: {e}")


# --- گزارش‌گیری ادمین ---
async def export_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != ADMIN_ID:
        await update.message.reply_text("شما دسترسی ندارید.")
        return

    try:
        conn = get_db_connection()

        df = pd.read_sql_query(
            "SELECT id, name, age, created_at FROM users",
            conn,
        )

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
                caption=f"📊 خروجی اکسل ({len(df)} کاربر ذخیره‌شده):",
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


# --- مدیریت کلیه پیام‌های متنی ---
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # بررسی خروج از هوش مصنوعی
    if text == "❌ پایان گفتگو با هوش مصنوعی":
        context.user_data["in_ai_mode"] = False
        context.user_data.pop("ai_history", None)

        await update.message.reply_text("👋 گفتگو با هوش مصنوعی پایان یافت.")
        await start(update, context)
        return

    # اگر کاربر در حالت هوش مصنوعی بود
    if context.user_data.get("in_ai_mode", False):
        await ask_groq_ai(update, context, text)
        return

    # پیام‌های منوی عادی
    if text == "⏰ ساعت فعلی":
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await update.message.reply_text(f"ساعت فعلی: {current_time}")

    elif text == "🎲 عدد شانس":
        lucky_num = random.randint(1, 100)
        await update.message.reply_text(f"عدد شانس شما: {lucky_num}")

    elif text == "🤖 پرسش از هوش مصنوعی":
        await start_ai_prompt(update, context)

    else:
        await update.message.reply_text(
            "متوجه نشدم! لطفاً از دکمه‌های منو استفاده کنید "
            "یا روی «🤖 پرسش از هوش مصنوعی» بزنید."
        )


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("متغیر BOT_TOKEN تنظیم نشده است.")

    print("Using Groq model:", GROQ_MODEL)

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
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("restart", restart),
        ],
    )

    app.add_handler(reg_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ai", start_ai_prompt))
    app.add_handler(CommandHandler("users", show_users))
    app.add_handler(CommandHandler("export", export_users))
    app.add_handler(CommandHandler("restart", restart))

    # کنترل‌کننده تمام متون و دکمه‌های بات
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
