import os
import datetime
import sqlite3
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

# ------------------ تنظیمات ------------------
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", "8443"))

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

ADMIN_ID = 93543054
CARD_NUMBER = "6037-9975-1234-5678"
CARD_HOLDER = "بانک ملی - به نام آرمان"

PERFUMES = {
    "dior": {"name": "عطر Dior Sauvage", "price": "2,500,000 تومان"},
    "bleu": {"name": "عطر Bleu de Chanel", "price": "3,200,000 تومان"},
    "ysl": {"name": "عطر YSL L'Homme", "price": "2,800,000 تومان"},
}

# وضعیت‌های گفتگو
REG_NAME, REG_PHONE, REG_ADDRESS = range(3)
ORDER_ADDRESS, ORDER_PHONE, WAIT_RECEIPT = range(3, 6)


# ------------------ دیتابیس ------------------
def get_db_connection():
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect("shop.db")


def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    if DATABASE_URL:
        # پاک کردن جدول‌های قدیمی برای اعمال ساختار جدید (فقط این نسخه یک بار اجرا می‌شود)
        c.execute("DROP TABLE IF EXISTS orders CASCADE;")
        c.execute("DROP TABLE IF EXISTS users CASCADE;")
        c.execute("""
            CREATE TABLE users (
                user_id BIGINT PRIMARY KEY,
                name TEXT,
                phone TEXT,
                address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        c.execute("""
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY,
                user_id BIGINT,
                name TEXT,
                phone TEXT,
                address TEXT,
                perfume TEXT,
                price TEXT,
                receipt_file_id TEXT,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    else:
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                phone TEXT,
                address TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT,
                phone TEXT,
                address TEXT,
                perfume TEXT,
                price TEXT,
                receipt_file_id TEXT,
                status TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.commit()
    conn.close()


def get_user_from_db(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    if DATABASE_URL:
        c.execute("SELECT name, phone, address FROM users WHERE user_id = %s;", (user_id,))
    else:
        c.execute("SELECT name, phone, address FROM users WHERE user_id = ?;", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"name": row[0], "phone": row[1], "address": row[2]}
    return None


def get_next_order_id():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT MAX(order_id) FROM orders;")
    row = c.fetchone()
    conn.close()
    if row and row[0] is not None:
        return row[0] + 1
    return 1001


# ------------------ دستورات عمومی ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_keyboard = [["🛍 خرید عطر", "👤 ثبت‌نام"]]
    markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "سلام! به 🌸 فروشگاه عطر آرمان 🌸 خوش آمدید.\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=markup,
    )


# ------------------ فرآیند ثبت‌نام مشتری ------------------
async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "لطفاً نام و نام خانوادگی خود را ارسال کنید:\n(برای انصراف /cancel را بزنید)",
        reply_markup=ReplyKeyboardRemove(),
    )
    return REG_NAME


async def reg_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reg_name"] = update.message.text.strip()
    await update.message.reply_text("لطفاً شماره تماس خود را وارد کنید (مثال: 09123456789):")
    return REG_PHONE


async def reg_get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not (phone.isdigit() and len(phone) >= 10):
        await update.message.reply_text("❌ شماره نامعتبر است. لطفاً یک شماره موبایل معتبر وارد کنید:")
        return REG_PHONE
    context.user_data["reg_phone"] = phone
    await update.message.reply_text("لطفاً آدرس دقیق پستی و کد پستی خود را وارد کنید:")
    return REG_ADDRESS


async def reg_get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reg_address"] = update.message.text.strip()
    user_id = update.effective_user.id
    name = context.user_data["reg_name"]
    phone = context.user_data["reg_phone"]
    address = context.user_data["reg_address"]

    conn = get_db_connection()
    c = conn.cursor()
    if DATABASE_URL:
        c.execute("""
            INSERT INTO users (user_id, name, phone, address)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                name = EXCLUDED.name,
                phone = EXCLUDED.phone,
                address = EXCLUDED.address;
        """, (user_id, name, phone, address))
    else:
        c.execute("""
            INSERT OR REPLACE INTO users (user_id, name, phone, address)
            VALUES (?, ?, ?, ?);
        """, (user_id, name, phone, address))
    conn.commit()
    conn.close()

    reply_keyboard = [["🛍 خرید عطر", "👤 ثبت‌نام"]]
    markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "✅ اطلاعات شما با موفقیت ذخیره شد.\nاکنون می‌توانید از بخش «🛍 خرید عطر» سفارش خود را ثبت کنید!",
        reply_markup=markup,
    )
    return ConversationHandler.END


