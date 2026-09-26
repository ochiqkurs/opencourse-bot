import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from api import confirm_auth, issue_code, report_start
from broadcast_core import fetch_contacts, run_broadcast

logger = logging.getLogger(__name__)

# Conversation states for the /broadcast flow.
BC_AWAIT_MESSAGE, BC_CONFIRM = range(2)

MSG_START_NO_TOKEN = (
    "Salom! Ochiq Kurs platformasiga xush kelibsiz.\n\n"
    "Saytga kirish uchun /login buyrug'ini yuboring — "
    "men sizga 6 ta raqamli kod beraman. Kodni saytga kiriting."
)
MSG_SUCCESS = "✅ Tizimga muvaffaqiyatli kirdingiz. Saytga qaytishingiz mumkin."
MSG_INVALID = (
    "❌ Havola yaroqsiz yoki muddati o'tgan. "
    "Saytdan qaytadan urinib ko'ring."
)
MSG_ERROR = "⚠️ Xatolik yuz berdi. Bir ozdan keyin qaytadan urinib ko'ring."
MSG_OTHER = (
    "Saytga kirish uchun /login buyrug'ini yuboring — "
    "men sizga 6 ta raqamli kod beraman."
)
MSG_HELP = (
    "Ochiq Kurs — kirish boti.\n\n"
    "Buyruqlar:\n"
    "/login — saytga kirish uchun 6 raqamli kod olish.\n"
    "/start — sayt havolasi orqali kirishni tasdiqlash.\n"
    "/help — shu yordam matni.\n\n"
    "Kod 10 daqiqa amal qiladi va bir marta ishlatiladi."
)


async def _confirm_and_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE, token: str
) -> None:
    """Confirm a deep-link token with Django and reply based on the result."""
    user = update.effective_user
    status = await confirm_auth(
        token=token,
        telegram_id=user.id,
        first_name=user.first_name or "",
        last_name=user.last_name or "",
        username=user.username or "",
    )
    if status == 200:
        await update.message.reply_text(MSG_SUCCESS)
    elif status == 400:
        await update.message.reply_text(MSG_INVALID)
    else:
        await update.message.reply_text(MSG_ERROR)


def _format_code(code: str) -> str:
    """Render `123456` as `123 456` for readability."""
    return f"{code[:3]} {code[3:]}" if len(code) == 6 else code


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start [token]. With a token, run the deep-link confirm flow."""
    try:
        if not update.message:
            return
        args = context.args

        # Fire-and-forget: record that this user pressed /start (funnel +
        # broadcast list). Scheduled as a background task so it never delays
        # the reply; report_start swallows its own errors.
        user = update.effective_user
        if user:
            context.application.create_task(
                report_start(
                    telegram_id=user.id,
                    chat_id=update.effective_chat.id if update.effective_chat else None,
                    first_name=user.first_name or "",
                    last_name=user.last_name or "",
                    username=user.username or "",
                    language_code=user.language_code or "",
                    has_token=bool(args),
                )
            )

        if not args:
            await update.message.reply_text(MSG_START_NO_TOKEN)
            return

        await _confirm_and_reply(update, context, args[0])

    except Exception as exc:
        logger.error("Unhandled error in /start handler: %s", exc)
        await update.message.reply_text(MSG_ERROR)


async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /login — issue a 6-digit login code for the user to type on the website."""
    try:
        if not update.message:
            return
        user = update.effective_user
        status, code = await issue_code(
            telegram_id=user.id,
            first_name=user.first_name or "",
            last_name=user.last_name or "",
            username=user.username or "",
        )
        if status == 200 and code:
            pretty = _format_code(code)
            await update.message.reply_text(
                f"Sizning kirish kodingiz:\n\n<code>{pretty}</code>\n\n"
                "Bu kodni ochiqkurs.uz saytidagi kirish sahifasiga kiriting. "
                "Kod 10 daqiqa ichida amal qiladi.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(MSG_ERROR)

    except Exception as exc:
        logger.error("Unhandled error in /login handler: %s", exc)
        await update.message.reply_text(MSG_ERROR)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help — show available commands."""
    try:
        if not update.message:
            return
        await update.message.reply_text(MSG_HELP)
    except Exception as exc:
        logger.error("Unhandled error in /help handler: %s", exc)


async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any non-command message."""
    try:
        if not update.message:
            return
        await update.message.reply_text(MSG_OTHER)
    except Exception as exc:
        logger.error("Unhandled error in fallback handler: %s", exc)


# ── /broadcast (admin only) ──────────────────────────────────────────────
# Admin restriction is enforced by a filter on the entry point (see bot.py),
# so these handlers only ever run for allowed users.

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: ask the admin for the message text."""
    await update.message.reply_text(
        "📣 Yubormoqchi bo'lgan e'lon matnini yuboring.\nBekor qilish: /bekor"
    )
    return BC_AWAIT_MESSAGE


async def broadcast_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Capture the message, show recipient count + a confirm/cancel keyboard."""
    text = update.message.text
    context.user_data["bc_text"] = text
    try:
        contacts = await fetch_contacts()
    except Exception as exc:
        logger.error("broadcast: could not fetch contacts: %s", exc)
        await update.message.reply_text(MSG_ERROR)
        return ConversationHandler.END

    context.user_data["bc_contacts"] = contacts
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Tasdiqlash", callback_data="bc_confirm"),
        InlineKeyboardButton("❌ Bekor", callback_data="bc_cancel"),
    ]])
    await update.message.reply_text(
        f"📊 {len(contacts)} ta foydalanuvchiga yuboriladi.\n\nMatn:\n{text}",
        reply_markup=keyboard,
    )
    return BC_CONFIRM


async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Confirm button: send the broadcast as a background task, report the result."""
    query = update.callback_query
    await query.answer()
    text = context.user_data.get("bc_text")
    contacts = context.user_data.get("bc_contacts")
    context.user_data.pop("bc_text", None)
    context.user_data.pop("bc_contacts", None)
    if not text:
        await query.edit_message_text(MSG_ERROR)
        return ConversationHandler.END

    await query.edit_message_text("⏳ Yuborilmoqda...")
    chat_id = query.message.chat_id
    message_id = query.message.message_id

    async def _run() -> None:
        try:
            r = await run_broadcast(context.bot, text, contacts=contacts)
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id,
                text=(f"✅ Yuborildi: {r['sent']}, xato: {r['failed']}, "
                      f"bloklangan: {r['blocked']}"),
            )
        except Exception as exc:
            logger.error("broadcast run failed: %s", exc)
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=MSG_ERROR,
            )

    # Run in the background so the bot keeps polling during a long send.
    context.application.create_task(_run())
    return ConversationHandler.END


async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel via the ❌ button or /bekor."""
    context.user_data.pop("bc_text", None)
    context.user_data.pop("bc_contacts", None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Bekor qilindi.")
    elif update.message:
        await update.message.reply_text("❌ Bekor qilindi.")
    return ConversationHandler.END
