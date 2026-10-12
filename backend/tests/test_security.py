import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from app.security import validate_init_data


def signed_init_data(bot_token: str, auth_date: int | None = None) -> str:
    values = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAExample",
        "user": json.dumps(
            {"id": 12345, "first_name": "Ada", "username": "ada"}, separators=(",", ":")
        ),
    }
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(
        secret_key, check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(values)


def test_init_data_signature_and_user_are_validated():
    identity = validate_init_data(signed_init_data("bot-token"), "bot-token")
    assert identity.user_id == 12345
    assert identity.username == "ada"
    assert identity.first_name == "Ada"


def test_init_data_rejects_modified_data():
    init_data = signed_init_data("bot-token").replace("Ada", "Eve")
    with pytest.raises(ValueError, match="signature"):
        validate_init_data(init_data, "bot-token")


def test_init_data_rejects_stale_auth_date():
    old = int(time.time()) - 90_000
    with pytest.raises(ValueError, match="Expired"):
        validate_init_data(signed_init_data("bot-token", old), "bot-token")
