# main.py — فروشگاه عطر آرمین 🌸
import os
import io
import logging
from datetime import datetime
import pytz
import pandas as pd
from dotenv import load_dotenv

from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, CallbackQueryHandler, ContextTypes, filters
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "93543054"))
DATABASE_URL = os.getenv("DATABASE_URL")
EXCEL_FILE = os.path.join(os.path.dirname(__file__), "products.xlsx")

TEHRAN = pytz.timezone("Asia/Tehran")
CARD_NUMBER = "6037-9975-1234-5678"
CARD_HOLDER = "بانک ملی - به نام آرمین"
SHOP_NAME = "🌸 فروشگاه عطر آرمین 🌸"
SUPPORT_TEXT = "آیدی پشتیبانی تلگرام: @Armin_Support_Shop\nتلفن تماس: 09120000000"

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ----------------- دیتابیس خودکار -----------------
import sqlite3

def get_conn():
    if DATABASE_URL:
        import psycopg2
        return psycopg2.connect(DATABASE_URL, sslmode="require"), "pg"
    return sqlite3.connect("shop.db", check_same_thread=False), "sqlite"

def db_setup():
    conn, kind = get_conn()
    cur = conn.cursor()
    if kind == "pg":
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                phone TEXT,
                address TEXT,
                joined_at TIMESTAMPTZ DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS orders (
                order_id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT,
                full_name TEXT,
                phone TEXT,
                address TEXT,
                sku TEXT,
                product_name TEXT,
                qty INT DEFAULT 1,
                price_toman BIGINT DEFAULT 0,
                receipt_file_id TEXT,
                status TEXT DEFAULT 'در انتظار تایید پرداخت',
                created_at TIMESTAMPTZ DEFAULT now()
            );
        """)
        user_cols = [
            ("username", "TEXT"),
            ("full_name", "TEXT"),
            ("phone", "TEXT"),
            ("address", "TEXT"),
            ("joined_at", "TIMESTAMPTZ DEFAULT now()")
        ]
        for col, col_type in user_cols:
            cur.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {col_type};")

        order_cols = [
            ("username", "TEXT"),
            ("full_name", "TEXT"),
            ("phone", "TEXT"),
            ("address", "TEXT"),
            ("sku", "TEXT"),
            ("product_name", "TEXT"),
            ("qty", "INT DEFAULT 1"),
            ("price_toman", "BIGINT DEFAULT 0"),
            ("receipt_file_id", "TEXT"),
            ("status", "TEXT DEFAULT 'در انتظار تایید پرداخت'"),
            ("created_at", "TIMESTAMPTZ DEFAULT now()")
        ]
        for col, col_type in order_cols:
            cur.execute(f"ALTER TABLE orders ADD COLUMN IF NOT EXISTS {col} {col_type};")
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                phone TEXT,
                address TEXT,
                joined_at TEXT
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                phone TEXT,
                address TEXT,
                sku TEXT,
                product_name TEXT,
                qty INTEGER DEFAULT 1,
                price_toman INTEGER DEFAULT 0,
                receipt_file_id TEXT,
                status TEXT DEFAULT 'در انتظار تایید پرداخت',
                created_at TEXT
            );
        """)
    conn.commit()
    cur.close()
    conn.close()

