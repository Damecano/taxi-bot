"""FARGONA — TOSHKENT TAXI boti.

Foydalanuvchilar uchun: /start, /kanal — kanal havolasi bilan xush kelibsiz xabari.
Adminlar uchun: /admin — parol bilan kirib, botning nomi, tavsifi, start xabari,
rasmi, kanal havolasi va parolini to'g'ridan-to'g'ri botning o'zidan tahrirlash.
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# --- Sozlamalar (Railway Variables orqali beriladi) ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "BU_YERGA_TOKEN")
# Railway'da Volume ulang va DB_PATH=/data/bot.db qiling —
# aks holda har deploy'dan keyin statistika VA sozlamalar nolga tushadi.
DB_PATH = os.getenv("DB_PATH", "bot.db")

# Bu qiymatlar faqat birinchi ishga tushishda bazaga yoziladi.
# Keyinchalik hammasi /admin → Sozlamalar bo'limidan o'zgartiriladi.
DEFAULTS = {
    "channel_url": os.getenv("CHANNEL_URL", "https://t.me/fargona_toshkentx_taksi"),
    "admin_password": os.getenv("ADMIN_PASSWORD", "admin12345"),
    "button_text": "📢 Kanalga o'tish",
    "welcome_photo": "",
    "welcome_text": (
        "🚕 <b>FARGONA — TOSHKENT TAXI</b>\n\n"
        "Asosiy guruh shu yerda 👇\n"
        "Bemalol buyurtma bering, haydovchi va yo'lovchilar shu kanalda.\n\n"
        "➡️ https://t.me/fargona_toshkentx_taksi\n"
        "➡️ https://t.me/fargona_toshkentx_taksi\n"
        "➡️ https://t.me/fargona_toshkentx_taksi\n"
    ),
}

# ConversationHandler holatlari
ASK_PASSWORD, MENU, EDIT_VALUE = range(3)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
)
log = logging.getLogger(__name__)


# ---------- Ma'lumotlar bazasi (SQLite) ----------
@contextmanager
def get_db():
    """DB ulanishini beradi: chiqishda commit qiladi va yopadi."""
    folder = os.path.dirname(DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, first_seen TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, added_at TEXT)"
        )
        # Birinchi marta — standart qiymatlarni yozamiz (mavjudlariga tegmaydi).
        for key, value in DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
            )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_setting(key: str) -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else DEFAULTS.get(key, "")


def set_setting(key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def add_user(user_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, first_seen) VALUES (?, ?)",
            (user_id, now_iso()),
        )


def count_users() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def incr_joins() -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO stats (key, value) VALUES ('joins', 1) "
            "ON CONFLICT(key) DO UPDATE SET value = value + 1"
        )


def get_joins() -> int:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM stats WHERE key='joins'").fetchone()
    return row["value"] if row else 0


def is_admin(user_id: int) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM admins WHERE user_id=?", (user_id,)
        ).fetchone()
    return row is not None


def add_admin(user_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id, added_at) VALUES (?, ?)",
            (user_id, now_iso()),
        )


def remove_admin(user_id: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))


def count_admins() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0]


def channel_username():
    """channel_url dan @username ni ajratib oladi (obunachi sonini olish uchun)."""
    url = get_setting("channel_url").rstrip("/")
    if "t.me/" in url:
        part = url.split("t.me/")[-1]
        if part and not part.startswith("+"):  # +... = maxfiy invite, username emas
            return "@" + part
    return None


# ---------- Tahrirlanadigan maydonlar ----------
# kind: text = oddiy matn | photo = rasm | secret = parol | api = Telegram profiliga yoziladi
FIELDS = {
    "welcome_text": {
        "title": "✉️ Start xabari",
        "kind": "text",
        "max_len": 3000,
        "hint": (
            "Yangi matnni yuboring.\n"
            "HTML teglar ishlaydi: <code>&lt;b&gt;qalin&lt;/b&gt;</code>, "
            "<code>&lt;i&gt;qiya&lt;/i&gt;</code>, "
            "<code>&lt;a href=\"havola\"&gt;matn&lt;/a&gt;</code>"
        ),
    },
    "welcome_photo": {
        "title": "🖼 Start xabari rasmi",
        "kind": "photo",
        "hint": "Rasm yuboring. Rasmni olib tashlash uchun <code>-</code> deb yozing.",
    },
    "channel_url": {
        "title": "🔗 Kanal havolasi",
        "kind": "text",
        "max_len": 200,
        "hint": "Havolani yuboring, masalan: <code>https://t.me/kanal_nomi</code>",
    },
    "button_text": {
        "title": "🔘 Tugma matni",
        "kind": "text",
        "max_len": 40,
        "hint": "Kanalga o'tish tugmasidagi matnni yuboring.",
    },
    "bot_name": {
        "title": "🤖 Bot nomi",
        "kind": "api",
        "max_len": 64,
        "hint": (
            "Botning yangi nomini yuboring (64 belgigacha).\n"
            "⚠️ Telegram nomni tez-tez o'zgartirishga ruxsat bermaydi."
        ),
    },
    "bot_description": {
        "title": "📝 Tavsif (description)",
        "kind": "api",
        "max_len": 512,
        "hint": (
            "Bot bo'sh chatda ochilganda ko'rinadigan matnni yuboring "
            "(512 belgigacha)."
        ),
    },
    "bot_short_description": {
        "title": "💬 Qisqa tavsif (about)",
        "kind": "api",
        "max_len": 120,
        "hint": "Bot profilida ko'rinadigan qisqa matnni yuboring (120 belgigacha).",
    },
    "admin_password": {
        "title": "🔑 Admin parol",
        "kind": "secret",
        "max_len": 64,
        "hint": (
            "Yangi parolni yuboring. Xabaringiz darhol o'chiriladi.\n"
            "⚠️ Eski parol bilan kirganlar admin bo'lib qoladi — kerak bo'lsa "
            "«Adminlarni tozalash» tugmasini bosing."
        ),
    },
}

API_FIELDS = {"bot_name", "bot_description", "bot_short_description"}


# ---------- Klaviaturalar ----------
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Statistika", callback_data="stats")],
            [InlineKeyboardButton("⚙️ Sozlamalar", callback_data="settings")],
            [InlineKeyboardButton("👁 Ko'rib chiqish", callback_data="preview")],
            [InlineKeyboardButton("✖️ Yopish", callback_data="close")],
        ]
    )


def settings_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(FIELDS["welcome_text"]["title"], callback_data="edit:welcome_text")],
        [InlineKeyboardButton(FIELDS["welcome_photo"]["title"], callback_data="edit:welcome_photo")],
        [InlineKeyboardButton(FIELDS["channel_url"]["title"], callback_data="edit:channel_url")],
        [InlineKeyboardButton(FIELDS["button_text"]["title"], callback_data="edit:button_text")],
        [InlineKeyboardButton(FIELDS["bot_name"]["title"], callback_data="edit:bot_name")],
        [InlineKeyboardButton(FIELDS["bot_description"]["title"], callback_data="edit:bot_description")],
        [InlineKeyboardButton(FIELDS["bot_short_description"]["title"], callback_data="edit:bot_short_description")],
        [InlineKeyboardButton("🖼 Bot profil rasmi", callback_data="avatar_help")],
        [InlineKeyboardButton(FIELDS["admin_password"]["title"], callback_data="edit:admin_password")],
        [InlineKeyboardButton("🧹 Adminlarni tozalash", callback_data="reset_admins")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data="menu")],
    ]
    return InlineKeyboardMarkup(rows)


def back_kb(target: str = "menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Orqaga", callback_data=target)]]
    )


# ---------- Foydalanuvchi qismi ----------
async def send_welcome(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start xabarini (rasm bilan yoki rasmsiz) yuboradi."""
    text = get_setting("welcome_text")
    photo = get_setting("welcome_photo")
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    get_setting("button_text") or "📢 Kanalga o'tish",
                    url=get_setting("channel_url"),
                )
            ]
        ]
    )
    if photo:
        await context.bot.send_photo(
            chat_id,
            photo=photo,
            caption=text[:1024],
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    else:
        await context.bot.send_message(
            chat_id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    add_user(update.effective_user.id)
    try:
        await send_welcome(update.effective_chat.id, context)
    except TelegramError as exc:
        # Masalan noto'g'ri HTML yoki buzilgan havola — bot jim qolmasin.
        log.error("Start xabarini yuborib bo'lmadi: %s", exc)
        await update.message.reply_text(
            "⚠️ Xabarni ko'rsatishda xatolik. Admin sozlamalarni tekshirsin."
        )


async def track_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kanalga yangi a'zo qo'shilganda hisoblaydi (bot kanalda admin bo'lishi kerak)."""
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


# ---------- Admin qismi ----------
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/admin — allaqachon admin bo'lsa menyu, aks holda parol so'raydi."""
    if is_admin(update.effective_user.id):
        await update.message.reply_html(
            "🛠 <b>Admin panel</b>\n\nKerakli bo'limni tanlang:",
            reply_markup=main_menu_kb(),
        )
        return MENU
    await update.message.reply_text("🔒 Parolni yuboring:")
    return ASK_PASSWORD


async def check_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    entered = (update.message.text or "").strip()
    # Parol chatda qolmasin.
    try:
        await update.message.delete()
    except TelegramError:
        pass

    if entered != get_setting("admin_password"):
        await update.effective_chat.send_message("❌ Parol noto'g'ri.")
        return ConversationHandler.END

    add_admin(update.effective_user.id)
    await update.effective_chat.send_message(
        "✅ Xush kelibsiz, admin!\n\nKerakli bo'limni tanlang:",
        reply_markup=main_menu_kb(),
    )
    return MENU


async def stats_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    total = "—"
    cu = channel_username()
    if cu:
        try:
            total = await context.bot.get_chat_member_count(cu)
        except TelegramError:
            total = "— (bot kanalda admin emas)"

    return (
        "📊 <b>Statistika</b>\n\n"
        f"👥 Start bosganlar: <b>{count_users()}</b>\n"
        f"📈 Bot kuzatgan qo'shilishlar: <b>{get_joins()}</b>\n"
        f"📢 Kanaldagi jami obunachi: <b>{total}</b>\n"
        f"🛠 Adminlar soni: <b>{count_admins()}</b>"
    )


def current_value_text(key: str) -> str:
    """Sozlamaning hozirgi qiymatini ko'rsatish uchun xavfsiz matn."""
    if key == "admin_password":
        return "••••••••"
    value = get_setting(key)
    if key == "welcome_photo":
        return "o'rnatilgan ✅" if value else "yo'q"
    if not value:
        return "—"
    if key == "welcome_text":
        return "\n\n" + value  # HTML sifatida ko'rinadi — o'zi preview bo'ladi
    return value


async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Admin paneldagi barcha tugmalar."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if not is_admin(update.effective_user.id):
        await query.edit_message_text("⛔️ Sessiya tugagan. /admin buyrug'ini yuboring.")
        return ConversationHandler.END

    if data == "menu":
        await query.edit_message_text(
            "🛠 <b>Admin panel</b>\n\nKerakli bo'limni tanlang:",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
        return MENU

    if data == "stats":
        await query.edit_message_text(
            await stats_text(context),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb("menu"),
        )
        return MENU

    if data == "settings":
        await query.edit_message_text(
            "⚙️ <b>Sozlamalar</b>\n\nNimani o'zgartiramiz?",
            parse_mode=ParseMode.HTML,
            reply_markup=settings_kb(),
        )
        return MENU

    if data == "preview":
        await query.edit_message_text(
            "👁 Start xabari quyidagicha ko'rinadi:", reply_markup=back_kb("menu")
        )
        try:
            await send_welcome(update.effective_chat.id, context)
        except TelegramError as exc:
            await update.effective_chat.send_message(f"⚠️ Xatolik: {exc}")
        return MENU

    if data == "avatar_help":
        await query.edit_message_text(
            "🖼 <b>Bot profil rasmi</b>\n\n"
            "Telegram Bot API orqali botning avatarini o'zgartirib bo'lmaydi — "
            "buni faqat @BotFather qiladi:\n\n"
            "1. @BotFather ni oching\n"
            "2. <code>/mybots</code> → shu botni tanlang\n"
            "3. <b>Edit Bot</b> → <b>Edit Botpic</b>\n"
            "4. Yangi rasmni yuboring\n\n"
            "💡 Start xabaridagi rasmni esa shu paneldan o'zgartirsa bo'ladi — "
            "«🖼 Start xabari rasmi».",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb("settings"),
        )
        return MENU

    if data == "reset_admins":
        await query.edit_message_text(
            "🧹 <b>Adminlarni tozalash</b>\n\n"
            "Barcha adminlar (siz ham) ro'yxatdan o'chiriladi. "
            "Keyin joriy parol bilan qaytadan kirasiz. Davom etamizmi?",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Ha, tozalansin", callback_data="reset_yes")],
                    [InlineKeyboardButton("⬅️ Orqaga", callback_data="settings")],
                ]
            ),
        )
        return MENU

    if data == "reset_yes":
        with get_db() as conn:
            conn.execute("DELETE FROM admins")
        await query.edit_message_text(
            "✅ Adminlar ro'yxati tozalandi. Qaytadan kirish uchun /admin yuboring."
        )
        return ConversationHandler.END

    if data == "logout":
        remove_admin(update.effective_user.id)
        await query.edit_message_text("👋 Chiqdingiz. Qaytadan: /admin")
        return ConversationHandler.END

    if data == "close":
        try:
            await query.delete_message()
        except TelegramError:
            pass
        return ConversationHandler.END

    if data.startswith("edit:"):
        key = data.split(":", 1)[1]
        field = FIELDS.get(key)
        if not field:
            return MENU
        context.user_data["edit_key"] = key
        limit = field.get("max_len")
        await query.edit_message_text(
            f"<b>{field['title']}</b>\n\n"
            f"Hozirgi qiymat: {current_value_text(key)}\n\n"
            f"{field['hint']}"
            + (f"\n\nChegara: {limit} belgi." if limit else "")
            + "\n\nBekor qilish: /cancel",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
        return EDIT_VALUE

    return MENU


async def apply_api_field(key: str, value: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Telegram profiliga yozadi (nom / tavsif / qisqa tavsif)."""
    if key == "bot_name":
        await context.bot.set_my_name(name=value)
    elif key == "bot_description":
        await context.bot.set_my_description(description=value)
    elif key == "bot_short_description":
        await context.bot.set_my_short_description(short_description=value)


async def save_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Admin yuborgan yangi qiymatni saqlaydi."""
    key = context.user_data.get("edit_key")
    field = FIELDS.get(key)
    if not field:
        await update.message.reply_text("Nimani tahrirlayotganimiz yo'qoldi. /admin")
        return ConversationHandler.END

    chat = update.effective_chat

    # --- Rasm ---
    if field["kind"] == "photo":
        if update.message.photo:
            set_setting("welcome_photo", update.message.photo[-1].file_id)
            await chat.send_message("✅ Rasm saqlandi.")
        elif (update.message.text or "").strip() == "-":
            set_setting("welcome_photo", "")
            await chat.send_message("✅ Rasm olib tashlandi.")
        else:
            await chat.send_message(
                "❗️ Rasm yuboring yoki olib tashlash uchun <code>-</code> yozing.",
                parse_mode=ParseMode.HTML,
            )
            return EDIT_VALUE
        await chat.send_message("⚙️ Sozlamalar:", reply_markup=settings_kb())
        return MENU

    # --- Matn ---
    value = (update.message.text_html if key == "welcome_text" else update.message.text)
    if not value:
        await chat.send_message("❗️ Matn yuboring.")
        return EDIT_VALUE
    value = value.strip()

    limit = field.get("max_len")
    if limit and len(value) > limit:
        await chat.send_message(
            f"❗️ Juda uzun: {len(value)} belgi, ruxsat {limit}. Qisqartirib yuboring."
        )
        return EDIT_VALUE

    if key == "channel_url" and not value.startswith(("https://", "http://")):
        await chat.send_message(
            "❗️ Havola <code>https://</code> bilan boshlanishi kerak.",
            parse_mode=ParseMode.HTML,
        )
        return EDIT_VALUE

    if field["kind"] == "secret":
        try:
            await update.message.delete()
        except TelegramError:
            pass

    if field["kind"] == "api":
        try:
            await apply_api_field(key, value, context)
        except TelegramError as exc:
            await chat.send_message(
                f"❌ Telegram qabul qilmadi: {exc}\n\n"
                "Nomni juda tez-tez o'zgartirsangiz shunday bo'ladi — "
                "biroz kutib qayta urinib ko'ring."
            )
            return MENU

    set_setting(key, value)
    context.user_data.pop("edit_key", None)

    if key == "welcome_text" and get_setting("welcome_photo") and len(value) > 1024:
        await chat.send_message(
            "⚠️ Saqlandi, lekin rasm bilan yuborilganda matn 1024 belgigacha "
            "qisqartiriladi. Rasmni olib tashlang yoki matnni qisqartiring."
        )
    else:
        await chat.send_message(f"✅ {field['title']} yangilandi.")

    await chat.send_message("⚙️ Sozlamalar:", reply_markup=settings_kb())
    return MENU


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("edit_key", None)
    await update.message.reply_text("Bekor qilindi. /admin")
    return ConversationHandler.END


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Xatolik: %s", context.error)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Kanal havolasi"),
            BotCommand("kanal", "Kanal havolasi"),
            BotCommand("admin", "Admin panel"),
        ]
    )


def main() -> None:
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("admin", admin)],
        states={
            ASK_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, check_password)
            ],
            MENU: [CallbackQueryHandler(on_menu)],
            EDIT_VALUE: [
                MessageHandler(
                    (filters.TEXT & ~filters.COMMAND) | filters.PHOTO, save_value
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("admin", admin)],
        conversation_timeout=600,
    )

    app.add_handler(admin_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("kanal", start))
    app.add_handler(ChatMemberHandler(track_join, ChatMemberHandler.CHAT_MEMBER))
    app.add_error_handler(on_error)

    log.info("Bot ishga tushdi...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
