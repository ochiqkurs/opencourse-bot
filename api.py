import asyncio
import logging

import httpx

from config import (
    BOT_SECRET,
    DJANGO_API_URL,
    DJANGO_BOT_START_URL,
    DJANGO_ISSUE_CODE_URL,
)

logger = logging.getLogger(__name__)

# Retry transient network failures a few times before giving up, so a brief
# blip contacting Django (e.g. during its own restart/deploy) doesn't force the
# user to re-send the command.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.5
REQUEST_TIMEOUT = 10.0


async def _post(url: str, payload: dict) -> httpx.Response | None:
    """
    POST to Django with the shared bot secret, retrying on network errors.

    Returns the httpx.Response, or None if every attempt failed at the
    network level (the caller maps None to a 0 status).
    """
    headers = {"X-Bot-Secret": BOT_SECRET}
    last_exc: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                return await client.post(url, json=payload, headers=headers)
        except httpx.RequestError as exc:
            last_exc = exc
            logger.warning(
                "Network error contacting Django (attempt %d/%d): %s",
                attempt,
                MAX_ATTEMPTS,
                exc,
            )
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)

    logger.error("Giving up after %d attempts: %s", MAX_ATTEMPTS, last_exc)
    return None


async def report_start(
    telegram_id: int,
    chat_id: int | None,
    first_name: str,
    last_name: str,
    username: str,
    language_code: str,
    has_token: bool,
) -> None:
    """
    Best-effort, fire-and-forget telemetry: tell Django this user pressed /start.

    Never raises and never retries — telemetry must not affect the /start UX or
    delay the reply. Any failure is logged and swallowed.
    """
    payload = {
        "telegram_id": telegram_id,
        "chat_id": chat_id,
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
        "language_code": language_code,
        "has_token": has_token,
    }
    headers = {"X-Bot-Secret": BOT_SECRET}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            await client.post(DJANGO_BOT_START_URL, json=payload, headers=headers)
    except Exception as exc:
        logger.warning("bot-start telemetry failed: %s", exc)


async def confirm_auth(
    token: str,
    telegram_id: int,
    first_name: str,
    last_name: str,
    username: str,
) -> int:
    """
    Send auth confirmation to Django backend (bot-link flow).

    Returns the HTTP status code, or 0 on network/unexpected error.
    """
    payload = {
        "token": token,
        "telegram_id": telegram_id,
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
    }

    try:
        response = await _post(DJANGO_API_URL, payload)
        if response is None:
            return 0
        logger.debug("Django response: %s", response.status_code)
        return response.status_code
    except Exception as exc:
        logger.error("Unexpected error in confirm_auth: %s", exc)
        return 0


async def issue_code(
    telegram_id: int,
    first_name: str,
    last_name: str,
    username: str,
) -> tuple[int, str | None]:
    """
    Ask Django to mint a 6-digit login code for this Telegram user.

    Django pre-confirms a token tied to the user; the user then types the
    returned code on the website to sign in.

    Returns (status_code, short_code):
      - (200, code) on success.
      - (403, None) when the bot secret is rejected (logged as an error).
      - (other, None) on Django error.
      - (0, None) on network/unexpected error.
    """
    payload = {
        "telegram_id": telegram_id,
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
    }

    try:
        response = await _post(DJANGO_ISSUE_CODE_URL, payload)
        if response is None:
            return 0, None
        logger.debug("Django issue-code response: %s", response.status_code)
        if response.status_code == 200:
            return 200, response.json().get("short_code")
        if response.status_code == 403:
            logger.error("Bot secret rejected by issue-code endpoint")
        return response.status_code, None
    except Exception as exc:
        logger.error("Unexpected error in issue_code: %s", exc)
        return 0, None
