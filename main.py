import os
import re
import io
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
# 1. تنظیمات و متغیرهای محیطی
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

EXCEL_FILE = os.path.join(os.path.dirname(__file__), "products.xlsx")
TEHRAN_TZ = pytz.timezone("Asia/Tehran")

# ----------------------------------------------------
# 2. وضعیت‌های ConversationHandler
# ----------------------------------------------------
REG_FULL_NAME, REG_PHONE, REG_ADDRESS, REG_POSTAL = range(4)
ORDER_AWAITING_RECEIPT = 10
ADMIN_AWAITING_EXCEL = 20

# ----------------------------------------------------
# 3. توابع کمکی
# ----------------------------------------------------
PERSIAN_TO_ENGLISH_MAP = {
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
    '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
}

def normalize_digits(text: str) -> str:
    if not text:
        return ""
    for fa_digit, en_digit in PERSIAN_TO_ENGLISH_MAP.items():
        text = text.replace(fa_digit, en_digit)
    return text.strip()

PHONE_REGEX = re.compile(r"^(?:(?:\+?98)|0)?9\d{9}$")
POSTAL_REGEX = re.compile(r"^\d{10}$")
NAME_REGEX = re.compile(r"^[\u0600-\u06FFa-zA-Z\s]{3,50}$")

# ----------------------------------------------------
# 4. اتصال به دیتابیس
# ----------------------------------------------------
def get_db_connection():
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL)
        return conn, "postgres"
    else:
        conn = sqlite3.connect("shop.db")
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"


