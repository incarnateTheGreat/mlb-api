"""
Push notification subscription management.

The frontend registers a browser PushSubscription here when the user opts in
to scoring play alerts for a game. The watcher (app/services/scoring_watcher.py)
reads these rows to decide who to notify.

Mutative routes go through CSRFMiddleware, so the frontend must send
X-CSRF-Token. apiFetch() in the React Router app does this automatically.
"""

import ipaddress
import logging
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.notifications import PushSubscription
from app.services import push_service
from app.services.scoring_watcher import notify_subscriptions_changed
from app.services.team_icons import matchup_icon_url, render_matchup_icon

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

# Stand-in matchup for the test notification's icon: Pirates behind, Cubs in
# front. Exercises the composite path rather than a simpler single logo, so
# the test button verifies what a real scoring play will actually send.
_TEST_ICON_BACK_TEAM_ID = 134
_TEST_ICON_FRONT_TEAM_ID = 112

# MLB team IDs are small positive integers. Bounding them keeps a caller from
# walking arbitrary values through the logo fetcher.
_MIN_TEAM_ID = 1
_MAX_TEAM_ID = 9999


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


# Hostnames that never belong to a real push service but do reach things the
# server should not be made to fetch - cloud metadata, loopback, and anything
# resolvable only inside the deployment network.
_BLOCKED_ENDPOINT_SUFFIXES = (
    ".local",
    ".internal",
    ".localdomain",
)


def validate_push_endpoint(endpoint: str) -> str:
    """Reject endpoints the server should never be asked to deliver to.

    A stored endpoint is a URL the watcher later POSTs to, so an attacker who
    could register an arbitrary one would have the server make requests on
    their behalf - server-side request forgery, reaching anything the host can
    see. A browser only ever produces an `https` URL on a public push service,
    so nothing legitimate is lost here.
    """
    parsed = urlparse(endpoint)

    if parsed.scheme != "https":
        raise ValueError("Push endpoint must use https")

    host = parsed.hostname

    if not host:
        raise ValueError("Push endpoint must include a host")

    lowered = host.lower()

    # An IP literal is never a push service, and is how the metadata endpoints
    # (169.254.169.254) and loopback would be reached.
    try:
        ipaddress.ip_address(lowered)
    except ValueError:
        pass
    else:
        raise ValueError("Push endpoint must not address an IP directly")

    if lowered == "localhost" or lowered.endswith(_BLOCKED_ENDPOINT_SUFFIXES):
        raise ValueError("Push endpoint must be publicly routable")

    # A single-label host resolves only through internal search domains.
    if "." not in lowered:
        raise ValueError("Push endpoint must be a fully qualified host")

    return endpoint


class SubscriptionKeys(BaseModel):
    """The encryption keys from PushSubscription.toJSON()."""

    p256dh: str
    auth: str


class SubscriptionPayload(BaseModel):
    """A browser PushSubscription, as returned by pushManager.subscribe()."""

    endpoint: str
    keys: SubscriptionKeys

    @field_validator("endpoint")
    @classmethod
    def _endpoint_is_deliverable(cls, value: str) -> str:
        return validate_push_endpoint(value)


class SubscribeRequest(BaseModel):
    subscription: SubscriptionPayload
    game_pk: int = Field(..., gt=0)
    # Seeds the dedup cursor so opting in mid-game doesn't replay earlier runs.
    last_at_bat_index: int = -1
    # Set when the caller is re-registering an endpoint that may already be
    # known, rather than opting in. The stored cursor is the server's record
    # of what it has actually delivered; a re-registration knows only what the
    # page is showing, which can already include a play still in flight.
    seed_only: bool = False


class UnsubscribeRequest(BaseModel):
    endpoint: str
    game_pk: int = Field(..., gt=0)


class RotateRequest(BaseModel):
    """A browser reporting that its push address has changed.

    Sent by the service worker from `pushsubscriptionchange`, which can fire
    while the app is closed - so this carries no game: every row for the old
    address moves, whichever games they belong to.
    """

    old_endpoint: str
    subscription: SubscriptionPayload


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