# ------------------ فرآیند خرید ------------------
async def catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Dior Sauvage - ۲,۵۰۰,۰۰۰ تومان", callback_data="perfume_dior")],
        [InlineKeyboardButton("Bleu de Chanel - ۳,۲۰۰,۰۰۰ تومان", callback_data="perfume_bleu")],
        [InlineKeyboardButton("YSL L'Homme - ۲,۸۰۰,۰۰۰ تومان", callback_data="perfume_ysl")],
    ]
    await update.message.reply_text(
        "🌟 لیست عطرهای موجود:\nیکی از گزینه‌ها را برای سفارش انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def choose_perfume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    perfume_key = query.data.replace("perfume_", "")
    item = PERFUMES.get(perfume_key)
    if not item:
        await query.edit_message_text("خطا در انتخاب محصول.")
        return ConversationHandler.END

    context.user_data["perfume_name"] = item["name"]
    context.user_data["perfume_price"] = item["price"]

    user_info = get_user_from_db(update.effective_user.id)
    if user_info:
        context.user_data["order_name"] = user_info["name"]
        context.user_data["order_phone"] = user_info["phone"]
        context.user_data["order_address"] = user_info["address"]

        invoice_text = (
            f"🧾 پیش‌فاکتور شما:\n"
            f"🏷 محصول: {item['name']}\n"
            f"💰 مبلغ: {item['price']}\n"
            f"👤 تحویل‌گیرنده: {user_info['name']}\n"
            f"📞 تلفن: {user_info['phone']}\n"
            f"📍 آدرس ارسال: {user_info['address']}\n\n"
            f"💳 لطفاً مبلغ را به شماره کارت زیر واریز کنید:\n"
            f"شماره کارت: `{CARD_NUMBER}`\n"
            f"صاحب حساب: {CARD_HOLDER}\n\n"
            f"📸 پس از پرداخت، عکس فیش واریزی را همینجا ارسال فرمایید:"
        )
        await query.edit_message_text(invoice_text, parse_mode="Markdown")
        return WAIT_RECEIPT
    else:
        await query.edit_message_text(
            f"محصول انتخابی: {item['name']}\nقیمت: {item['price']}\n\n"
            "لطفاً آدرس پستی دقیق جهت ارسال را وارد فرمایید:\n(یا ابتدا دکمه ثبت‌نام را بزنید)",
        )
        return ORDER_ADDRESS


async def order_get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order_address"] = update.message.text.strip()
    await update.message.reply_text("لطفاً شماره تماس در دسترس خود را وارد فرمایید:")
    return ORDER_PHONE


async def order_get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not (phone.isdigit() and len(phone) >= 10):
        await update.message.reply_text("❌ شماره نامعتبر است. لطفاً شماره همراه معتبر وارد کنید:")
        return ORDER_PHONE

    context.user_data["order_phone"] = phone
    context.user_data["order_name"] = update.effective_user.full_name

    perfume = context.user_data["perfume_name"]
    price = context.user_data["perfume_price"]

    invoice_text = (
        f"🧾 پیش‌فاکتور شما:\n"
        f"🏷 محصول: {perfume}\n"
        f"💰 مبلغ: {price}\n"
        f"📍 آدرس: {context.user_data['order_address']}\n"
        f"📞 تلفن: {phone}\n\n"
        f"💳 شماره کارت جهت واریز:\n`{CARD_NUMBER}`\n{CARD_HOLDER}\n\n"
        f"📸 لطفاً تصویر فیش واریزی را ارسال کنید:"
    )
    await update.message.reply_text(invoice_text, parse_mode="Markdown")
    return WAIT_RECEIPT


async def order_get_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("لطفاً عکس فیش را به صورت تصویر (Photo) ارسال کنید:")
        return WAIT_RECEIPT

    photo_file_id = update.message.photo[-1].file_id
    order_id = get_next_order_id()
    user_id = update.effective_user.id
    name = context.user_data.get("order_name", update.effective_user.full_name)
    phone = context.user_data.get("order_phone", "-")
    address = context.user_data.get("order_address", "-")
    perfume = context.user_data.get("perfume_name", "-")
    price = context.user_data.get("perfume_price", "-")

    conn = get_db_connection()
    c = conn.cursor()
    if DATABASE_URL:
        c.execute("""
            INSERT INTO orders (order_id, user_id, name, phone, address, perfume, price, receipt_file_id, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'در انتظار تایید');
        """, (order_id, user_id, name, phone, address, perfume, price, photo_file_id))
    else:
        c.execute("""
            INSERT INTO orders (order_id, user_id, name, phone, address, perfume, price, receipt_file_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'در انتظار تایید');
        """, (order_id, user_id, name, phone, address, perfume, price, photo_file_id))
    conn.commit()
    conn.close()

    # پیام تایید برای مشتری
    reply_keyboard = [["🛍 خرید عطر", "👤 ثبت‌نام"]]
    markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
    await update.message.reply_text(
        f"✅ سفارش شما ثبت شد!\n🔖 کد پیگیری: {order_id}\nپس از بررسی فیش توسط ادمین، نتیجه به شما اطلاع داده خواهد شد. متشکریم 🌹",
        reply_markup=markup,
    )

    # ارسال فیش برای ادمین
    admin_caption = (
        f"📦 سفارش جدید #{order_id}\n"
        f"👤 مشتری: {name} (شناسه: {user_id})\n"
        f"📞 تلفن: {phone}\n"
        f"📍 آدرس: {address}\n"
        f"🏷 محصول: {perfume}\n"
        f"💰 مبلغ: {price}"
    )
    admin_keyboard = [
        [
            InlineKeyboardButton("✅ تایید", callback_data=f"order_approve_{order_id}"),
            InlineKeyboardButton("❌ رد", callback_data=f"order_reject_{order_id}"),
        ]
    ]
    try:
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_file_id,
            caption=admin_caption,
            reply_markup=InlineKeyboardMarkup(admin_keyboard),
        )
    except Exception as e:
        print(f"Error sending receipt to admin: {e}")

    return ConversationHandler.END


