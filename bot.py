import logging
import os
import sqlite3
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# --- Sozlamalar (Railway Variables orqali beriladi) ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "BU_YERGA_TOKEN")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/tashkent_andijon1")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin12345")
# Railway'da Volume ulasangiz: DB_PATH=/data/bot.db qiling (statistika saqlanib qoladi)
DB_PATH = os.getenv("DB_PATH", "bot.db")

# Start bosganda ko'rinadigan matn
WELCOME_TEXT = (
    "🚕 <b>ANDIJON — TOSHKENT TAXI</b>\n\n"
    "Asosiy guruh shu yerda 👇\n"
    "Bemalol buyurtma bering, haydovchi va yo'lovchilar shu kanalda.\n\n"
    f"➡️ {CHANNEL_URL}"
)

ASK_PASSWORD = 1  # ConversationHandler holati

logging.basicConfig(level=logging.INFO)


# ---------- Ma'lumotlar bazasi (SQLite) ----------
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, first_seen TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)"
    )
    conn.commit()
    return conn


def add_user(user_id: int) -> None:
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, first_seen) VALUES (?, ?)",
        (user_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def count_users() -> int:
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return n


def incr_joins() -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO stats (key, value) VALUES ('joins', 1) "
        "ON CONFLICT(key) DO UPDATE SET value = value + 1"
    )
    conn.commit()
    conn.close()


def get_joins() -> int:
    conn = get_db()
    row = conn.execute("SELECT value FROM stats WHERE key='joins'").fetchone()
    conn.close()
    return row[0] if row else 0


def channel_username():
    """CHANNEL_URL dan @username ni ajratib oladi (jonli obunachi sonini olish uchun)."""
    url = CHANNEL_URL.rstrip("/")
    if "t.me/" in url:
        part = url.split("t.me/")[-1]
        if part and not part.startswith("+"):  # +... = maxfiy invite, username emas
            return "@" + part
    return None


# ---------- Handlerlar ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    add_user(update.effective_user.id)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📢 Kanalga o'tish", url=CHANNEL_URL)]]
    )
    await update.message.reply_html(WELCOME_TEXT, reply_markup=keyboard)


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🔒 Parolni yuboring:")
    return ASK_PASSWORD


async def check_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text.strip() != ADMIN_PASSWORD:
        await update.message.reply_text("❌ Parol noto'g'ri.")
        return ConversationHandler.END

    started = count_users()
    joins = get_joins()

    total = "—"
    cu = channel_username()
    if cu:
        try:
            total = await context.bot.get_chat_member_count(cu)
        except Exception:
            total = "— (bot kanalda admin emas)"

    text = (
        "📊 <b>Statistika</b>\n\n"
        f"👥 Start bosganlar: <b>{started}</b>\n"
        f"📈 Bot kuzatgan qo'shilishlar: <b>{joins}</b>\n"
        f"📢 Kanaldagi jami obunachi: <b>{total}</b>"
    )
    await update.message.reply_html(text)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Bekor qilindi.")
    return ConversationHandler.END


async def track_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kanalga yangi a'zo qo'shilганда hisoblaydi (bot kanalda admin bo'lishi kerak)."""
    result = update.chat_member
    if result is None:
        return
    members = (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.OWNER,
        ChatMemberStatus.ADMINISTRATOR,
    )
    was_member = result.old_chat_member.status in members
    is_member = result.new_chat_member.status in members
    if not was_member and is_member:
        incr_joins()


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("admin", admin)],
        states={ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_password)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(admin_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("kanal", start))
    app.add_handler(ChatMemberHandler(track_join, ChatMemberHandler.CHAT_MEMBER))

    print("Bot ishga tushdi...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