def init_db():
    conn, db_type = get_db_connection()
    cur = conn.cursor()

    if db_type == "postgres":
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
        user_cols = ["full_name TEXT", "phone TEXT", "address TEXT", "postal_code TEXT", "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"]
        for col in user_cols:
            name, ctype = col.split(" ", 1)
            cur.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {name} {ctype};")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id SERIAL PRIMARY KEY,
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
        order_cols = [
            ("product_sku", "TEXT"),
            ("product_name", "TEXT"),
            ("quantity", "INT DEFAULT 1"),
            ("total_price", "BIGINT"),
            ("receipt_file_id", "TEXT"),
            ("status", "TEXT DEFAULT 'pending'"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        ]
        for name, ctype in order_cols:
            cur.execute(f"ALTER TABLE orders ADD COLUMN IF NOT EXISTS {name} {ctype};")

    else:
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
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    logger.info("Database initialized successfully.")


# ----------------------------------------------------
# 5. مدیریت محصولات و خواندن از اکسل
# ----------------------------------------------------
DEFAULT_PRODUCTS = [
    {
        "sku": "PARFUM-001",
        "name": "عطر لالیک مشکی (Encre Noire)",
        "volume": "100 میل",
        "price": 2850000,
        "description": "رایحه گرم و تلخ، بوی دلپذیر چوب سوخته و خس‌خس، ماندگاری بالا."
    },
    {
        "sku": "PARFUM-002",
        "name": "عطر کرید اونتوس (Creed Aventus)",
        "volume": "100 میل",
        "price": 4900000,
        "description": "خنک و میوه‌ای-چرمی، نماد اعتماد به نفس، پخش بوی استثنایی."
    },
    {
        "sku": "PARFUM-003",
        "name": "عطر باکارات رژ ۵۴۰ (Baccarat Rouge)",
        "volume": "70 میل",
        "price": 5400000,
        "description": "رایحه شیرین، شرقی و گلی، بسیار لوکس، اشرافی و جذاب."
    },
    {
        "sku": "PARFUM-004",
        "name": "عطر ساواج دیور (Dior Sauvage)",
        "volume": "100 میل",
        "price": 3950000,
        "description": "خنک، تند و آروماتیک مرکباتی، پرطرفدارترین عطر چهارفصل."
    }
]

def get_products():
    if os.path.exists(EXCEL_FILE):
        try:
            df = pd.read_excel(EXCEL_FILE)
            df.columns = [str(c).strip().lower() for c in df.columns]
            products = []
            for _, row in df.iterrows():
                products.append({
                    "sku": str(row.get("sku", "")).strip(),
                    "name": str(row.get("name", "")).strip(),
                    "volume": str(row.get("volume", "")).strip() if pd.notna(row.get("volume")) else "",
                    "price": int(row.get("price", 0)) if pd.notna(row.get("price")) else 0,
                    "description": str(row.get("description", "")).strip() if pd.notna(row.get("description")) else ""
                })
            if products and len(products) > 0 and products[0]["sku"]:
                return products
        except Exception as e:
            logger.error(f"Error reading excel: {e}")

    return DEFAULT_PRODUCTS


def get_product_by_sku(sku: str):
    products = get_products()
    for prod in products:
        if str(prod.get("sku", "")).strip() == str(sku).strip():
            return prod
    return None


# ----------------------------------------------------
# 6. کیبوردهای کاربران و ادمین
# ----------------------------------------------------
def main_menu_keyboard(is_admin: bool = False):
    keyboard = [
        [KeyboardButton("🛍 مشاهده و خرید محصولات")],
        [KeyboardButton("👤 مشخصات من"), KeyboardButton("📋 ثبت / ویرایش اطلاعات")],
        [KeyboardButton("📦 پیگیری سفارشات"), KeyboardButton("📞 ارتباط با ما و راهنمای سفارش")]
    ]
    if is_admin:
        # دکمه‌های ویژه ادمین
        keyboard.append([KeyboardButton("📥 بروزرسانی محصولات (آپلود اکسل)"), KeyboardButton("📤 خروجی اکسل سفارشات")])
        keyboard.append([KeyboardButton("📊 آمار و گزارش کلی")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def cancel_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ انصراف")]], resize_keyboard=True)


# ----------------------------------------------------
# 7. هندلرهای ناوبری و عمومی
# ----------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin = (user_id == ADMIN_ID)
    welcome = (
        "🌸 به فروشگاه عطر آرمین خوش آمدید 🌸\n\n"
        "مجموعه‌ای از اصیل‌ترین روایح و عطرهای اورجینال با بهترین کیفیت.\n"
        "برای ثبت سریع سفارش، ابتدا مشخصات خود را در بخش «ثبت / ویرایش اطلاعات» تکمیل فرمایید."
    )
    if is_admin:
        welcome += "\n\n👑 **سلام آرمین جان؛ به پنل مدیریت خوش آمدی! گزینه‌های مدیریتی در منو فعال شدند.**"
    await update.message.reply_text(welcome, reply_markup=main_menu_keyboard(is_admin))


async def about_us_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = (
        "🌸 فروشگاه عطر آرمین 🌸\n\n"
        "✨ ضمانت اصالت ۱۰۰٪ تمامی محصولات\n"
        "🚚 ارسال سریع و ایمن به سراسر کشور\n"
        "💳 پرداخت مطمئن از طریق کارت به کارت با تایید ادمین\n\n"
        "📞 پشتیبانی در تلگرام: @Armin_Admin"
    )
    await update.message.reply_text(info)


async def show_profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn, db_type = get_db_connection()
    cur = conn.cursor()

    if db_type == "postgres":
        cur.execute("SELECT full_name, phone, address, postal_code FROM users WHERE user_id = %s;", (user_id,))
    else:
        cur.execute("SELECT full_name, phone, address, postal_code FROM users WHERE user_id = ?;", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user or not user[0]:
        await update.message.reply_text(
            "⚠️ شما هنوز مشخصات خود را ثبت نکرده‌اید.\nروی دکمه «📋 ثبت / ویرایش اطلاعات» بزنید."
        )
        return

    text = (
        "👤 مشخصات ثبت‌شده شما:\n\n"
        f"▫️ نام: {user[0]}\n"
        f"▫️ شماره تماس: {user[1]}\n"
        f"▫️ نشانی: {user[2]}\n"
        f"▫️ کد پستی: {user[3]}\n\n"
        "جهت تغییر هر یک، می‌توانید مجدداً از گزینه «📋 ثبت / ویرایش اطلاعات» استفاده کنید."
    )
    await update.message.reply_text(text)


async def show_products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = get_products()
    if not products:
        await update.message.reply_text("محصولی یافت نشد.")
        return

    for prod in products:
        sku = prod["sku"]
        name = prod["name"]
        vol = f" | حجم: {prod['volume']}" if prod.get('volume') else ""
        desc = f"\n▫️ {prod['description']}" if prod.get('description') else ""
        price = f"{int(prod['price']):,} تومان"

        msg = (
            f"✨ {name}{vol}\n"
            f"💰 قیمت: {price}"
            f"{desc}"
        )
        inline_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 خرید این محصول", callback_data=f"buy_{sku}")]
        ])
        await update.message.reply_text(msg, reply_markup=inline_kb)


# ----------------------------------------------------
# 8. فرآیند ثبت و ویرایش مشخصات
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
    norm = normalize_digits(raw_text)

    if not PHONE_REGEX.match(norm):
        await update.message.reply_text(
            "⚠️ شماره موبایل نامعتبر است! لطفاً یک شماره موبایل صحیح ۱۱ رقمی (شروع با 09) وارد کنید:"
        )
        return REG_PHONE

    context.user_data["reg_phone"] = norm
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
    norm = normalize_digits(raw_text)

    if not POSTAL_REGEX.match(norm):
        await update.message.reply_text(
            "⚠️ کدپستی باید دقیقاً ۱۰ رقم عددی باشد. لطفاً دوباره وارد کنید:"
        )
        return REG_POSTAL

    user_id = update.effective_user.id
    name = context.user_data.get("reg_name")
    phone = context.user_data.get("reg_phone")
    address = context.user_data.get("reg_address")
    postal = norm

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

        is_admin = (user_id == ADMIN_ID)
        await update.message.reply_text(
            "✅ اطلاعات پستی و هویتی شما با موفقیت ثبت گردید.",
            reply_markup=main_menu_keyboard(is_admin)
        )
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error saving user profile: {e}")
        is_admin = (user_id == ADMIN_ID)
        await update.message.reply_text(
            "متأسفانه خطایی در ذخیره اطلاعات رخ داد. لطفاً مجدداً امتحان کنید.",
            reply_markup=main_menu_keyboard(is_admin)
        )
        return ConversationHandler.END


async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_admin = (update.effective_user.id == ADMIN_ID)
    await update.message.reply_text("فرآیند لغو شد.", reply_markup=main_menu_keyboard(is_admin))
    return ConversationHandler.END


# ----------------------------------------------------
# 9. فرآیند خرید و ارسال فیش واریز
# ----------------------------------------------------
async def buy_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    sku = query.data.split("_", 1)[1]
    prod = get_product_by_sku(sku)

    if not prod:
        await query.message.reply_text("محصول یافت نشد.")
        return

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
            "⚠️ ابتدا باید اطلاعات پستی و تماس خود را ثبت کنید.\nلطفاً روی دکمه «📋 ثبت / ویرایش اطلاعات» در منو کلیک کنید."
        )
        return

    context.user_data["pending_order"] = {
        "sku": sku,
        "name": prod["name"],
        "price": prod["price"],
        "user_name": user[0],
        "user_phone": user[1]
    }

    card_text = (
        f"🛍 شما محصول «{prod['name']}» را انتخاب کردید.\n\n"
        f"💰 مبلغ قابل پرداخت: {int(prod['price']):,} تومان\n\n"
        f"💳 شماره کارت جهت واریز:\n"
        f"6037-9975-1234-5678\n"
        f"به نام: بانک ملی - به نام آرمین\n\n"
        f"📸 لطفاً پس از واریز، عکس فیش واریزی خود را ارسال فرمایید:\n"
        f"(یا برای لغو دکمه «❌ انصراف» را بزنید)"
    )
    await query.message.reply_text(card_text, reply_markup=cancel_keyboard())
    return ORDER_AWAITING_RECEIPT


async def order_receipt_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = context.user_data.get("pending_order")
    is_admin = (update.effective_user.id == ADMIN_ID)
    if not pending:
        await update.message.reply_text("اطلاعات سفارش منقضی شده است.", reply_markup=main_menu_keyboard(is_admin))
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
                RETURNING order_id;
            """, (user_id, pending["sku"], pending["name"], 1, pending["price"], file_id, now))
            row = cur.fetchone()
            order_id = row[0] if row else 1
        else:
            cur.execute("""
                INSERT INTO orders (user_id, product_sku, product_name, quantity, total_price, receipt_file_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?);
            """, (user_id, pending["sku"], pending["name"], 1, pending["price"], file_id, now))
            order_id = cur.lastrowid

        conn.commit()
        cur.close()
        conn.close()

        await update.message.reply_text(
            f"✅ فیش واریزی شما با شماره پیگیری #{order_id} ثبت شد.\n"
            "پس از تایید ادمین، سفارش شما بسته‌بندی و ارسال خواهد شد.",
            reply_markup=main_menu_keyboard(is_admin)
        )

        admin_caption = (
            f"🔔 سفارش جدید ثبت شد! (کد #{order_id})\n\n"
            f"👤 خریدار: {pending['user_name']}\n"
            f"📱 تلفن: {pending['user_phone']}\n"
            f"🛍 محصول: {pending['name']}\n"
            f"💰 مبلغ: {int(pending['price']):,} تومان\n"
            f"🆔 تلگرام: {user_id}"
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
            reply_markup=admin_kb
        )

        context.user_data.pop("pending_order", None)
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error saving order: {e}")
        await update.message.reply_text("خطایی در ثبت سفارش رخ داد. لطفاً مجدداً تلاش کنید.", reply_markup=main_menu_keyboard(is_admin))
        return ConversationHandler.END


async def order_receipt_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_admin = (update.effective_user.id == ADMIN_ID)
    context.user_data.pop("pending_order", None)
    await update.message.reply_text("ثبت سفارش لغو شد.", reply_markup=main_menu_keyboard(is_admin))
    return ConversationHandler.END


# ----------------------------------------------------
# 10. تایید و رد توسط ادمین
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
        cur.execute("SELECT user_id, product_name FROM orders WHERE order_id = %s;", (order_id,))
    else:
        cur.execute("SELECT user_id, product_name FROM orders WHERE order_id = ?;", (order_id,))
    row = cur.fetchone()

    if not row:
        await query.message.reply_text("سفارش مورد نظر پیدا نشد.")
        cur.close()
        conn.close()
        return

    buyer_id, prod_name = row[0], row[1]

    if db_type == "postgres":
        cur.execute("UPDATE orders SET status = %s WHERE order_id = %s;", (new_status, order_id))
    else:
        cur.execute("UPDATE orders SET status = ? WHERE order_id = ?;", (new_status, order_id))

    conn.commit()
    cur.close()
    conn.close()

    status_fa = "تایید شد ✅" if new_status == "approved" else "رد شد ❌"
    await query.edit_message_caption(
        caption=f"{query.message.caption}\n\n📌 وضعیت: {status_fa}"
    )

    if new_status == "approved":
        msg = f"🎉 سفارش شما برای محصول «{prod_name}» تایید گردید و آماده ارسال است!"
    else:
        msg = f"❌ سفارش شما برای محصول «{prod_name}» به دلیل عدم تطابق فیش رد شد."

    try:
        await context.bot.send_message(chat_id=buyer_id, text=msg)
    except Exception as e:
        logger.error(f"Cannot message user {buyer_id}: {e}")


# ----------------------------------------------------
# 11. پیگیری سفارشات توسط خریدار
# ----------------------------------------------------
async def track_orders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn, db_type = get_db_connection()
    cur = conn.cursor()

    try:
        if db_type == "postgres":
            cur.execute("""
                SELECT order_id, product_name, total_price, status, created_at
                FROM orders WHERE user_id = %s ORDER BY order_id DESC LIMIT 5;
            """, (user_id,))
        else:
            cur.execute("""
                SELECT order_id, product_name, total_price, status, created_at
                FROM orders WHERE user_id = ? ORDER BY order_id DESC LIMIT 5;
            """, (user_id,))
        rows = cur.fetchall()
    except Exception as e:
        logger.error(f"Error querying orders: {e}")
        rows = []
    finally:
        cur.close()
        conn.close()

    if not rows:
        await update.message.reply_text("شما هنوز هیچ سفارشی ثبت نکرده‌اید.")
        return

    status_map = {
        "pending": "در انتظار بررسی فیش ⏳",
        "approved": "تایید شده و در حال پردازش/ارسال 📦",
        "rejected": "رد شده ❌"
    }

    report = "📦 لیست آخرین سفارش‌های شما:\n\n"
    for r in rows:
        report += (
            f"🔹 شماره سفارش: #{r[0]}\n"
            f"▫️ محصول: {r[1]}\n"
            f"▫️ مبلغ: {int(r[2]):,} تومان\n"
            f"▫️ وضعیت: {status_map.get(r[3], r[3])}\n\n"
        )

    await update.message.reply_text(report)


# ----------------------------------------------------
# 12. بخش اختصاصی ادمین (IMPORT & EXPORT اکسل و گزارشات)
# ----------------------------------------------------

# الف) ارسال خروجی اکسل سفارشات به ادمین (EXPORT)
async def export_orders_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text("⏳ در حال استخراج و ساخت فایل اکسل سفارشات...")

    conn, db_type = get_db_connection()
    try:
        query = """
            SELECT 
                o.order_id AS "کد سفارش",
                o.user_id AS "شناسه تلگرام خریدار",
                u.full_name AS "نام خریدار",
                u.phone AS "تلفن تماس",
                u.address AS "آدرس پستی",
                u.postal_code AS "کد پستی",
                o.product_name AS "محصول",
                o.product_sku AS "کد محصول",
                o.total_price AS "مبلغ کل (تومان)",
                o.status AS "وضعیت سفارش",
                o.created_at AS "تاریخ ثبت"
            FROM orders o
            LEFT JOIN users u ON o.user_id = u.user_id
            ORDER BY o.order_id DESC;
        """
        df = pd.read_sql_query(query, conn)
        conn.close()

        if df.empty:
            await update.message.reply_text("هیچ سفارشی در دیتابیس ثبت نشده است.")
            return

        # تبدیل به بایت و ارسال با تلگرام بدون ذخیره موقت
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name="Orders")
        output.seek(0)

        filename = f"orders_report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        await update.message.reply_document(
            document=output,
            filename=filename,
            caption="📊 فایل اکسل گزارش کامل سفارشات فروشگاه عطر آرمین خدمت شما."
        )

    except Exception as e:
        logger.error(f"Error exporting orders to excel: {e}")
        await update.message.reply_text(f"خطا در ایجاد خروجی اکسل: {e}")


# ب) دریافت فایل اکسل محصولات جدید از ادمین (IMPORT)
async def import_products_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    msg = (
        "📥 **ورود محصولات جدید از اکسل**\n\n"
        "لطفاً فایل اکسل محصولات (`.xlsx`) را به عنوان **Document (فایل)** بفرستید.\n\n"
        "⚠️ ستون‌های ضروری فایل اکسل:\n"
        "▫️ `sku` (شناسه یکتا مانند PARFUM-01)\n"
        "▫️ `name` (نام عطر)\n"
        "▫️ `price` (قیمت به تومان)\n"
        "▫️ `volume` (حجم مثلا ۱۰۰ میل)\n"
        "▫️ `description` (توضیحات رایحه)\n\n"
        "برای لغو روی دکمه «❌ انصراف» بزنید."
    )
    await update.message.reply_text(msg, reply_markup=cancel_keyboard())
    return ADMIN_AWAITING_EXCEL


async def import_products_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.endswith(('.xlsx', '.xls')):
        await update.message.reply_text("⚠️ لطفاً حتماً فایل با پسوند `.xlsx` ارسال کنید:")
        return ADMIN_AWAITING_EXCEL

    try:
        new_file = await doc.get_file()
        await new_file.download_to_drive(EXCEL_FILE)

        # اعتبارسنجی اولیه فایل
        df = pd.read_excel(EXCEL_FILE)
        count = len(df)

        await update.message.reply_text(
            f"✅ فایل اکسل با موفقیت دریافت و بارگذاری شد!\n"
            f"تعداد {count} محصول هم‌اکنون در فروشگاه فعال و آماده خرید هستند.",
            reply_markup=main_menu_keyboard(is_admin=True)
        )
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error saving imported excel: {e}")
        await update.message.reply_text(
            f"خطا در خواندن فایل اکسل: {e}\nلطفاً ساختار ستون‌ها را بررسی فرمایید.",
            reply_markup=main_menu_keyboard(is_admin=True)
        )
        return ConversationHandler.END


async def import_products_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("فرآیند آپلود اکسل لغو شد.", reply_markup=main_menu_keyboard(is_admin=True))
    return ConversationHandler.END


# ج) گزارش آمار کلی سیستم برای ادمین
async def admin_stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    conn, db_type = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM users;")
    total_users = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM orders;")
    total_orders = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending';")
    pending_orders = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(total_price), 0) FROM orders WHERE status = 'approved';")
    total_revenue = cur.fetchone()[0]

    cur.close()
    conn.close()

    stat_msg = (
        "📊 **آمار و عملکرد فروشگاه عطر آرمین**\n\n"
        f"👥 کل کاربران ثبت‌شده: {total_users} نفر\n"
        f"📦 کل سفارش‌ها: {total_orders} عدد\n"
        f"⏳ سفارش‌های در انتظار تایید فیش: {pending_orders} عدد\n"
        f"💰 مجموع فروش تاییدشده: {int(total_revenue):,} تومان"
    )
    await update.message.reply_text(stat_msg)


# ----------------------------------------------------
# 13. راه‌اندازی و اتصال هندلرها
# ----------------------------------------------------
def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # گفتگوی ثبت اطلاعات کاربر
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

    # گفتگوی خرید و ارسال فیش
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

    # گفتگوی آپلود فایل اکسل جدید محصولات توسط ادمین (IMPORT)
    admin_import_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📥 بروزرسانی محصولات \\(آپلود اکسل\\)$"), import_products_start)],
        states={
            ADMIN_AWAITING_EXCEL: [
                MessageHandler(filters.Regex("^❌ انصراف$"), import_products_cancel),
                MessageHandler(filters.Document.ALL, import_products_receive),
            ]
        },
        fallbacks=[MessageHandler(filters.Regex("^❌ انصراف$"), import_products_cancel)],
    )

    # دستورات و کانورسیشن‌ها
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(reg_conv)
    app.add_handler(order_conv)
    app.add_handler(admin_import_conv)

    # کلیدهای منوی عمومی
    app.add_handler(MessageHandler(filters.Regex("^🛍 مشاهده و خرید محصولات$"), show_products_handler))
    app.add_handler(MessageHandler(filters.Regex("^👤 مشخصات من$"), show_profile_handler))
    app.add_handler(MessageHandler(filters.Regex("^📦 پیگیری سفارشات$"), track_orders_handler))
    app.add_handler(MessageHandler(filters.Regex("^📞 ارتباط با ما و راهنمای سفارش$"), about_us_handler))

    # کلیدهای پنل ادمین
    app.add_handler(MessageHandler(filters.Regex("^📤 خروجی اکسل سفارشات$"), export_orders_excel))
    app.add_handler(MessageHandler(filters.Regex("^📊 آمار و گزارش کلی$"), admin_stats_handler))

    app.add_handler(CallbackQueryHandler(admin_decision_handler, pattern="^(approve|reject)_"))

    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/telegram"
        logger.info(f"Webhook running on port {PORT} with URL {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="telegram",
            webhook_url=webhook_url
        )
    else:
        logger.info("Polling running...")
        app.run_polling()


if __name__ == "__main__":
    main()
