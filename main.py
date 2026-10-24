import os
import re
import logging
import sqlite3
import psycopg2
import psycopg2.extras
from datetime import datetime
import pytz
import pandas as pd
from dotenv import load_dotenv

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ----------------------------------------------------
# 1. تنظیمات اولیه و لاگینگ
# ----------------------------------------------------
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "93543054"))
DATABASE_URL = os.getenv("DATABASE_URL")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", "10000"))

EXCEL_FILE = "products.xlsx"
TEHRAN_TZ = pytz.timezone("Asia/Tehran")

# ----------------------------------------------------
# 2. وضعیت‌های ConversationHandler
# ----------------------------------------------------
# ثبت‌نام و پروفایل
REG_FULL_NAME, REG_PHONE, REG_ADDRESS, REG_POSTAL = range(4)

# سفارش و ارسال فیش
ORDER_AWAITING_RECEIPT = 10

# ----------------------------------------------------
# 3. توابع کمکی تبدیل اعداد و اعتبارسنجی
# ----------------------------------------------------
PERSIAN_TO_ENGLISH_MAP = {
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
    '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
}

def normalize_digits(text: str) -> str:
    """تبدیل تمام اعداد فارسی و عربی به انگلیسی"""
    if not text:
        return ""
    for fa_digit, en_digit in PERSIAN_TO_ENGLISH_MAP.items():
        text = text.replace(fa_digit, en_digit)
    return text.strip()

PHONE_REGEX = re.compile(r"^(?:(?:\+?98)|0)?9\d{9}$")
POSTAL_REGEX = re.compile(r"^\d{10}$")
NAME_REGEX = re.compile(r"^[\u0600-\u06FFa-zA-Z\s]{3,50}$")

# ----------------------------------------------------
# 4. ارتباط با دیتابیس (SQLite / PostgreSQL)
# ----------------------------------------------------
def get_db_connection():
    if DATABASE_URL:
        # اتصال به PostgreSQL در Render
        conn = psycopg2.connect(DATABASE_URL)
        return conn, "postgres"
    else:
        # اتصال محلی SQLite
        conn = sqlite3.connect("shop.db")
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"


