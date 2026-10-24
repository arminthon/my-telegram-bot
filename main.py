import os
import io
import logging
from datetime import datetime
import pytz
import jdatetime
import pandas as pd

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# تنظیمات لاگینگ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# بارگذاری متغیرهای محیطی
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "93543054"))
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

CARD_NUMBER = "6037-9975-1234-5678"
CARD_HOLDER = "بانک ملی - به نام آرمین"

# اتصال دیتابیس (سازگار با Render PostgreSQL و لوکال SQLite)
IS_POSTGRES = False
if DATABASE_URL and DATABASE_URL.startswith("postgres"):
    IS_POSTGRES = True
    import psycopg2
    from psycopg2.extras import RealDictCursor
else:
    import sqlite3

def get_db_connection():
    if IS_POSTGRES:
        db_url = DATABASE_URL
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(db_url)
    else:
        conn = sqlite3.connect("perfume_shop.db")
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # جدول کاربران
    if IS_POSTGRES:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                full_name TEXT,
                phone TEXT,
                address TEXT,
                postal_code TEXT,
                registered_at TIMESTAMP
            );
        """)
        # جدول محصولات
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                sku VARCHAR(100) PRIMARY KEY,
                product_name TEXT NOT NULL,
                brand TEXT,
                volume_ml INT,
                price_toman BIGINT NOT NULL,
                stock_qty INT DEFAULT 0,
                active BOOLEAN DEFAULT TRUE,
                tags TEXT,
                description TEXT,
                photo_file_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # جدول سفارش‌ها
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id SERIAL PRIMARY KEY,
                user_id BIGINT,
                product_sku VARCHAR(100),
                product_name TEXT,
                price_toman BIGINT,
                receipt_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP
            );
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                phone TEXT,
                address TEXT,
                postal_code TEXT,
                registered_at TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                sku TEXT PRIMARY KEY,
                product_name TEXT NOT NULL,
                brand TEXT,
                volume_ml INTEGER,
                price_toman INTEGER NOT NULL,
                stock_qty INTEGER DEFAULT 0,
                active BOOLEAN DEFAULT 1,
                tags TEXT,
                description TEXT,
                photo_file_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                product_sku TEXT,
                product_name TEXT,
                price_toman INTEGER,
                receipt_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP
            );
        """)

    # محصولات پیش‌فرض اولیه در صورت خالی بودن جدول
    cur.execute("SELECT COUNT(*) FROM products;")
    count = cur.fetchone()[0]
    if count == 0:
        default_items = [
            ("perfume_dior_sauvage_100", "عطر Dior Sauvage", "Dior", 100, 2500000, 15, True, "مردانه,خنک,تند", "یکی از پرطرفدارترین عطرهای مردانه با رایحه ترنج و فلفل سیچوان"),
            ("perfume_bleu_chanel_100", "عطر Bleu de Chanel", "Chanel", 100, 3200000, 10, True, "مردانه,خنک,چوبی", "عطری ماندگار و باوقار مناسب استفاده رسمی و روزمره"),
            ("perfume_ysl_lhomme_100", "عطر YSL L'Homme", "YSL", 100, 2800000, 8, True, "مردانه,ملایم,چوبی", "ترکیبی جذاب از زنجبیل و رایحه‌های مرکباتی و چوبی")
        ]
        if IS_POSTGRES:
            cur.executemany("""
                INSERT INTO products (sku, product_name, brand, volume_ml, price_toman, stock_qty, active, tags, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, default_items)
        else:
            cur.executemany("""
                INSERT INTO products (sku, product_name, brand, volume_ml, price_toman, stock_qty, active, tags, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, default_items)

    conn.commit()
    cur.close()
    conn.close()

# مراحل ConversationHandler
REG_NAME, REG_PHONE, REG_ADDRESS, REG_POSTAL = range(4)
ORDER_RECEIPT = 4
IMPORT_WAIT_FILE = 5

ITEMS_PER_PAGE = 4

def get_main_keyboard(is_admin=False):
    kb = [
        [KeyboardButton("🛍 محصولات و سفارش"), KeyboardButton("👤 پروفایل من")],
        [KeyboardButton("📋 ثبت / ویرایش اطلاعات"), KeyboardButton("📞 پشتیبانی")]
    ]
    if is_admin:
        kb.append([KeyboardButton("📊 خروجی سفارشات (اکسل)"), KeyboardButton("📦 درون‌ریزی محصولات (اکسل)")])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

# فرمان /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin = (user_id == ADMIN_ID)
    msg = (
        "🌸 به فروشگاه عطر آرمین خوش آمدید 🌸\n\n"
        "برای مشاهده لیست عطرهای خاص ما و ثبت سفارش، از دکمه‌های زیر استفاده کنید."
    )
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(is_admin))