@router.get(
    "/matchup-icon/{back_team_id}/{front_team_id}.png",
    response_class=Response,
    responses={
        200: {"content": {"image/png": {}}, "description": "Composite logo"},
        404: {"description": "Logo artwork unavailable"},
    },
)
async def get_matchup_icon(
    back_team_id: int = Path(ge=_MIN_TEAM_ID, le=_MAX_TEAM_ID),
    front_team_id: int = Path(ge=_MIN_TEAM_ID, le=_MAX_TEAM_ID),
) -> Response:
    """
    Two team logos staggered into one 192x192 PNG.

    A notification carries a single `icon` URL, so pairing the logos has to
    happen server side. The browser fetches this while rendering the
    notification, including when the page is closed.
    """
    icon = await render_matchup_icon(back_team_id, front_team_id)

    if icon is None:
        raise HTTPException(status_code=404, detail="Logo artwork unavailable")

    return Response(
        content=icon,
        media_type="image/png",
        # Logos are stable for a season, so let the browser keep it rather
        # than refetching on every notification.
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


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

        # A re-registration must not rewind or skip what is already queued.
        if not body.seed_only:
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

    # The watcher stops querying entirely when nothing is subscribed, so it
    # cannot discover this row by polling. Hand it the news directly.
    notify_subscriptions_changed()

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


@router.post("/rotate")
async def rotate(
    body: RotateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Point every subscription for a browser at its new push address.

    A push endpoint is not permanent: the browser reissues it after storage
    pressure, a long idle period or a push-service re-registration. The old
    address stops accepting messages, so the watcher deletes those rows and
    the device goes silent while its toggle still reads as on.

    Moving the rows rather than recreating them is what preserves
    `last_at_bat_index`. A fresh subscribe would have to guess that cursor
    from whatever the page is showing, which can already include a play whose
    push is still in flight.

    `old_endpoint` is only ever used to look rows up, so it is deliberately
    not validated: a row stored before endpoint validation existed still has
    to be movable.
    """
    new_endpoint = body.subscription.endpoint
    keys = body.subscription.keys

    if body.old_endpoint == new_endpoint:
        # Nothing moved. Refresh the keys anyway: the browser can reissue
        # those without changing the address.
        await db.execute(
            update(PushSubscription)
            .where(PushSubscription.endpoint == new_endpoint)
            .values(p256dh=keys.p256dh, auth=keys.auth)
        )
        await db.commit()

        return {"status": "unchanged", "moved": 0}

    stale = list(
        await db.scalars(
            select(PushSubscription).where(
                PushSubscription.endpoint == body.old_endpoint
            )
        )
    )

    if not stale:
        # Already cleaned up, or this browser was never registered. Not an
        # error: the service worker cannot know which it is.
        logger.info(
            "Rotate found no rows for old endpoint %s", body.old_endpoint[:40]
        )

        return {"status": "unknown", "moved": 0}

    # The browser may already have registered its new address for some of
    # these games, and (endpoint, game_pk) is unique.
    existing = {
        row.game_pk: row
        for row in await db.scalars(
            select(PushSubscription).where(
                PushSubscription.endpoint == new_endpoint
            )
        )
    }

    moved = 0

    for row in stale:
        duplicate = existing.get(row.game_pk)

        if duplicate is None:
            row.endpoint = new_endpoint
            row.p256dh = keys.p256dh
            row.auth = keys.auth
            moved += 1

            continue

        # Keep whichever cursor is further along, so the move can neither
        # replay a delivered play nor skip a pending one.
        duplicate.last_at_bat_index = max(
            duplicate.last_at_bat_index, row.last_at_bat_index
        )
        duplicate.p256dh = keys.p256dh
        duplicate.auth = keys.auth

        await db.delete(row)

    await db.commit()

    logger.info(
        "Rotated %d subscription(s) from %s to %s",
        moved,
        body.old_endpoint[:40],
        new_endpoint[:40],
    )

    return {"status": "rotated", "moved": moved}


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
            # A fixed matchup: this endpoint exists to prove the delivery
            # path works, and the icon is part of that path. Which teams they
            # are does not matter, only that it arrives instead of the app icon.
            "icon": matchup_icon_url(
                _TEST_ICON_BACK_TEAM_ID,
                _TEST_ICON_FRONT_TEAM_ID,
            ),
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