def init_db():
    """ایجاد جداول و اعمال میگریشن‌های خودکار برای سازگاری کامل دیتابیس قبلی"""
    conn, db_type = get_db_connection()
    cur = conn.cursor()

    if db_type == "postgres":
        # 1. ساخت جداول پایه در صورت عدم وجود
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                full_name TEXT,
                phone TEXT,
                address TEXT,
                postal_code TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                product_sku TEXT,
                product_name TEXT,
                quantity INT DEFAULT 1,
                total_price BIGINT,
                receipt_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 2. میگریشن خودکار کاربران (افزودن ستون‌های جاافتاده در صورت وجود جدول قدیمی)
        user_columns = [
            ("full_name", "TEXT"),
            ("phone", "TEXT"),
            ("address", "TEXT"),
            ("postal_code", "TEXT"),
            ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        ]
        for col_name, col_type in user_columns:
            cur.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_type};")

        # 3. میگریشن خودکار سفارشات (حل قطعی مشکل خطا در ثبت فیش)
        order_columns = [
            ("product_sku", "TEXT"),
            ("product_name", "TEXT"),
            ("quantity", "INT DEFAULT 1"),
            ("total_price", "BIGINT"),
            ("receipt_file_id", "TEXT"),
            ("status", "TEXT DEFAULT 'pending'"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        ]
        for col_name, col_type in order_columns:
            cur.execute(f"ALTER TABLE orders ADD COLUMN IF NOT EXISTS {col_name} {col_type};")

    else:
        # اسکریپت SQLite محلی
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                phone TEXT,
                address TEXT,
                postal_code TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                product_sku TEXT,
                product_name TEXT,
                quantity INTEGER DEFAULT 1,
                total_price INTEGER,
                receipt_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

    conn.commit()
    cur.close()
    conn.close()
    logger.info("Database schema initialized and verified successfully.")


# ----------------------------------------------------
# 5. لود و مدیریت محصولات از Excel
# ----------------------------------------------------
def get_products():
    try:
        df = pd.read_excel(EXCEL_FILE)
        return df.to_dict(orient="records")
    except Exception as e:
        logger.error(f"Error loading products: {e}")
        return []


def get_product_by_sku(sku: str):
    products = get_products()
    for prod in products:
        if str(prod.get("sku", "")).strip() == str(sku).strip():
            return prod
    return None


# ----------------------------------------------------
# 6. دکمه‌ها و منوهای ناوبری
# ----------------------------------------------------
def main_menu_keyboard():
    keyboard = [
        [KeyboardButton("🛍 مشاهده و خرید محصولات")],
        [KeyboardButton("📋 ثبت / ویرایش اطلاعات"), KeyboardButton("📦 پیگیری سفارشات")],
        [KeyboardButton("📞 ارتباط با ما و راهنمای سفارش")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def cancel_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ انصراف")]], resize_keyboard=True)


# ----------------------------------------------------
# 7. هندلرهای دستورات و دکمه‌های اصلی
# ----------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🌸 به فروشگاه عطر آرمین خوش آمدید 🌸\n\n"
        "مجموعه‌ای از اصیل‌ترین روایح و عطرهای اورجینال با بهترین کیفیت.\n"
        "برای ثبت سریع سفارش، ابتدا اطلاعات پستی خود را در بخش «ثبت / ویرایش اطلاعات» تکمیل فرمایید."
    )
    await update.message.reply_text(welcome_text, reply_markup=main_menu_keyboard())


async def about_us_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info_text = (
        "🌸 **فروشگاه عطر آرمین** 🌸\n\n"
        "✨ ضمانت اصالت ۱۰۰٪ تمامی محصولات\n"
        "🚚 ارسال به سراسر کشور با بسته‌بندی ایمن\n"
        "💳 پرداخت امن از طریق کارت به کارت با تاییدیه ادمین\n\n"
        "📞 پشتیبانی تلگرام: @Armin_Admin"
    )
    await update.message.reply_text(info_text, parse_mode="Markdown")


async def show_products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = get_products()
    if not products:
        await update.message.reply_text("متأسفانه در حال حاضر محصولی در لیست موجود نیست.")
        return

    for prod in products:
        sku = prod.get("sku", "")
        name = prod.get("name", "نامشخص")
        volume = prod.get("volume", "")
        price = prod.get("price", 0)
        desc = prod.get("description", "")

        msg = (
            f"✨ **{name}**\n"
            f"▫️ حجم: {volume}\n"
            f"▫️ توضیحات: {desc}\n"
            f"💰 قیمت: {int(price):,} تومان"
        )
        inline_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 خرید این محصول", callback_data=f"buy_{sku}")]
        ])
        await update.message.reply_text(msg, reply_markup=inline_kb, parse_mode="Markdown")


# ----------------------------------------------------
# 8. فرآیند ثبت و ویرایش اطلاعات کاربر (ConversationHandler)
# ----------------------------------------------------
async def reg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "لطفاً نام و نام‌خانوادگی خود را وارد فرمایید:\n(برای انصراف دکمه «❌ انصراف» را بزنید)",
        reply_markup=cancel_keyboard()
    )
    return REG_FULL_NAME


async def reg_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not NAME_REGEX.match(text):
        await update.message.reply_text(
            "⚠️ نام وارد شده معتبر نیست. لطفاً نام و نام‌خانوادگی خود را با حروف فارسی یا انگلیسی وارد فرمایید:"
        )
        return REG_FULL_NAME

    context.user_data["reg_name"] = text
    await update.message.reply_text(
        "📱 شماره همراه خود را وارد کنید (مثال: 09121234567):",
        reply_markup=cancel_keyboard()
    )
    return REG_PHONE


async def reg_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.strip()
    normalized = normalize_digits(raw_text)

    if not PHONE_REGEX.match(normalized):
        await update.message.reply_text(
            "⚠️ شماره موبایل نامعتبر است! لطفاً یک شماره موبایل صحیح ۱۱ رقمی (شروع با 09) وارد کنید:"
        )
        return REG_PHONE

    context.user_data["reg_phone"] = normalized
    await update.message.reply_text(
        "📍 نشانی دقیق پستی جهت تحویل سفارش را بنویسید:",
        reply_markup=cancel_keyboard()
    )
    return REG_ADDRESS