# کاتالوگ پویا محصولات با صفحه‌بندی
async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT sku, product_name, volume_ml, price_toman, stock_qty FROM products WHERE active = TRUE ORDER BY brand, product_name;")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        text = "در حال حاضر هیچ محصول فعالی در انبار ثبت نشده است."
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return

    total_pages = (len(rows) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(0, min(page, total_pages - 1))
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    current_items = rows[start_idx:end_idx]

    text = f"🌸 **کاتالوگ عطرهای اصل آرمین** (صفحه {page + 1} از {total_pages}):\n\n"
    keyboard = []

    for r in current_items:
        sku = r[0] if IS_POSTGRES else r["sku"]
        name = r[1] if IS_POSTGRES else r["product_name"]
        vol = r[2] if IS_POSTGRES else r["volume_ml"]
        price = r[3] if IS_POSTGRES else r["price_toman"]
        stock = r[4] if IS_POSTGRES else r["stock_qty"]
        
        status_stock = f"{stock} عدد در انبار" if stock > 0 else "ناموجود ❌"
        text += f"🔹 **{name}** ({vol}ml)\n"
        text += f"   💰 قیمت: {price:,.0f} تومان | موجودی: {status_stock}\n\n"

        if stock > 0:
            keyboard.append([InlineKeyboardButton(f"🛒 خرید {name} ({vol}ml)", callback_data=f"buy_{sku}")])

    # دکمه‌های ناوبری صفحه‌بندی
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"catpage_{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"catpage_{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

# انتخاب محصول برای خرید
async def handle_buy_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    sku = query.data.replace("buy_", "")

    conn = get_db_connection()
    cur = conn.cursor()
    # بررسی مشخصات محصول
    if IS_POSTGRES:
        cur.execute("SELECT product_name, price_toman, stock_qty FROM products WHERE sku = %s;", (sku,))
    else:
        cur.execute("SELECT product_name, price_toman, stock_qty FROM products WHERE sku = ?;", (sku,))
    prod = cur.fetchone()

    # بررسی ثبت‌نام کاربر
    if IS_POSTGRES:
        cur.execute("SELECT full_name, phone, address FROM users WHERE user_id = %s;", (user_id,))
    else:
        cur.execute("SELECT full_name, phone, address FROM users WHERE user_id = ?;", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not prod:
        await query.edit_message_text("متأسفانه این محصول یافت نشد.")
        return

    prod_name = prod[0] if IS_POSTGRES else prod["product_name"]
    prod_price = prod[1] if IS_POSTGRES else prod["price_toman"]
    prod_stock = prod[2] if IS_POSTGRES else prod["stock_qty"]

    if prod_stock <= 0:
        await query.edit_message_text(f"متأسفانه موجودی «{prod_name}» به اتمام رسیده است.")
        return

    if not user:
        await query.message.reply_text(
            "⚠️ قبل از ثبت سفارش باید اطلاعات تماس و آدرس پستی خود را تکمیل کنید.\n"
            "لطفاً دستور /register را بزنید یا دکمه «📋 ثبت / ویرایش اطلاعات» را لمس کنید."
        )
        return

    context.user_data["current_order"] = {
        "sku": sku,
        "name": prod_name,
        "price": prod_price
    }

    msg = (
        f"سفارش شما: **{prod_name}**\n"
        f"مبلغ قابل پرداخت: **{prod_price:,.0f} تومان**\n\n"
        f"💳 شماره کارت جهت واریز:\n`{CARD_NUMBER}`\n"
        f"{CARD_HOLDER}\n\n"
        "لطفاً پس از واریز، **عکس فیش واریزی** خود را ارسال کنید.\n"
        "(برای انصراف /cancel را بفرستید)"
    )
    await query.message.reply_text(msg, parse_mode="Markdown")
    return ORDER_RECEIPT

# دریافت فیش پرداخت
async def receive_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    order_data = context.user_data.get("current_order")

    if not order_data:
        await update.message.reply_text("خطا در بازیابی اطلاعات سفارش. لطفاً دوباره از منوی محصولات شروع کنید.")
        return ConversationHandler.END

    photo = update.message.photo[-1]
    file_id = photo.file_id
    tehran_tz = pytz.timezone("Asia/Tehran")
    now_tehran = datetime.now(tehran_tz)

    conn = get_db_connection()
    cur = conn.cursor()
    if IS_POSTGRES:
        cur.execute("""
            INSERT INTO orders (user_id, product_sku, product_name, price_toman, receipt_file_id, status, created_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', %s) RETURNING order_id;
        """, (user_id, order_data["sku"], order_data["name"], order_data["price"], file_id, now_tehran))
        order_id = cur.fetchone()[0]
    else:
        cur.execute("""
            INSERT INTO orders (user_id, product_sku, product_name, price_toman, receipt_file_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?);
        """, (user_id, order_data["sku"], order_data["name"], order_data["price"], file_id, now_tehran))
        order_id = cur.lastrowid
    
    # دریافت اطلاعات خریدار
    if IS_POSTGRES:
        cur.execute("SELECT full_name, phone, address, postal_code FROM users WHERE user_id = %s;", (user_id,))
    else:
        cur.execute("SELECT full_name, phone, address, postal_code FROM users WHERE user_id = ?;", (user_id,))
    u = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()

    u_name = u[0] if IS_POSTGRES else u["full_name"]
    u_phone = u[1] if IS_POSTGRES else u["phone"]
    u_addr = u[2] if IS_POSTGRES else u["address"]
    u_post = u[3] if IS_POSTGRES else u["postal_code"]

    await update.message.reply_text(
        f"✅ سفارش شماره #{order_id} با موفقیت ثبت شد و فیش ارسال گردید.\n"
        "پس از بررسی و تایید مدیریت، سفارش شما بسته‌بندی و ارسال خواهد شد."
    )

    # اطلاع به ادمین به همراه دکمه‌های تایید / رد
    admin_caption = (
        f"🚨 **سفارش جدید دریافت شد!** (#{order_id})\n\n"
        f"🛍 محصول: {order_data['name']}\n"
        f"💰 مبلغ: {order_data['price']:,.0f} تومان\n"
        f"👤 مشتری: {u_name} (آیدی: {user_id})\n"
        f"📞 تماس: {u_phone}\n"
        f"📮 کدپستی: {u_post}\n"
        f"📍 آدرس: {u_addr}"
    )
    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید سفارش", callback_data=f"appr_{order_id}"),
            InlineKeyboardButton("❌ رد سفارش", callback_data=f"rejc_{order_id}")
        ]
    ])
    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=file_id,
        caption=admin_caption,
        reply_markup=admin_kb,
        parse_mode="Markdown"
    )

    context.user_data.pop("current_order", None)
    return ConversationHandler.END

