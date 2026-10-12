import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from app.config import settings


@dataclass(frozen=True)
class TelegramIdentity:
    user_id: int
    username: str | None
    first_name: str


def validate_init_data(
    init_data: str, bot_token: str, max_age: int = 86_400
) -> TelegramIdentity:
    """Validate Telegram Mini App initData using Telegram's WebAppData HMAC scheme."""
    if not bot_token:
        raise ValueError("BOT_TOKEN is not configured")
    if not init_data or len(init_data) > 16_384:
        raise ValueError("Missing or oversized initData")

    values = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    received_hash = values.pop("hash", None)
    if not received_hash or len(received_hash) != 64:
        raise ValueError("Missing initData hash")

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key, check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise ValueError("Invalid initData signature")

    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed initData user or auth_date") from exc

    now = int(time.time())
    if auth_date > now + 30 or now - auth_date > max_age:
        raise ValueError("Expired initData")

    return TelegramIdentity(
        user_id=user_id,
        username=user.get("username"),
        first_name=str(user.get("first_name", ""))[:128],
    )


async def get_telegram_identity(
    x_telegram_init_data: str = Header(default="", alias="X-Telegram-Init-Data"),
) -> TelegramIdentity:
    if not settings.bot_token:
        raise HTTPException(status_code=503, detail="Telegram auth is not configured")
    try:
        return validate_init_data(x_telegram_init_data, settings.bot_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