async def reg_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 8:
        await update.message.reply_text(
            "⚠️ لطفاً نشانی پستی را به صورت کامل (شامل شهر، خیابان، پلاک، واحد) بنویسید:"
        )
        return REG_ADDRESS

    context.user_data["reg_address"] = text
    await update.message.reply_text(
        "📮 کدپستی ۱۰ رقمی خود را وارد فرمایید:",
        reply_markup=cancel_keyboard()
    )
    return REG_POSTAL


async def reg_postal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.strip()
    normalized = normalize_digits(raw_text)

    if not POSTAL_REGEX.match(normalized):
        await update.message.reply_text(
            "⚠️ کدپستی باید دقیقاً ۱۰ رقم عددی باشد. لطفاً دوباره وارد کنید:"
        )
        return REG_POSTAL

    user_id = update.effective_user.id
    name = context.user_data.get("reg_name")
    phone = context.user_data.get("reg_phone")
    address = context.user_data.get("reg_address")
    postal = normalized

    try:
        conn, db_type = get_db_connection()
        cur = conn.cursor()
        now = datetime.now(TEHRAN_TZ)

        if db_type == "postgres":
            cur.execute("""
                INSERT INTO users (user_id, full_name, phone, address, postal_code, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE
                SET full_name = EXCLUDED.full_name,
                    phone = EXCLUDED.phone,
                    address = EXCLUDED.address,
                    postal_code = EXCLUDED.postal_code,
                    updated_at = EXCLUDED.updated_at;
            """, (user_id, name, phone, address, postal, now))
        else:
            cur.execute("""
                INSERT INTO users (user_id, full_name, phone, address, postal_code, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    full_name=excluded.full_name,
                    phone=excluded.phone,
                    address=excluded.address,
                    postal_code=excluded.postal_code,
                    updated_at=excluded.updated_at;
            """, (user_id, name, phone, address, postal, now))

        conn.commit()
        cur.close()
        conn.close()

        await update.message.reply_text(
            "✅ اطلاعات پستی و هویتی شما با موفقیت ثبت شد.",
            reply_markup=main_menu_keyboard()
        )
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error saving user profile: {e}")
        await update.message.reply_text(
            "متأسفانه خطایی در ذخیره اطلاعات رخ داد. لطفاً کمی بعد مجدداً تلاش فرمایید.",
            reply_markup=main_menu_keyboard()
        )
        return ConversationHandler.END


async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "فرآیند ثبت اطلاعات لغو شد.",
        reply_markup=main_menu_keyboard()
    )
    return ConversationHandler.END