# تایید یا رد سفارش توسط ادمین و کسر موجودی انبار
async def handle_order_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    action, order_id = query.data.split("_")
    order_id = int(order_id)

    conn = get_db_connection()
    cur = conn.cursor()

    if action == "appr":
        # خواندن مشخصات سفارش
        if IS_POSTGRES:
            cur.execute("SELECT user_id, product_sku, product_name FROM orders WHERE order_id = %s;", (order_id,))
        else:
            cur.execute("SELECT user_id, product_sku, product_name FROM orders WHERE order_id = ?;", (order_id,))
        ord_info = cur.fetchone()
        
        if ord_info:
            target_user = ord_info[0] if IS_POSTGRES else ord_info["user_id"]
            target_sku = ord_info[1] if IS_POSTGRES else ord_info["product_sku"]
            target_name = ord_info[2] if IS_POSTGRES else ord_info["product_name"]

            # به‌روزرسانی سفارش
            if IS_POSTGRES:
                cur.execute("UPDATE orders SET status = 'approved' WHERE order_id = %s;", (order_id,))
                # کسر موجودی
                cur.execute("UPDATE products SET stock_qty = GREATEST(stock_qty - 1, 0) WHERE sku = %s;", (target_sku,))
            else:
                cur.execute("UPDATE orders SET status = 'approved' WHERE order_id = ?;", (order_id,))
                cur.execute("UPDATE products SET stock_qty = MAX(stock_qty - 1, 0) WHERE sku = ?;", (target_sku,))
            conn.commit()

            await query.edit_message_caption(caption=f"{query.message.caption}\n\n✅ وضعیت: **تایید شد و موجودی انبار کسر گردید**")
            try:
                await context.bot.send_message(
                    chat_id=target_user,
                    text=f"🎉 سفارش #{order_id} ({target_name}) توسط مدیریت تأیید شد و به زودی ارسال می‌شود."
                )
            except Exception as e:
                logger.error(f"Error sending msg to user: {e}")

    elif action == "rejc":
        if IS_POSTGRES:
            cur.execute("UPDATE orders SET status = 'rejected' WHERE order_id = %s RETURNING user_id, product_name;", (order_id,))
        else:
            cur.execute("SELECT user_id, product_name FROM orders WHERE order_id = ?;", (order_id,))
            res = cur.fetchone()
            cur.execute("UPDATE orders SET status = 'rejected' WHERE order_id = ?;", (order_id,))
        
        conn.commit()
        await query.edit_message_caption(caption=f"{query.message.caption}\n\n❌ وضعیت: **رد شد**")

    cur.close()
    conn.close()

