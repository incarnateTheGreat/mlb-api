"""
Web Push delivery.

Sends VAPID-signed messages to browser push services (FCM for Chrome,
Mozilla autopush for Firefox, Apple for Safari). The push service then wakes
the recipient's service worker - which is why this works when the device is
locked and the browser is closed.

Generating a VAPID keypair (once, then store in env vars):

    python -c "from py_vapid import Vapid01; v = Vapid01(); v.generate_keys(); \\
        print('public:', v.public_key_urlsafe_base64()); \\
        print('private:', v.private_key_urlsafe_base64())"
"""

import asyncio
import json
import logging
from typing import Any, Literal

from pywebpush import WebPushException, webpush

from app.config import get_settings

logger = logging.getLogger(__name__)

# Push services reject payloads larger than ~4KB once encrypted.
MAX_PAYLOAD_BYTES = 3500

# "expired" means the browser install is gone for good and the row should be
# deleted. "failed" is transient - keep the subscription and try again later.
PushResult = Literal["sent", "expired", "failed"]


def is_push_configured() -> bool:
    """True when VAPID keys are present and push can actually be sent."""
    settings = get_settings()

    return bool(settings.vapid_public_key and settings.vapid_private_key)


def get_public_key() -> str | None:
    """The VAPID public key, which is safe to share with the browser."""
    return get_settings().vapid_public_key


def _send_sync(subscription_info: dict[str, Any], payload: str) -> None:
    """Blocking send. Runs in a worker thread via `send_push`."""
    settings = get_settings()

    webpush(
        subscription_info=subscription_info,
        data=payload,
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={"sub": settings.vapid_subject},
        ttl=600,  # Drop undelivered alerts after 10 minutes; stale scores are noise.
    )


async def send_push(
    endpoint: str,
    p256dh: str,
    auth: str,
    payload: dict[str, Any],
) -> PushResult:
    """
    Deliver one push message.

    Returns:
        "sent" - the push service accepted the message
        "expired" - the subscription is dead; the caller should delete it
        "failed" - transient problem; keep the subscription
    """
    if not is_push_configured():
        logger.warning("Push requested but VAPID keys are not configured")

        return "failed"

    body = json.dumps(payload)

    if len(body.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        logger.warning("Push payload too large (%d bytes), skipping", len(body))

        return "failed"

    subscription_info = {
        "endpoint": endpoint,
        "keys": {"p256dh": p256dh, "auth": auth},
    }

    try:
        # pywebpush is synchronous, so keep it off the event loop.
        await asyncio.to_thread(_send_sync, subscription_info, body)

        return "sent"
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)

        if status in (404, 410):
            logger.info("Push subscription expired (%s)", status)

            return "expired"

        logger.warning("Push delivery failed (%s): %s", status, exc)

        return "failed"
    except Exception:
        logger.exception("Unexpected error sending push")

        return "failed"