# ----------------------------------------------------
# 9. فرآیند خرید و پرداخت (ثبت سفارش)
# ----------------------------------------------------
async def buy_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    sku = query.data.split("_")[1]
    product = get_product_by_sku(sku)

    if not product:
        await query.message.reply_text("محصول مورد نظر یافت نشد.")
        return

    # بررسی ثبت بودن پروفایل کاربر قبل از خرید
    conn, db_type = get_db_connection()
    cur = conn.cursor()
    if db_type == "postgres":
        cur.execute("SELECT full_name, phone, address FROM users WHERE user_id = %s;", (user_id,))
    else:
        cur.execute("SELECT full_name, phone, address FROM users WHERE user_id = ?;", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user or not user[0] or not user[1]:
        await query.message.reply_text(
            "⚠️ ابتدا باید اطلاعات پستی و تماس خود را ثبت کنید.\nلطفاً روی دکمه «📋 ثبت / ویرایش اطلاعات» در منو کلیک فرمایید."
        )
        return

    # ذخیره در context موقت
    context.user_data["pending_order"] = {
        "sku": sku,
        "name": product.get("name"),
        "price": product.get("price"),
        "user_name": user[0],
        "user_phone": user[1]
    }

    card_msg = (
        f"🛍 شما محصول «**{product.get('name')}**» را انتخاب کردید.\n\n"
        f"💰 مبلغ قابل پرداخت: **{int(product.get('price', 0)):,} تومان**\n\n"
        f"💳 شماره کارت جهت واریز:\n"
        f"`6037-9975-1234-5678`\n"
        f"به نام: **بانک ملی - به نام آرمین**\n\n"
        f"📸 لطفاً پس از واریز، **عکس فیش واریزی** خود را ارسال کنید:\n"
        f"(یا برای انصراف دکمه «❌ انصراف» را بزنید)"
    )
    await query.message.reply_text(card_msg, parse_mode="Markdown", reply_markup=cancel_keyboard())
    return ORDER_AWAITING_RECEIPT


async def order_receipt_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = context.user_data.get("pending_order")
    if not pending:
        await update.message.reply_text("اطلاعات سفارش منقضی شده است. لطفاً از ابتدا محصول را انتخاب کنید.", reply_markup=main_menu_keyboard())
        return ConversationHandler.END

    photo = update.message.photo[-1]
    file_id = photo.file_id
    user_id = update.effective_user.id
    now = datetime.now(TEHRAN_TZ)

    try:
        conn, db_type = get_db_connection()
        cur = conn.cursor()

        if db_type == "postgres":
            cur.execute("""
                INSERT INTO orders (user_id, product_sku, product_name, quantity, total_price, receipt_file_id, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)
                RETURNING id;
            """, (user_id, pending["sku"], pending["name"], 1, pending["price"], file_id, now))
            order_id = cur.fetchone()[0]
        else:
            cur.execute("""
                INSERT INTO orders (user_id, product_sku, product_name, quantity, total_price, receipt_file_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?);
            """, (user_id, pending["sku"], pending["name"], 1, pending["price"], file_id, now))
            order_id = cur.lastrowid

        conn.commit()
        cur.close()
        conn.close()

        # پیام به خریدار
        await update.message.reply_text(
            f"✅ فیش واریزی شما با شماره پیگیری `{order_id}` ثبت شد.\n"
            "پس از تایید مدیریت، سفارش شما بسته‌بندی و ارسال خواهد شد.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard()
        )

        # ارسال فیش و مشخصات برای ادمین
        admin_caption = (
            f"🔔 **سفارش جدید ثبت شد!** (کد سفارش: #{order_id})\n\n"
            f"👤 خریدار: {pending['user_name']}\n"
            f"📱 تلفن: `{pending['user_phone']}`\n"
            f"🛍 محصول: {pending['name']}\n"
            f"💰 مبلغ: {int(pending['price']):,} تومان\n"
            f"🆔 شناسه تلگرام خریدار: `{user_id}`"
        )
        admin_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ تایید سفارش", callback_data=f"approve_{order_id}"),
                InlineKeyboardButton("❌ رد سفارش", callback_data=f"reject_{order_id}")
            ]
        ])
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=file_id,
            caption=admin_caption,
            reply_markup=admin_kb,
            parse_mode="Markdown"
        )

        context.user_data.pop("pending_order", None)
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error handling order receipt: {e}")
        await update.message.reply_text(
            "خطایی در ثبت سفارش رخ داد. لطفاً مجدداً تلاش کنید.",
            reply_markup=main_menu_keyboard()
        )
        return ConversationHandler.END


