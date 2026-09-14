"""Telegram Mini App initData validation.

https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
secret_key = HMAC_SHA256(key="WebAppData", message=<bot token>)
hash       = HMAC_SHA256(key=secret_key, message=<data check string>)
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE_SECONDS = 24 * 60 * 60
MAX_LENGTH = 8192


def _hash(token: str, data_check_string: str) -> str:
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()


def telegram_user_id(init_data: str, token: str, clock=time.time) -> int | None:
    """Return the verified Telegram user ID, or None for absent/tampered/expired data."""
    if not init_data or len(init_data) > MAX_LENGTH:
        return None
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = fields.pop("hash", "")
    if not received or "auth_date" not in fields or "user" not in fields:
        return None
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    if not hmac.compare_digest(received, _hash(token, check)):
        return None
    try:
        age = clock() - int(fields["auth_date"])
        identifier = json.loads(fields["user"])["id"]
        if not 0 - MAX_AGE_SECONDS <= age <= MAX_AGE_SECONDS or type(identifier) is not int:
            return None
    except (ValueError, TypeError, KeyError):
        return None
    return identifier


def sign(fields: dict, token: str) -> str:
    """Test and local-development helper: produce valid initData for these fields."""
    from urllib.parse import urlencode

    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    return urlencode({**fields, "hash": _hash(token, check)})
