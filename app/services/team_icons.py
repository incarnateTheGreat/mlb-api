"""
Team logo artwork for push notification icons.

A notification carries a single `icon` URL, so showing both teams means serving
one image that already contains both - there is no way to layer two URLs client
side. This module owns the single-team URL, the composite URL, and the
compositor that backs it.

Android crops `icon` to a circle and iOS/macOS Safari ignore it entirely, so
everything here is a progressive enhancement rather than a guarantee.
"""

import asyncio
import logging
from io import BytesIO

import httpx
from cachetools import TTLCache
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

# Source artwork: a genuine 192x192 PNG with a transparent background.
_TEAM_LOGO_URL = "https://midfield.mlbstatic.com/v1/team/{team_id}/spots/{size}"
_LOGO_SIZE = 192

# Browser-facing path for the composite. Relative on purpose: the service
# worker resolves it against the frontend origin, where `/api/*` is proxied
# through to this API (mlb-app/app/routes/api.$.ts). An absolute URL would mean
# teaching the API its own public hostname - one more environment variable to
# forget to set on Railway.
_MATCHUP_ICON_PATH = "/api/notifications/matchup-icon"

# Composite geometry. Android masks the icon to the inscribed circle, so both
# logos have to stay inside that circle rather than the full square. Each logo
# sits _DIAGONAL_OFFSET px along the diagonal from centre, which reads as a
# stagger while keeping its artwork within the safe radius. The budget, for a
# worst-case logo that fills its box edge to edge:
#   offset from centre (30 * sqrt 2 = 42.4)
#   + half a logo (48)
#   + the halo below (3)
#   = 93.4, inside the 96px radius with room for antialiasing.
_CANVAS_SIZE = 192
_LOGO_BOX = 96
_DIAGONAL_OFFSET = 30

# White outline around the front logo. The two logos overlap by roughly a
# third of their width, and without a separating edge a dark logo on a dark
# logo reads as one shape.
_HALO_WIDTH = 3

# Composites are stable for a season, so this is about bounding memory rather
# than freshness: a caller walking arbitrary ID pairs cannot grow it without
# limit. ~256 entries of ~20KB caps it around 5MB.
_icon_cache: TTLCache = TTLCache(maxsize=256, ttl=86_400)


def team_logo_url(team_id: int | None) -> str | None:
    """Single-team notification icon URL, or None when the ID is unknown."""
    if team_id is None:
        return None

    return _TEAM_LOGO_URL.format(team_id=team_id, size=_LOGO_SIZE)


def matchup_icon_url(
    back_team_id: int | None,
    front_team_id: int | None,
) -> str | None:
    """
    Icon showing both teams, with `front_team_id` drawn on top.

    Degrades to a single logo when only one ID is known, and to None when
    neither is - callers omit `icon` entirely in that case.
    """
    if back_team_id is None or front_team_id is None:
        return team_logo_url(
            front_team_id if front_team_id is not None else back_team_id
        )

    return f"{_MATCHUP_ICON_PATH}/{back_team_id}/{front_team_id}.png"


def _prepare(png: bytes) -> Image.Image:
    """Decode one logo and scale it to the composite's per-logo box."""
    image = Image.open(BytesIO(png)).convert("RGBA")

    return image.resize((_LOGO_BOX, _LOGO_BOX), Image.Resampling.LANCZOS)


def _compose(back_png: bytes, front_png: bytes) -> bytes:
    """Stagger two logos into a single transparent PNG."""
    back = _prepare(back_png)
    front = _prepare(front_png)

    centre = _CANVAS_SIZE // 2
    half = _LOGO_BOX // 2
    back_xy = (centre - _DIAGONAL_OFFSET - half,) * 2
    front_xy = (centre + _DIAGONAL_OFFSET - half,) * 2

    canvas = Image.new("RGBA", (_CANVAS_SIZE, _CANVAS_SIZE), (0, 0, 0, 0))
    canvas.alpha_composite(back, back_xy)

    # Dilating the front logo's own alpha channel traces its silhouette, which
    # gives an outline that follows the artwork instead of a bounding box.
    halo = front.getchannel("A").filter(
        ImageFilter.MaxFilter(_HALO_WIDTH * 2 + 1)
    )
    canvas.paste((255, 255, 255, 255), front_xy, halo)

    canvas.alpha_composite(front, front_xy)

    buffer = BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)

    return buffer.getvalue()


async def _fetch_logo(client: httpx.AsyncClient, team_id: int) -> bytes:
    """Download one team's source logo."""
    url = _TEAM_LOGO_URL.format(team_id=team_id, size=_LOGO_SIZE)
    response = await client.get(url)
    response.raise_for_status()

    return response.content


async def render_matchup_icon(
    back_team_id: int,
    front_team_id: int,
) -> bytes | None:
    """
    PNG bytes for a staggered pair of logos, or None if artwork is missing.

    Cached, so the fetch-and-compose work happens roughly once per matchup.
    """
    key = (back_team_id, front_team_id)
    cached = _icon_cache.get(key)

    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            back_png, front_png = await asyncio.gather(
                _fetch_logo(client, back_team_id),
                _fetch_logo(client, front_team_id),
            )
    except httpx.HTTPError:
        logger.warning(
            "Matchup icon source fetch failed for %s/%s",
            back_team_id,
            front_team_id,
        )

        return None

    # Pillow is CPU-bound, and this event loop also runs the scoring watcher's
    # polling - blocking it would delay every subscriber's notification.
    icon = await asyncio.to_thread(_compose, back_png, front_png)

    _icon_cache[key] = icon

    return icon
