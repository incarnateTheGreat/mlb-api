"""
Push notification subscription management.

The frontend registers a browser PushSubscription here when the user opts in
to scoring play alerts for a game. The watcher (app/services/scoring_watcher.py)
reads these rows to decide who to notify.

Mutative routes go through CSRFMiddleware, so the frontend must send
X-CSRF-Token. apiFetch() in the React Router app does this automatically.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.notifications import PushSubscription
from app.services import push_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


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
    else:
        db.add(
            PushSubscription(
                endpoint=endpoint,
                p256dh=keys.p256dh,
                auth=keys.auth,
                game_pk=body.game_pk,
                last_at_bat_index=body.last_at_bat_index,
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
        },
    )

    if result == "expired":
        await db.delete(subscription)
        await db.commit()

        raise HTTPException(status_code=410, detail="Subscription expired")

    if result == "failed":
        raise HTTPException(status_code=502, detail="Push delivery failed")

    return {"status": "sent"}