async def order_receipt_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_order", None)
    await update.message.reply_text("ثبت سفارش لغو شد.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ----------------------------------------------------
# 10. پنل و کلیدهای تایید/رد ادمین
# ----------------------------------------------------
async def admin_decision_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    action, order_id = data.split("_")
    new_status = "approved" if action == "approve" else "rejected"

    conn, db_type = get_db_connection()
    cur = conn.cursor()

    if db_type == "postgres":
        cur.execute("SELECT user_id, product_name FROM orders WHERE id = %s;", (order_id,))
    else:
        cur.execute("SELECT user_id, product_name FROM orders WHERE id = ?;", (order_id,))
    row = cur.fetchone()

    if not row:
        await query.message.reply_text("سفارش مورد نظر پیدا نشد.")
        cur.close()
        conn.close()
        return

    buyer_id, prod_name = row[0], row[1]

    if db_type == "postgres":
        cur.execute("UPDATE orders SET status = %s WHERE id = %s;", (new_status, order_id))
    else:
        cur.execute("UPDATE orders SET status = ? WHERE id = ?;", (new_status, order_id))

    conn.commit()
    cur.close()
    conn.close()

    status_persian = "تایید شد ✅" if new_status == "approved" else "رد شد ❌"
    await query.edit_message_caption(
        caption=f"{query.message.caption}\n\n📌 **وضعیت:** {status_persian}",
        parse_mode="Markdown"
    )

    # اطلاع به خریدار
    if new_status == "approved":
        msg_to_buyer = f"🎉 سفارش شما برای محصول «{prod_name}» تایید گردید و آماده ارسال است!"
    else:
        msg_to_buyer = f"❌ سفارش شما برای محصول «{prod_name}» به دلیل عدم تطابق فیش واریز تایید نشد. جهت پیگیری با پشتیبانی تماس بگیرید."

    try:
        await context.bot.send_message(chat_id=buyer_id, text=msg_to_buyer)
    except Exception as e:
        logger.error(f"Could not notify user {buyer_id}: {e}")


# ----------------------------------------------------
# 11. پیگیری سفارشات توسط خریدار
# ----------------------------------------------------
async def track_orders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn, db_type = get_db_connection()
    cur = conn.cursor()

    if db_type == "postgres":
        cur.execute("""
            SELECT id, product_name, total_price, status, created_at
            FROM orders WHERE user_id = %s ORDER BY id DESC LIMIT 5;
        """, (user_id,))
    else:
        cur.execute("""
            SELECT id, product_name, total_price, status, created_at
            FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 5;
        """, (user_id,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        await update.message.reply_text("شما هنوز هیچ سفارشی ثبت نکرده‌اید.")
        return

    status_map = {
        "pending": "در انتظار تایید فیش ⏳",
        "approved": "تایید شده و در حال پردازش/ارسال 📦",
        "rejected": "عدم تایید ❌"
    }

    report = "📦 **لیست آخرین سفارش‌های شما:**\n\n"
    for r in rows:
        report += (
            f"🔹 شماره سفارش: #{r[0]}\n"
            f"▫️ محصول: {r[1]}\n"
            f"▫️ مبلغ: {int(r[2]):,} تومان\n"
            f"▫️ وضعیت: {status_map.get(r[3], r[3])}\n\n"
        )

    await update.message.reply_text(report, parse_mode="Markdown")


# ----------------------------------------------------
# 12. اجرای برنامه (Webhook روی Render یا Polling لوکال)
# ----------------------------------------------------
def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # گفتگوی ثبت اطلاعات
    reg_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📋 ثبت / ویرایش اطلاعات$"), reg_start)],
        states={
            REG_FULL_NAME: [
                MessageHandler(filters.Regex("^❌ انصراف$"), reg_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_full_name)
            ],
            REG_PHONE: [
                MessageHandler(filters.Regex("^❌ انصراف$"), reg_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_phone)
            ],
            REG_ADDRESS: [
                MessageHandler(filters.Regex("^❌ انصراف$"), reg_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_address)
            ],
            REG_POSTAL: [
                MessageHandler(filters.Regex("^❌ انصراف$"), reg_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_postal)
            ],
        },
        fallbacks=[MessageHandler(filters.Regex("^❌ انصراف$"), reg_cancel)],
    )

    # گفتگوی دریافت فیش واریز
    order_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(buy_callback_handler, pattern="^buy_")],
        states={
            ORDER_AWAITING_RECEIPT: [
                MessageHandler(filters.Regex("^❌ انصراف$"), order_receipt_cancel),
                MessageHandler(filters.PHOTO, order_receipt_received),
            ]
        },
        fallbacks=[MessageHandler(filters.Regex("^❌ انصراف$"), order_receipt_cancel)],
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(reg_conv)
    app.add_handler(order_conv)
    app.add_handler(MessageHandler(filters.Regex("^🛍 مشاهده و خرید محصولات$"), show_products_handler))
    app.add_handler(MessageHandler(filters.Regex("^📦 پیگیری سفارشات$"), track_orders_handler))
    app.add_handler(MessageHandler(filters.Regex("^📞 ارتباط با ما و راهنمای سفارش$"), about_us_handler))
    app.add_handler(CallbackQueryHandler(admin_decision_handler, pattern="^(approve|reject)_"))

    if RENDER_EXTERNAL_URL:
        # اجرای وب‌هوک در سرور Render
        webhook_url = f"{RENDER_EXTERNAL_URL}/telegram"
        logger.info(f"Starting Webhook on port {PORT} with URL: {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="telegram",
            webhook_url=webhook_url
        )
    else:
        # اجرای محلی به روش Polling
        logger.info("Starting local polling...")
        app.run_polling()


if __name__ == "__main__":
    main()