# فرآیند ثبت‌نام کاربر
async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "لطفاً **نام و نام‌خانوادگی** کامل خود را وارد کنید:\n(برای لغو /cancel بزنید)"
    )
    return REG_NAME

async def reg_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reg_name"] = update.message.text.strip()
    await update.message.reply_text("شماره تماس همراه خود را وارد کنید (مثال: 09121234567):")
    return REG_PHONE

async def reg_get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reg_phone"] = update.message.text.strip()
    await update.message.reply_text("آدرس دقیق پستی جهت تحویل سفارش را بنویسید (شهر، خیابان، پلاک، زنگ):")
    return REG_ADDRESS

async def reg_get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reg_address"] = update.message.text.strip()
    await update.message.reply_text("کدپستی ۱۰ رقمی خود را وارد کنید:")
    return REG_POSTAL

async def reg_get_postal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    postal_code = update.message.text.strip()
    name = context.user_data["reg_name"]
    phone = context.user_data["reg_phone"]
    addr = context.user_data["reg_address"]
    now = datetime.now(pytz.timezone("Asia/Tehran"))

    conn = get_db_connection()
    cur = conn.cursor()
    if IS_POSTGRES:
        cur.execute("""
            INSERT INTO users (user_id, full_name, phone, address, postal_code, registered_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                full_name = EXCLUDED.full_name,
                phone = EXCLUDED.phone,
                address = EXCLUDED.address,
                postal_code = EXCLUDED.postal_code;
        """, (user_id, name, phone, addr, postal_code, now))
    else:
        cur.execute("""
            INSERT OR REPLACE INTO users (user_id, full_name, phone, address, postal_code, registered_at)
            VALUES (?, ?, ?, ?, ?, ?);
        """, (user_id, name, phone, addr, postal_code, now))
    conn.commit()
    cur.close()
    conn.close()

    await update.message.reply_text(
        "🎉 مشخصات شما با موفقیت ثبت شد!\nاکنون می‌توانید از منوی محصولات خرید خود را انجام دهید.",
        reply_markup=get_main_keyboard(user_id == ADMIN_ID)
    )
    return ConversationHandler.END

