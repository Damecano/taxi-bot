import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Sozlamalar (bularni o'zgartiring) ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "BU_YERGA_BOTFATHER_TOKEN")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/tashkent_andijon1")
CHANNEL_NAME = os.getenv("CHANNEL_NAME", "Andijon–Toshkent Taxi")

# Start bosganda ko'rinadigan matn
WELCOME_TEXT = (
    "🚕 <b>ANDIJON — TOSHKENT TAXI</b>\n\n"
    "Asosiy guruh shu yerda 👇\n"
    "Bemalol buyurtma bering, haydovchi va yo'lovchilar shu kanalda.\n\n"
    f"➡️ {CHANNEL_URL}"
)

logging.basicConfig(level=logging.INFO)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📢 Kanalga o'tish", url=CHANNEL_URL)]]
    )
    await update.message.reply_html(
        WELCOME_TEXT,
        reply_markup=keyboard,
        disable_web_page_preview=False,
    )


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    # /kanal buyrug'i ham bir xil javob bersin
    app.add_handler(CommandHandler("kanal", start))
    print("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