# ------------------ تایید یا رد سفارش توسط ادمین ------------------
async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_caption(caption="⛔️ دسترسی غیرمجاز!")
        return

    data = query.data
    action, order_id = data.rsplit("_", 1)
    is_approve = "approve" in action
    status_text = "تایید شد ✅" if is_approve else "رد شد ❌"
    db_status = "تایید شده" if is_approve else "رد شده"

    conn = get_db_connection()
    c = conn.cursor()
    if DATABASE_URL:
        c.execute("UPDATE orders SET status = %s WHERE order_id = %s RETURNING user_id;", (db_status, order_id))
        row = c.fetchone()
    else:
        c.execute("SELECT user_id FROM orders WHERE order_id = ?;", (order_id,))
        row = c.fetchone()
        c.execute("UPDATE orders SET status = ? WHERE order_id = ?;", (db_status, order_id))
    conn.commit()
    conn.close()

    original_caption = query.message.caption or ""
    await query.edit_message_caption(
        caption=f"{original_caption}\n\n➡️ وضعیت: {status_text}",
        reply_markup=None,
    )

    if row:
        target_user_id = row[0]
        msg = (
            f"سفارش شماره #{order_id} شما تایید شد و به زودی ارسال می‌شود. 🚀"
            if is_approve
            else f"متاسفانه فیش واریزی سفارش شماره #{order_id} تایید نشد."
        )
        try:
            await context.bot.send_message(chat_id=target_user_id, text=msg)
        except Exception as e:
            print(f"Could not notify user: {e}")


# ------------------ پنل ادمین (لیست کاربران و سفارش‌ها) ------------------
async def show_users_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست کاربران ثبت‌نامی به صورت پیام متنی در تلگرام"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔️ دسترسی فقط برای ادمین مجاز است.")
        return

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT user_id, name, phone, address, created_at FROM users ORDER BY created_at DESC;")
    users = c.fetchall()
    conn.close()

    if not users:
        await update.message.reply_text("هیچ کاربری تا کنون ثبت‌نام نکرده است.")
        return

    text = f"👥 لیست کاربران ثبت‌نام شده ({len(users)} نفر):\n\n"
    for idx, u in enumerate(users, 1):
        uid, name, phone, addr, created_at = u
        text += (
            f"{idx}. نام: {name}\n"
            f"   🆔 آیدی: `{uid}`\n"
            f"   📞 تلفن: `{phone}`\n"
            f"   📍 آدرس: {addr}\n"
            f"   📅 تاریخ: {str(created_at)[:10]}\n"
            f"-------------------------\n"
        )

    if len(text) > 4000:
        for chunk in [text[i:i + 4000] for i in range(0, len(text), 4000)]:
            await update.message.reply_text(chunk, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")


async def export_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال فایل اکسل سفارشات برای ادمین"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔️ دسترسی فقط برای ادمین مجاز است.")
        return

    df = pd.read_sql_query("SELECT * FROM orders ORDER BY created_at DESC", get_db_connection())
    if df.empty:
        await update.message.reply_text("هیچ سفارشی ثبت نشده است.")
        return

    file_name = "orders.xlsx"
    df.to_excel(file_name, index=False)
    with open(file_name, "rb") as f:
        await update.message.reply_document(document=f, caption="📊 گزارش سفارش‌های فروشگاه")
    os.remove(file_name)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_keyboard = [["🛍 خرید عطر", "👤 ثبت‌نام"]]
    markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
    await update.message.reply_text("عملیات لغو شد.", reply_markup=markup)
    return ConversationHandler.END


# ------------------ بدنه اصلی ربات ------------------
def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    reg_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^👤 ثبت‌نام$"), start_registration),
            CommandHandler("register", start_registration),
        ],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_name)],
            REG_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_phone)],
            REG_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_get_address)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    order_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(choose_perfume, pattern="^perfume_")],
        states={
            ORDER_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_get_address)],
            ORDER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_get_phone)],
            WAIT_RECEIPT: [MessageHandler(filters.PHOTO, order_get_receipt)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🛍 خرید عطر$"), catalog))
    app.add_handler(CommandHandler("users", show_users_text))
    app.add_handler(CommandHandler("orders", export_orders))
    app.add_handler(reg_conv)
    app.add_handler(order_conv)
    app.add_handler(CallbackQueryHandler(admin_decision, pattern="^order_"))

    if WEBHOOK_URL:
        print("Starting webhook on Render...")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=WEBHOOK_URL,
        )
    else:
        print("Starting polling locally...")
        app.run_polling()


if __name__ == "__main__":
    main()