# نمایش پروفایل
async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db_connection()
    cur = conn.cursor()
    if IS_POSTGRES:
        cur.execute("SELECT full_name, phone, address, postal_code FROM users WHERE user_id = %s;", (user_id,))
    else:
        cur.execute("SELECT full_name, phone, address, postal_code FROM users WHERE user_id = ?;", (user_id,))
    u = cur.fetchone()
    cur.close()
    conn.close()

    if not u:
        await update.message.reply_text("اطلاعات شما هنوز ثبت نشده است. دستور /register را بزنید.")
        return

    name = u[0] if IS_POSTGRES else u["full_name"]
    phone = u[1] if IS_POSTGRES else u["phone"]
    addr = u[2] if IS_POSTGRES else u["address"]
    post = u[3] if IS_POSTGRES else u["postal_code"]

    msg = (
        f"👤 **مشخصات ثبت‌شده شما:**\n\n"
        f"🏷 نام: {name}\n"
        f"📞 تماس: {phone}\n"
        f"📮 کدپستی: {post}\n"
        f"📍 آدرس: {addr}\n\n"
        "جهت ویرایش مجدد دکمه «📋 ثبت / ویرایش اطلاعات» را لمس کنید."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# درون‌ریزی اکسل محصولات (Import)
async def start_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "📥 لطفاً فایل **products.xlsx** را ارسال فرمایید (به صورت فایل Document).\n"
        "برای انصراف /cancel بزنید."
    )
    return IMPORT_WAIT_FILE

async def receive_products_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    document = update.message.document
    if not (document.file_name.endswith(".xlsx") or document.file_name.endswith(".xls")):
        await update.message.reply_text("⚠️ لطفاً حتماً یک فایل اکسل با پسوند .xlsx بفرستید.")
        return IMPORT_WAIT_FILE

    tg_file = await document.get_file()
    file_bytes = await tg_file.download_as_bytearray()

    try:
        df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0)
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در خواندن فایل اکسل:\n{e}")
        return ConversationHandler.END

    required_cols = {"sku", "product_name", "price_toman", "volume_ml"}
    if not required_cols.issubset(set(df.columns)):
        await update.message.reply_text(f"❌ فایل اکسل باید حداقل شامل ستون‌های زیر باشد:\n{', '.join(required_cols)}")
        return ConversationHandler.END

    inserted = 0
    updated = 0
    errors = 0

    conn = get_db_connection()
    cur = conn.cursor()

    for idx, row in df.iterrows():
        try:
            sku = str(row["sku"]).strip()
            name = str(row["product_name"]).strip()
            if not sku or not name or sku == "nan" or name == "nan":
                errors += 1
                continue

            brand = str(row.get("brand", "")).strip() if pd.notna(row.get("brand")) else ""
            vol = int(row.get("volume_ml", 100))
            price = int(row.get("price_toman", 0))
            stock = int(row.get("stock_qty", 0))
            active = bool(row.get("active", True))
            tags = str(row.get("tags", "")) if pd.notna(row.get("tags")) else ""
            desc = str(row.get("description", "")) if pd.notna(row.get("description")) else ""
            photo = str(row.get("photo_file_id", "")) if pd.notna(row.get("photo_file_id")) else ""

            if IS_POSTGRES:
                cur.execute("""
                    INSERT INTO products (sku, product_name, brand, volume_ml, price_toman, stock_qty, active, tags, description, photo_file_id, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (sku) DO UPDATE SET
                        product_name = EXCLUDED.product_name,
                        brand = EXCLUDED.brand,
                        volume_ml = EXCLUDED.volume_ml,
                        price_toman = EXCLUDED.price_toman,
                        stock_qty = EXCLUDED.stock_qty,
                        active = EXCLUDED.active,
                        tags = EXCLUDED.tags,
                        description = EXCLUDED.description,
                        photo_file_id = EXCLUDED.photo_file_id,
                        updated_at = CURRENT_TIMESTAMP;
                """, (sku, name, brand, vol, price, stock, active, tags, desc, photo))
            else:
                cur.execute("""
                    INSERT INTO products (sku, product_name, brand, volume_ml, price_toman, stock_qty, active, tags, description, photo_file_id, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(sku) DO UPDATE SET
                        product_name = excluded.product_name,
                        brand = excluded.brand,
                        volume_ml = excluded.volume_ml,
                        price_toman = excluded.price_toman,
                        stock_qty = excluded.stock_qty,
                        active = excluded.active,
                        tags = excluded.tags,
                        description = excluded.description,
                        photo_file_id = excluded.photo_file_id,
                        updated_at = CURRENT_TIMESTAMP;
                """, (sku, name, brand, vol, price, stock, active, tags, desc, photo))

            inserted += 1
        except Exception as e:
            logger.error(f"Error on row {idx}: {e}")
            errors += 1

    conn.commit()
    cur.close()
    conn.close()

    await update.message.reply_text(
        f"✅ عملیات با موفقیت پایان یافت!\n\n"
        f"📦 تعداد پردازش و به‌روزرسانی: {inserted}\n"
        f"⚠️ تعداد خطا: {errors}"
    )
    return ConversationHandler.END

