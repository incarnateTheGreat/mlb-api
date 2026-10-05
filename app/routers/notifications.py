"""
Push notification subscription management.

The frontend registers a browser PushSubscription here when the user opts in
to scoring play alerts for a game. The watcher (app/services/scoring_watcher.py)
reads these rows to decide who to notify.

Mutative routes go through CSRFMiddleware, so the frontend must send
X-CSRF-Token. apiFetch() in the React Router app does this automatically.
"""

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.notifications import PushSubscription
from app.services import push_service
from app.services.scoring_watcher import team_logo_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


# Sec-CH-UA-Mobile is a structured-field boolean ("?1"/"?0") that Chromium
# sends on every secure-origin request without opt-in. It's the browser
# stating its own form factor, so it wins over sniffing the User-Agent.
# This fallback list covers Safari and Firefox, which do not implement
# Client Hints.
_MOBILE_UA_TOKENS = (
    "android",
    "iphone",
    "ipod",
    "ipad",
    "mobile",
    "opera mini",
    "windows phone",
    "silk",
    "kindle",
)

# A User-Agent is attacker-controlled and unbounded; cap it before storage.
_MAX_USER_AGENT_LENGTH = 512

# Stand-in team for the test notification's icon (Pittsburgh Pirates).
_TEST_ICON_TEAM_ID = 134


def classify_device(
    user_agent: str | None,
    mobile_hint: str | None = None,
) -> str | None:
    """
    "mobile", "desktop", or None when the request can't be classified.

    `mobile_hint` is the Sec-CH-UA-Mobile header: a structured-field boolean
    ("?1"/"?0") that Chromium sends on every secure-origin request without the
    site opting in. It's the browser stating its own form factor, so it wins
    over sniffing the User-Agent string.

    Known gap: iPadOS 13+ reports a desktop User-Agent and sets the hint to
    "?0", so an iPad is classified as "desktop" either way.
    """
    if mobile_hint == "?1":
        return "mobile"

    if mobile_hint == "?0":
        return "desktop"

    if not user_agent:
        return None

    lowered = user_agent.lower()

    return (
        "mobile"
        if any(token in lowered for token in _MOBILE_UA_TOKENS)
        else "desktop"
    )


class SubscriptionKeys(BaseModel):
    """The encryption keys from PushSubscription.toJSON()."""

    p256dh: str
    auth: str


class SubscriptionPayload(BaseModel):
    """A browser PushSubscription, as returned by pushManager.subscribe()."""

    endpoint: str
    keys: SubscriptionKeys


class SubscribeRequest(BaseModel):
    subscription: SubscriptionPayload
    game_pk: int = Field(..., gt=0)
    # Seeds the dedup cursor so opting in mid-game doesn't replay earlier runs.
    last_at_bat_index: int = -1


class UnsubscribeRequest(BaseModel):
    endpoint: str
    game_pk: int = Field(..., gt=0)


@router.get(
    "/vapid-public-key",
    responses={503: {"description": "Push notifications are not configured"}},
)
async def get_vapid_public_key() -> dict:
    """
    The VAPID public key the browser needs to create a subscription.

    Safe to expose - it's the counterpart to the private signing key and is
    embedded in every subscription request.
    """
    if not push_service.is_push_configured():
        raise HTTPException(
            status_code=503,
            detail="Push notifications are not configured on this server.",
        )

    return {"publicKey": push_service.get_public_key()}


@router.post(
    "/subscribe",
    responses={503: {"description": "Push notifications are not configured"}},
)
async def subscribe(
    body: SubscribeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Register a browser to receive scoring play alerts for one game.

    Idempotent: re-subscribing the same browser to the same game refreshes the
    stored keys rather than creating a duplicate.
    """
    if not push_service.is_push_configured():
        raise HTTPException(
            status_code=503,
            detail="Push notifications are not configured on this server.",
        )

    endpoint = body.subscription.endpoint
    keys = body.subscription.keys

    raw_user_agent = request.headers.get("user-agent")
    user_agent = raw_user_agent[:_MAX_USER_AGENT_LENGTH] if raw_user_agent else None
    device_type = classify_device(
        raw_user_agent,
        request.headers.get("sec-ch-ua-mobile"),
    )

    existing = await db.scalar(
        select(PushSubscription).where(
            PushSubscription.endpoint == endpoint,
            PushSubscription.game_pk == body.game_pk,
        )
    )

    if existing is not None:
        # Keys can be rotated by the browser, so always refresh them.
        existing.p256dh = keys.p256dh
        existing.auth = keys.auth
        existing.last_at_bat_index = body.last_at_bat_index
        # Backfills rows written before these columns existed.
        existing.device_type = device_type
        existing.user_agent = user_agent
    else:
        db.add(
            PushSubscription(
                endpoint=endpoint,
                p256dh=keys.p256dh,
                auth=keys.auth,
                game_pk=body.game_pk,
                last_at_bat_index=body.last_at_bat_index,
                device_type=device_type,
                user_agent=user_agent,
            )
        )

    await db.commit()

    return {"status": "subscribed", "gamePk": body.game_pk}


@router.post("/unsubscribe")
async def unsubscribe(
    body: UnsubscribeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Stop sending alerts to this browser for one game."""
    await db.execute(
        delete(PushSubscription).where(
            PushSubscription.endpoint == body.endpoint,
            PushSubscription.game_pk == body.game_pk,
        )
    )

    await db.commit()

    return {"status": "unsubscribed", "gamePk": body.game_pk}


@router.post(
    "/test",
    responses={
        404: {"description": "Subscription not found"},
        410: {"description": "Subscription expired and was removed"},
        502: {"description": "Push delivery failed"},
    },
)
async def send_test_notification(
    body: UnsubscribeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Send a test push to a registered browser.

    Useful for confirming the full path works without waiting for a real
    scoring play.
    """
    subscription = await db.scalar(
        select(PushSubscription).where(
            PushSubscription.endpoint == body.endpoint,
            PushSubscription.game_pk == body.game_pk,
        )
    )

    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    result = await push_service.send_push(
        endpoint=subscription.endpoint,
        p256dh=subscription.p256dh,
        auth=subscription.auth,
        payload={
            "title": "Test notification",
            "body": "Scoring play alerts are working.",
            "gamePk": body.game_pk,
            # A fixed team logo: this endpoint exists to prove the delivery
            # path works, and the icon is part of that path. Which team it is
            # does not matter, only that it arrives instead of the app icon.
            "icon": team_logo_url(_TEST_ICON_TEAM_ID),
            # Unique per send. Without it the service worker derives a tag
            # from gamePk alone, and a repeat test would silently replace the
            # previous notification rather than alerting again.
            "tag": f"test-{uuid4()}",
        },
    )

    if result == "expired":
        await db.delete(subscription)
        await db.commit()

        raise HTTPException(status_code=410, detail="Subscription expired")

    if result == "failed":
        raise HTTPException(status_code=502, detail="Push delivery failed")

    return {"status": "sent"}