def get_user_profile(user_id):
    conn, kind = get_conn()
    cur = conn.cursor()
    q = "SELECT user_id, username, full_name, phone, address FROM users WHERE user_id = %s" if kind == "pg" else "SELECT user_id, username, full_name, phone, address FROM users WHERE user_id = ?"
    cur.execute(q, (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {"user_id": row[0], "username": row[1], "full_name": row[2], "phone": row[3], "address": row[4]}
    return None

def upsert_user(user_id, username, full_name=None, phone=None, address=None):
    conn, kind = get_conn()
    cur = conn.cursor()
    exist = get_user_profile(user_id)
    if not exist:
        if kind == "pg":
            cur.execute(
                "INSERT INTO users (user_id, username, full_name, phone, address) VALUES (%s, %s, %s, %s, %s);",
                (user_id, str(username or ""), full_name, phone, address)
            )
        else:
            cur.execute(
                "INSERT INTO users (user_id, username, full_name, phone, address, joined_at) VALUES (?, ?, ?, ?, ?, ?);",
                (user_id, str(username or ""), full_name, phone, address, datetime.now(TEHRAN).isoformat())
            )
    else:
        new_name = full_name if full_name is not None else exist["full_name"]
        new_phone = phone if phone is not None else exist["phone"]
        new_addr = address if address is not None else exist["address"]
        if kind == "pg":
            cur.execute(
                "UPDATE users SET username=%s, full_name=%s, phone=%s, address=%s WHERE user_id=%s;",
                (str(username or ""), new_name, new_phone, new_addr, user_id)
            )
        else:
            cur.execute(
                "UPDATE users SET username=?, full_name=?, phone=?, address=? WHERE user_id=?;",
                (str(username or ""), new_name, new_phone, new_addr, user_id)
            )
    conn.commit()
    cur.close()
    conn.close()

def record_order(user_id, username, full_name, phone, address, sku, product_name, qty, price_toman, receipt_file_id):
    conn, kind = get_conn()
    cur = conn.cursor()
    safe_price = int(float(price_toman or 0))
    if kind == "pg":
        cur.execute(
            """INSERT INTO orders (user_id, username, full_name, phone, address, sku, product_name, qty, price_toman, receipt_file_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING order_id;""",
            (user_id, str(username or ""), full_name, phone, address, str(sku), str(product_name or ""), qty, safe_price, receipt_file_id)
        )
        order_id = cur.fetchone()[0]
    else:
        cur.execute(
            """INSERT INTO orders (user_id, username, full_name, phone, address, sku, product_name, qty, price_toman, receipt_file_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            (user_id, str(username or ""), full_name, phone, address, str(sku), str(product_name or ""), qty, safe_price, receipt_file_id, datetime.now(TEHRAN).isoformat())
        )
        order_id = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    return order_id

def get_order_by_id(order_id):
    conn, kind = get_conn()
    cur = conn.cursor()
    q = "SELECT order_id, user_id, product_name, qty, price_toman, status FROM orders WHERE order_id = %s" if kind == "pg" else "SELECT order_id, user_id, product_name, qty, price_toman, status FROM orders WHERE order_id = ?"
    cur.execute(q, (order_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        return {"order_id": row[0], "user_id": row[1], "product_name": row[2], "qty": row[3], "price_toman": row[4], "status": row[5]}
    return None

def update_order_status(order_id, new_status):
    conn, kind = get_conn()
    cur = conn.cursor()
    q = "UPDATE orders SET status = %s WHERE order_id = %s" if kind == "pg" else "UPDATE orders SET status = ? WHERE order_id = ?"
    cur.execute(q, (new_status, order_id))
    conn.commit()
    cur.close()
    conn.close()

# ----------------- اکسل محصولات -----------------
def get_products():
    if not os.path.exists(EXCEL_FILE):
        return []
    try:
        df = pd.read_excel(EXCEL_FILE)
        df.columns = [str(c).strip().lower() for c in df.columns]
        for c in ["sku", "product_name", "brand", "volume_ml", "price_toman", "stock_qty", "active", "tags", "description", "photo_file_id"]:
            if c not in df.columns:
                df[c] = None
        df = df[df["active"].astype(str).str.lower().isin(["true", "1", "yes", "فعال", "بله"])]
        df = df.dropna(subset=["sku", "product_name"])
        return df.to_dict("records")
    except Exception as e:
        logging.error(f"Error reading products.xlsx: {e}")
        return []

def get_product(sku):
    for p in get_products():
        if str(p.get("sku")).strip() == str(sku).strip():
            return p
    return None

def fmt_price(v):
    try:
        return f"{int(float(v)):,} تومان"
    except:
        return "نامشخص"

# ----------------- کیبوردها -----------------
def main_kb(user_id: int):
    menu = [
        ["🛍 مشاهده محصولات", "🧾 پیگیری سفارشات من"],
        ["👤 پروفایل من", "💳 شماره کارت فروشگاه"],
        ["📞 ارتباط با پشتیبانی"]
    ]
    if int(user_id) == ADMIN_ID:
        menu.append(["📊 خروجی اکسل سفارشات", "📥 بروزرسانی فایل محصولات"])
        menu.append(["👥 لیست کاربران", "🖼 دریافت شناسه عکس محصول"])
    return ReplyKeyboardMarkup(menu, resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup([["❌ انصراف"]], resize_keyboard=True)

def profile_inline_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ثبت نام / ویرایش اطلاعات", callback_data="btn_edit_profile")],
        [InlineKeyboardButton("🛍 مشاهده و خرید محصولات", callback_data="btn_view_products")]
    ])

def admin_order_actions_kb(order_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید پرداخت و ارسال", callback_data=f"ord_approve:{order_id}"),
            InlineKeyboardButton("❌ رد سفارش (نامعتبر)", callback_data=f"ord_reject:{order_id}")
        ]
    ])

# ----------------- نمایش پروفایل کاربر -----------------
async def show_profile_card(target_send_func, uid: int):
    prof = get_user_profile(uid)
    fname = (prof and prof.get("full_name")) or "ثبت نشده ❌"
    phone = (prof and prof.get("phone")) or "ثبت نشده ❌"
    addr = (prof and prof.get("address")) or "ثبت نشده ❌"

    is_complete = bool(prof and prof.get("full_name") and prof.get("phone") and prof.get("address"))
    status_icon = "✅ تکمیل شده" if is_complete else "⚠️ ناقص (نیاز به تکمیل)"

    msg = (
        "👤 **پروفایل و اطلاعات حساب شما:**\n\n"
        f"🏷 نام و نام خانوادگی: `{fname}`\n"
        f"📞 شماره تماس: `{phone}`\n"
        f"📍 آدرس ارسال: `{addr}`\n"
        f"وضعیت حساب: **{status_icon}**\n\n"
        "👇 برای ثبت یا ویرایش اطلاعات روی دکمه شیشه‌ای زیر بزنید:"
    )
    await target_send_func(msg, reply_markup=profile_inline_kb(), parse_mode="Markdown")

# ----------------- هندلرهای دستورات متنی منو -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    uname = update.effective_user.username
    upsert_user(uid, uname)
    role_msg = " (پنل مدیریت فعال)" if uid == ADMIN_ID else ""
    await update.message.reply_text(
        f"سلام خوش آمدید به {SHOP_NAME}{role_msg} 🌸\nلطفاً یک گزینه را انتخاب کنید:",
        reply_markup=main_kb(uid)
    )

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_profile_card(update.message.reply_text, update.effective_user.id)

async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    conn, kind = get_conn()
    cur = conn.cursor()
    q = (
        "SELECT order_id, product_name, qty, price_toman, status FROM orders WHERE user_id = %s ORDER BY order_id DESC LIMIT 5;"
        if kind == "pg" else
        "SELECT order_id, product_name, qty, price_toman, status FROM orders WHERE user_id = ? ORDER BY order_id DESC LIMIT 5;"
    )
    cur.execute(q, (uid,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        await update.message.reply_text("شما هنوز هیچ سفارشی ثبت نکرده‌اید.")
        return

    msg = "📋 **سفارشات اخیر شما:**\n\n"
    for r in rows:
        msg += f"🔹 سفارش #{r[0]}: {r[1]} ({r[2]} عدد) - {fmt_price(r[3])}\nوضعیت: **{r[4]}**\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    conn, kind = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, full_name, phone FROM users ORDER BY user_id DESC LIMIT 15;")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        await update.message.reply_text("هیچ کاربری در سیستم ثبت نشده است.")
        return

    msg = f"👥 **لیست ۱۵ کاربر اخیر:**\n\n"
    for u in rows:
        msg += f"• `{u[0]}` | @{u[1] or '—'} | {u[2] or 'بدون نام'} | `{u[3] or 'بدون شماره'}`\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

# ----------------- مکالمه پروفایل -----------------
PROF_NAME, PROF_PHONE, PROF_ADDR = range(10, 13)

async def profile_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # این متد هم از دکمه شیشه‌ای و هم از پیام متنی فراخوانی می‌شود
    if update.callback_query:
        await update.callback_query.answer()
        target = update.callback_query.message.reply_text
    else:
        target = update.message.reply_text

    await target("لطفاً **نام و نام خانوادگی** خود را وارد فرمایید:", reply_markup=cancel_kb(), parse_mode="Markdown")
    return PROF_NAME

async def profile_edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["prof_name"] = update.message.text.strip()
    await update.message.reply_text("لطفاً **شماره موبایل** خود را بفرستید (مثال: 09121234567):", reply_markup=cancel_kb())
    return PROF_PHONE

async def profile_edit_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["prof_phone"] = update.message.text.strip()
    await update.message.reply_text("لطفاً **آدرس پستی دقیق همراه با کدپستی** را ارسال کنید:", reply_markup=cancel_kb())
    return PROF_ADDR

async def profile_edit_addr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    addr = update.message.text.strip()
    uid = update.effective_user.id
    upsert_user(
        uid,
        update.effective_user.username,
        full_name=context.user_data.get("prof_name"),
        phone=context.user_data.get("prof_phone"),
        address=addr
    )
    context.user_data.clear()
    await update.message.reply_text(
        "✅ مشخصات شما با موفقیت ذخیره شد!\nاکنون می‌توانید به راحتی خرید خود را نهایی کنید.",
        reply_markup=main_kb(uid)
    )
    return ConversationHandler.END

# ----------------- مکالمه خرید و فیش -----------------
BUY_QTY, BUY_RECEIPT = range(20, 22)

async def buy_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    prof = get_user_profile(uid)

    if not prof or not prof.get("full_name") or not prof.get("phone") or not prof.get("address"):
        inline_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ تکمیل پروفایل و آدرس", callback_data="btn_edit_profile")]
        ])
        await update.message.reply_text(
            "⚠️ قبل از ثبت سفارش باید اطلاعات تماس و آدرس خود را ثبت کنید.\n"
            "روی دکمه شیشه‌ای زیر بزنید:",
            reply_markup=inline_kb
        )
        return ConversationHandler.END

    sku = update.message.text.replace("/buy_", "").strip()
    p = get_product(sku)
    if not p:
        await update.message.reply_text("عطر انتخاب شده یافت نشد.", reply_markup=main_kb(uid))
        return ConversationHandler.END

    context.user_data["buy_p"] = p
    await update.message.reply_text(
        f"🌸 انتخاب شما: {p.get('product_name')}\n"
        f"💰 قیمت واحد: {fmt_price(p.get('price_toman'))}\n\n"
        "تعداد درخواستی خود را فقط با رقم (عدد انگلیسی یا فارسی) ارسال کنید:",
        reply_markup=cancel_kb()
    )
    return BUY_QTY

async def buy_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    for fa, en in zip("۰۱۲۳۴۵۶۷۸۹", "0123456789"):
        raw = raw.replace(fa, en)

    if not raw.isdigit() or int(raw) <= 0:
        await update.message.reply_text("لطفاً یک عدد معتبر ارسال کنید:")
        return BUY_QTY

    qty = int(raw)
    p = context.user_data["buy_p"]
    price = int(float(p.get("price_toman") or 0))
    total = price * qty
    context.user_data["buy_qty"] = qty
    context.user_data["buy_total"] = total

    msg = (
        f"🧾 **پیش‌فاکتور سفارش:**\n\n"
        f"🌸 محصول: {p.get('product_name')}\n"
        f"📦 تعداد: {qty} عدد\n"
        f"💳 کل مبلغ پرداختی: **{fmt_price(total)}**\n\n"
        f"شماره کارت فروشگاه:\n`{CARD_NUMBER}`\n{CARD_HOLDER}\n\n"
        "📸 **مرحله آخر:** لطفاً عکس فیش واریز را همین‌جا ارسال نمایید:"
    )
    await update.message.reply_text(msg, reply_markup=cancel_kb(), parse_mode="Markdown")
    return BUY_RECEIPT

async def buy_receipt_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not update.message.photo:
        await update.message.reply_text("لطفاً عکس فیش را بفرستید، یا دکمه «❌ انصراف» را بزنید:")
        return BUY_RECEIPT

    receipt_file_id = update.message.photo[-1].file_id
    p = context.user_data["buy_p"]
    qty = context.user_data["buy_qty"]
    total = context.user_data["buy_total"]
    prof = get_user_profile(uid)

    order_id = record_order(
        uid,
        update.effective_user.username,
        prof.get("full_name"),
        prof.get("phone"),
        prof.get("address"),
        p["sku"],
        p.get("product_name"),
        qty,
        total,
        receipt_file_id
    )

    await update.message.reply_text(
        f"✅ سفارش شما با کد پیگیری #{order_id} ثبت شد!\n"
        "فیش واریزی برای مدیریت ارسال گردید. به محض تایید به شما اطلاع می‌دهیم.",
        reply_markup=main_kb(uid)
    )

    admin_caption = (
        f"🚨 **سفارش جدید دریافت شد! (# {order_id})**\n\n"
        f"👤 مشتری: {prof.get('full_name')} (@{update.effective_user.username or 'ندارد'})\n"
        f"📞 تماس: `{prof.get('phone')}`\n"
        f"📍 آدرس: {prof.get('address')}\n\n"
        f"🌸 عطر: {p.get('product_name')}\n"
        f"📦 تعداد: {qty} عدد\n"
        f"💰 مبلغ واریز: {fmt_price(total)}\n\n"
        "👇 وضعیت بررسی را تعیین کنید:"
    )
    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=receipt_file_id,
            caption=admin_caption,
            reply_markup=admin_order_actions_kb(order_id),
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Error forwarding receipt to admin: {e}")

    context.user_data.clear()
    return ConversationHandler.END

async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ عملیات لغو گردید.", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# ----------------- دکمه‌های تایید / رد ادمین -----------------
async def handle_admin_order_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        return

    data = query.data or ""
    action, order_id_str = data.split(":")
    order_id = int(order_id_str)
    order = get_order_by_id(order_id)

    if not order:
        await query.edit_message_caption(caption="❌ سفارش مورد نظر یافت نشد.")
        return

    buyer_id = order["user_id"]
    current_caption = query.message.caption or ""

    if action == "ord_approve":
        update_order_status(order_id, "تایید شده و در حال ارسال 📦")
        new_caption = current_caption + "\n\n━━━━━━━━━━━━━━━\n✅ **وضعیت: این سفارش توسط ادمین تایید شد و به زودی ارسال می‌شود.**"
        await query.edit_message_caption(caption=new_caption, reply_markup=None, parse_mode="Markdown")

        try:
            await context.bot.send_message(
                chat_id=buyer_id,
                text=(
                    f"🎉 **خبر خوب! سفارش #{order_id} تایید شد.**\n\n"
                    f"🌸 محصول: {order['product_name']} ({order['qty']} عدد)\n"
                    "پرداخت شما تایید شد و بسته در نوبت ارسال پستی قرار گرفت.\n"
                    "با تشکر از خرید شما از فروشگاه عطر آرمین 🌸"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logging.error(f"Error notifying buyer {buyer_id}: {e}")

    elif action == "ord_reject":
        update_order_status(order_id, "رد شده / نامعتبر ❌")
        new_caption = current_caption + "\n\n━━━━━━━━━━━━━━━\n❌ **وضعیت: فیش واریزی این سفارش رد شد.**"
        await query.edit_message_caption(caption=new_caption, reply_markup=None, parse_mode="Markdown")

        try:
            await context.bot.send_message(
                chat_id=buyer_id,
                text=(
                    f"⚠️ **سفارش #{order_id} تایید نشد.**\n\n"
                    "فیش واریزی یا اطلاعات پرداخت ارسال‌شده مورد تایید قرار نگرفت.\n"
                    f"در صورت نیاز به پیگیری، لطفاً با پشتیبانی در ارتباط باشید:\n{SUPPORT_TEXT}"
                )
            )
        except Exception as e:
            logging.error(f"Error notifying buyer {buyer_id}: {e}")

# ----------------- بخش اکسل ادمین (حل قطعی باگ خروجی) -----------------
async def admin_export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        conn, _ = get_conn()
        q = "SELECT order_id, user_id, username, full_name, phone, address, sku, product_name, qty, price_toman, status, created_at FROM orders ORDER BY order_id DESC;"
        df = pd.read_sql_query(q, conn)
        conn.close()

        if df.empty:
            await update.message.reply_text("هیچ سفارشی در دیتابیس ثبت نشده است.")
            return

        # تبدیل ستون تاریخ به رشته ساده بدون timezone تا باگ openpyxl رخ ندهد
        if "created_at" in df.columns:
            df["created_at"] = df["created_at"].astype(str)

        buf = io.BytesIO()
        df.to_excel(buf, index=False, engine="openpyxl")
        buf.seek(0)
        file_bytes = buf.getvalue()

        await context.bot.send_document(
            chat_id=ADMIN_ID,
            document=file_bytes,
            filename=f"orders_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            caption="📊 **گزارش کلیه سفارشات ثبت‌شده در فروشگاه عطر آرمین**",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Excel export error: {e}")
        await update.message.reply_text(f"❌ خطا در ساخت اکسل: {e}")

ADMIN_EXCEL_WAIT = 30
ADMIN_PHOTO_WAIT = 31

async def admin_import_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text(
        "لطفاً فایل جدید اکسل محصولات (`products.xlsx`) را به صورت فایل ارسال کنید:",
        reply_markup=cancel_kb()
    )
    return ADMIN_EXCEL_WAIT

async def admin_import_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name.endswith((".xlsx", ".xls")):
        await update.message.reply_text("لطفاً یک فایل معتبر اکسل (.xlsx) ارسال فرمایید:")
        return ADMIN_EXCEL_WAIT

    file_obj = await context.bot.get_file(doc.file_id)
    await file_obj.download_to_drive(EXCEL_FILE)
    await update.message.reply_text("✅ لیست محصولات با موفقیت بروز شد!", reply_markup=main_kb(ADMIN_ID))
    return ConversationHandler.END

async def admin_photo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text(
        "عکس مورد نظر محصول را ارسال فرمایید تا شناسه `photo_file_id` تولید شود:",
        reply_markup=cancel_kb()
    )
    return ADMIN_PHOTO_WAIT

async def admin_photo_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fid = update.message.photo[-1].file_id
    await update.message.reply_text(
        f"🖼 شناسه عکس:\n\n`{fid}`\n\nاین مقدار را در ستون `photo_file_id` فایل اکسل قرار دهید.",
        parse_mode="Markdown",
        reply_markup=main_kb(ADMIN_ID)
    )
    return ConversationHandler.END

# ----------------- دکمه‌ها و کالبک‌های عمومی -----------------
async def show_all_products(message_obj):
    prods = get_products()
    if not prods:
        await message_obj.reply_text("هیچ عطری در سیستم یافت نشد.")
        return
    for p in prods:
        caption = (
            f"🌸 {p.get('product_name')}\n"
            f"🏷 برند: {p.get('brand') or '—'}\n"
            f"💧 حجم: {p.get('volume_ml') or '—'} میل\n"
            f"💰 قیمت: {fmt_price(p.get('price_toman'))}\n"
            f"📦 موجودی: {p.get('stock_qty') or 0} عدد\n"
            f"📝 توضیحات: {p.get('description') or '—'}\n\n"
            f"خرید این عطر: /buy_{p.get('sku')}"
        )
        photo = str(p.get("photo_file_id") or "").strip()
        if photo and photo.lower() not in ["none", "nan"]:
            try:
                await message_obj.reply_photo(photo, caption=caption)
                continue
            except:
                pass
        await message_obj.reply_text(caption)

async def inline_btn_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "btn_view_products":
        await show_all_products(query.message)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    uid = update.effective_user.id

    if txt == "🛍 مشاهده محصولات":
        await show_all_products(update.message)
    elif txt == "🧾 پیگیری سفارشات من":
        await cmd_orders(update, context)
    elif txt == "👤 پروفایل من":
        await show_profile_card(update.message.reply_text, uid)
    elif txt == "🔙 بازگشت به منوی اصلی":
        await update.message.reply_text("منوی اصلی:", reply_markup=main_kb(uid))
    elif txt == "💳 شماره کارت فروشگاه":
        await update.message.reply_text(
            f"💳 **اطلاعات کارت جهت واریز وجه:**\n\n`{CARD_NUMBER}`\n{CARD_HOLDER}",
            parse_mode="Markdown"
        )
    elif txt == "📞 ارتباط با پشتیبانی":
        await update.message.reply_text(f"📞 **پشتیبانی فروشگاه عطر آرمین:**\n\n{SUPPORT_TEXT}")
    elif txt == "📊 خروجی اکسل سفارشات":
        await admin_export_excel(update, context)
    elif txt == "👥 لیست کاربران":
        await cmd_users(update, context)

def main():
    db_setup()
    app = Application.builder().token(BOT_TOKEN).build()

    # مکالمه ثبت‌نام / ویرایش پروفایل با پشتیبانی از دکمه شیشه‌ای و متنی
    prof_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(profile_edit_start, pattern=r"^btn_edit_profile$"),
            CommandHandler("register", profile_edit_start),
            MessageHandler(filters.Regex(r"^✏️ ویرایش مشخصات$"), profile_edit_start)
        ],
        states={
            PROF_NAME: [MessageHandler(filters.TEXT & ~filters.Regex(r"^❌ انصراف$"), profile_edit_name)],
            PROF_PHONE: [MessageHandler(filters.TEXT & ~filters.Regex(r"^❌ انصراف$"), profile_edit_phone)],
            PROF_ADDR: [MessageHandler(filters.TEXT & ~filters.Regex(r"^❌ انصراف$"), profile_edit_addr)],
        },
        fallbacks=[MessageHandler(filters.Regex(r"^❌ انصراف$"), cancel_flow)]
    )

    buy_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^/buy_"), buy_start)],
        states={
            BUY_QTY: [MessageHandler(filters.TEXT & ~filters.Regex(r"^❌ انصراف$"), buy_qty)],
            BUY_RECEIPT: [MessageHandler(filters.PHOTO, buy_receipt_step)],
        },
        fallbacks=[MessageHandler(filters.Regex(r"^❌ انصراف$"), cancel_flow)]
    )

    excel_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^📥 بروزرسانی فایل محصولات$"), admin_import_start)],
        states={
            ADMIN_EXCEL_WAIT: [MessageHandler(filters.Document.ALL & ~filters.Regex(r"^❌ انصراف$"), admin_import_file)]
        },
        fallbacks=[MessageHandler(filters.Regex(r"^❌ انصراف$"), cancel_flow)]
    )

    photo_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^🖼 دریافت شناسه عکس محصول$"), admin_photo_start)],
        states={
            ADMIN_PHOTO_WAIT: [MessageHandler(filters.PHOTO & ~filters.Regex(r"^❌ انصراف$"), admin_photo_receive)]
        },
        fallbacks=[MessageHandler(filters.Regex(r"^❌ انصراف$"), cancel_flow)]
    )

    # دستورات اسلش تلگرام
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("export", admin_export_excel))

    # مکالمات
    app.add_handler(prof_conv)
    app.add_handler(buy_conv)
    app.add_handler(excel_conv)
    app.add_handler(photo_conv)

    # کالبک‌های اینلاین
    app.add_handler(CallbackQueryHandler(handle_admin_order_decision, pattern=r"^ord_"))
    app.add_handler(CallbackQueryHandler(inline_btn_router, pattern=r"^btn_"))

    # پیام‌های متنی
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    port = int(os.getenv("PORT", "10000"))
    if os.getenv("RENDER"):
        host = os.getenv("RENDER_EXTERNAL_HOST") or "my-telegram-bot-8k9h.onrender.com"
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="telegram",
            webhook_url=f"https://{host}/telegram"
        )
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