# خروجی اکسل محصولات (Export)
async def export_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    conn = get_db_connection()
    query = "SELECT sku, product_name, brand, volume_ml, price_toman, stock_qty, active, tags, description, photo_file_id FROM products ORDER BY brand, product_name;"
    df = pd.read_sql_query(query, conn)
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Products", index=False)
    output.seek(0)

    await update.message.reply_document(
        document=output,
        filename="products_export.xlsx",
        caption="📊 آخرین خروجی دیتابیس محصولات فروشگاه عطر آرمین"
    )

# گزارش سفارشات برای ادمین (/orders)
async def export_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    conn = get_db_connection()
    query = """
        SELECT o.order_id, o.user_id, u.full_name, u.phone, u.postal_code, u.address,
               o.product_name, o.price_toman, o.status, o.created_at
        FROM orders o
        LEFT JOIN users u ON o.user_id = u.user_id
        ORDER BY o.order_id DESC;
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        await update.message.reply_text("هنوز هیچ سفارشی ثبت نشده است.")
        return

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Orders", index=False)
    output.seek(0)

    await update.message.reply_document(
        document=output,
        filename="orders_report.xlsx",
        caption="📋 گزارش کامل سفارشات ثبت‌شده"
    )

# لغو عملیات جاری
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("عملیات لغو شد.", reply_markup=get_main_keyboard(update.effective_user.id == ADMIN_ID))
    return ConversationHandler.END

# کنترل دکمه‌های ناوبری کاتالوگ
async def catalog_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    page = int(query.data.split("_")[1])
    await show_catalog(update, context, page=page)

# راه‌اندازی و اجرای برنامه
def main():
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    # مکالمه ثبت‌نام
    reg_handler = ConversationHandler(
        entry_points=[
            CommandHandler("register", start_registration),
            MessageHandler(filters.Regex("^📋 ثبت / ویرایش اطلاعات$"), start_registration)
        ],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_name)],
            REG_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_phone)],
            REG_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_address)],
            REG_POSTAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_postal)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )

    # مکالمه سفارش و ارسال فیش
    order_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_buy_click, pattern="^buy_")],
        states={
            ORDER_RECEIPT: [MessageHandler(filters.PHOTO, receive_receipt)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )

    # مکالمه ایمپورت اکسل محصولات
    import_handler = ConversationHandler(
        entry_points=[
            CommandHandler("import_products", start_import),
            MessageHandler(filters.Regex("^📦 درون‌ریزی محصولات \(اکسل\)$"), start_import)
        ],
        states={
            IMPORT_WAIT_FILE: [MessageHandler(filters.Document.ALL, receive_products_file)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("catalog", lambda u, c: show_catalog(u, c, 0)))
    app.add_handler(CommandHandler("orders", export_orders))
    app.add_handler(CommandHandler("export_products", export_products))

    app.add_handler(reg_handler)
    app.add_handler(order_handler)
    app.add_handler(import_handler)

    app.add_handler(CallbackQueryHandler(catalog_page_callback, pattern="^catpage_"))
    app.add_handler(CallbackQueryHandler(handle_order_decision, pattern="^(appr|rejc)_"))

    # دکمه‌های منوی اصلی ریپلای
    app.add_handler(MessageHandler(filters.Regex("^🛍 محصولات و سفارش$"), lambda u, c: show_catalog(u, c, 0)))
    app.add_handler(MessageHandler(filters.Regex("^👤 پروفایل من$"), show_profile))
    app.add_handler(MessageHandler(filters.Regex("^📊 خروجی سفارشات \(اکسل\)$"), export_orders))
    app.add_handler(MessageHandler(filters.Regex("^📞 پشتیبانی$"), lambda u, c: u.message.reply_text("جهت پشتیبانی با ادمین در ارتباط باشید: @ArminSupport")))

    # راه‌اندازی Webhook روی Render یا Polling لوکال
    if WEBHOOK_URL:
        logger.info(f"Starting Webhook on port {PORT}...")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
        )
    else:
        logger.info("Starting Polling locally...")
        app.run_polling()

if __name__ == "__main__":
    main()
