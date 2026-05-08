from telegram import Bot

from app.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID
)

bot = Bot(token=TELEGRAM_BOT_TOKEN)


def send_message(message: str):

    try:
        bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode="HTML"
        )

    except Exception as e:
        print(f"Telegram error: {e}")