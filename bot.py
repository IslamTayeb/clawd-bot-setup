import asyncio
import logging
import os
import tempfile
from functools import lru_cache, wraps

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from brain import process_message
from telegram_formatting import format_for_telegram
from transcribe import transcribe_voice

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)


@lru_cache(maxsize=1)
def _settings() -> tuple[str, int]:
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN is required. Copy .env.example to .env and fill it in.")

    allowed_user_id = os.environ.get("ALLOWED_USER_ID")
    if not allowed_user_id:
        raise RuntimeError("ALLOWED_USER_ID is required. Copy .env.example to .env and fill it in.")

    try:
        return token, int(allowed_user_id)
    except ValueError as exc:
        raise RuntimeError("ALLOWED_USER_ID must be an integer.") from exc


def authorized(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None:
            logger.warning("Received update without an effective user")
            return

        _, allowed_user_id = _settings()
        if user.id != allowed_user_id:
            logger.warning("Unauthorized access from user %s", user.id)
            return
        return await func(update, context)

    return wrapper


@authorized
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if message is None:
        return

    await message.reply_text(
        "Hey! I'm your personal assistant. Send me:\n"
        "- Text messages (todos, questions, research requests)\n"
        "- Voice notes (I'll transcribe and process them)\n\n"
        "Examples:\n"
        '- "Add todo: buy groceries"\n'
        '- "Search papers on transformer architectures"\n'
        '- "Read my tasks for today"'
    )


async def _send_long(update: Update, text: str):
    message = update.effective_message
    if message is None:
        return

    while text:
        chunk = text[:4096]
        # Try to split at a newline
        if len(text) > 4096:
            last_nl = chunk.rfind("\n")
            if last_nl > 3000:
                chunk = text[:last_nl]
        formatted = format_for_telegram(chunk)
        try:
            await message.reply_text(
                formatted,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except BadRequest:
            await message.reply_text(chunk, disable_web_page_preview=True)
        text = text[len(chunk):]


def _history_for_chat(context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    return list(context.chat_data.get("conversation_history", []))


def _store_history(context: ContextTypes.DEFAULT_TYPE, history: list[dict]) -> None:
    context.chat_data["conversation_history"] = history


@authorized
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.text:
        return

    await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
    try:
        response, history = await asyncio.to_thread(
            process_message,
            message.text,
            _history_for_chat(context),
            True,
        )
        _store_history(context, history)
        await _send_long(update, response)
    except Exception as e:
        logger.exception("Error processing text message")
        await message.reply_text(f"Something went wrong: {e}")


@authorized
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
    try:
        voice = message.voice or message.audio
        if voice is None:
            await message.reply_text("I couldn't find an audio attachment in that message.")
            return

        file = await context.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as tmp_file:
            tmp_path = tmp_file.name

        await file.download_to_drive(tmp_path)

        region = os.environ.get("AWS_REGION", "us-east-1")
        transcription = await transcribe_voice(tmp_path, region, getattr(voice, "duration", None))
        if not transcription:
            await message.reply_text("I couldn't transcribe that voice note.")
            return

        if len(transcription) <= 500:
            await message.reply_text(f"Heard: {transcription}")
        else:
            await message.reply_text("Transcribed your note. Processing it now.")

        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
        response, history = await asyncio.to_thread(
            process_message,
            transcription,
            _history_for_chat(context),
            True,
        )
        _store_history(context, history)
        await _send_long(update, response)
    except Exception as e:
        logger.exception("Error processing voice message")
        await message.reply_text(f"Something went wrong: {e}")


def main():
    token, _ = _settings()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
