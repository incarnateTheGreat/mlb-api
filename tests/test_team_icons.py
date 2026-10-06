"""
Tests for the staggered matchup icon compositor.

The geometry here is the whole point of the feature: Android masks a
notification icon to a circle, so a pair of logos that looks fine as a square
can lose its corners on a real phone. These tests pin that invariant down
without needing a device.
"""

from io import BytesIO
from math import sqrt
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app
from app.services import team_icons
from app.services.team_icons import _compose, render_matchup_icon

client = TestClient(app)

RED = (220, 40, 40, 255)
BLUE = (40, 60, 220, 255)


def _logo_png(colour: tuple[int, int, int, int], size: int = 192) -> bytes:
    """
    A stand-in for real logo artwork: a filled circle inscribed in the box.

    MLB's "spots" logos are full-bleed roundels - verified to have no
    transparent margin - so a circle touching every edge is the worst case the
    layout has to survive.
    """
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((0, 0, size - 1, size - 1), fill=colour)

    buffer = BytesIO()
    image.save(buffer, format="PNG")

    return buffer.getvalue()


def _opaque_pixels(png: bytes) -> int:
    """How many pixels of a PNG carry artwork."""
    alpha = Image.open(BytesIO(png)).convert("RGBA").getchannel("A")

    # getcolors() yields (count, value), not (value, count).
    return sum(count for count, value in alpha.getcolors() if value > 16)


@pytest.fixture(autouse=True)
def _clear_cache():
    team_icons._icon_cache.clear()
    yield
    team_icons._icon_cache.clear()


class TestCompose:
    def test_output_matches_the_configured_canvas(self):
        image = Image.open(BytesIO(_compose(_logo_png(RED), _logo_png(BLUE))))

        assert image.size == (team_icons._CANVAS_SIZE, team_icons._CANVAS_SIZE)
        assert image.mode == "RGBA"

    def test_artwork_survives_androids_circular_crop(self):
        """
        Nothing may fall outside the inscribed circle.

        This is the constraint that bounds how large the logos can be. If
        someone raises the overlap knob too far, this fails here rather than
        on a phone showing a clipped logo.
        """
        image = Image.open(BytesIO(_compose(_logo_png(RED), _logo_png(BLUE))))
        alpha = image.getchannel("A")

        centre = image.width / 2
        radius = image.width / 2

        outside = [
            (x, y)
            for y in range(image.height)
            for x in range(image.width)
            if alpha.getpixel((x, y)) > 16
            and ((x - centre) ** 2 + (y - centre) ** 2) ** 0.5 > radius
        ]

        assert outside == []

    def test_front_logo_is_drawn_over_the_back_one(self):
        """Where they overlap, the front team wins - that is what marks it."""
        image = Image.open(BytesIO(_compose(_logo_png(RED), _logo_png(BLUE))))
        centre = team_icons._CANVAS_SIZE // 2

        assert image.getpixel((centre, centre)) == BLUE

    def test_back_logo_stays_recognisable(self):
        """
        The whole point of a pair is that both teams read.

        Overlap is the knob that buys logo size, so this is the guard on the
        other side of that trade: push it too far and the back team becomes a
        sliver, which is just a single logo with extra steps.
        """
        image = Image.open(BytesIO(_compose(_logo_png(RED), _logo_png(BLUE))))

        colours = dict(
            (colour, count)
            for count, colour in image.getcolors(maxcolors=1 << 16) or []
        )

        visible_back = colours.get(RED, 0)
        whole_logo = _opaque_pixels(_logo_png(RED, team_icons._LOGO_BOX))

        assert visible_back / whole_logo > 0.3

    def test_background_stays_transparent(self):
        """The canvas corners must not become an opaque box."""
        image = Image.open(BytesIO(_compose(_logo_png(RED), _logo_png(BLUE))))

        assert image.getpixel((0, 0))[3] == 0


class TestGeometry:
    """
    The layout is derived rather than hard-coded, so these cover the maths
    that replaced the constants.
    """

    def test_logos_actually_overlap_by_the_configured_amount(self):
        box, offset = team_icons._fit_to_circle(team_icons._LOGO_OVERLAP)
        separation = 2 * offset * sqrt(2)

        assert (box - separation) / box == pytest.approx(
            team_icons._LOGO_OVERLAP, abs=0.02
        )

    @pytest.mark.parametrize("overlap", [0.1, 0.25, 0.45, 0.6, 0.75])
    def test_any_overlap_setting_still_fits_the_circle(self, overlap):
        """Re-tuning the knob cannot silently produce a clipped icon."""
        box, offset = team_icons._fit_to_circle(overlap)
        reach = offset * sqrt(2) + box / 2 + team_icons._HALO_WIDTH

        assert reach <= team_icons._CANVAS_SIZE / 2

    def test_more_overlap_buys_a_larger_logo(self):
        """The trade the knob exists to make."""
        tight, _ = team_icons._fit_to_circle(0.2)
        loose, _ = team_icons._fit_to_circle(0.5)

        assert loose > tight


class TestRenderMatchupIcon:
    async def test_composes_from_fetched_artwork(self):
        with patch.object(
            team_icons, "_fetch_logo", AsyncMock(return_value=_logo_png(RED))
        ):
            icon = await render_matchup_icon(134, 112)

        assert icon is not None
        assert Image.open(BytesIO(icon)).size == (
            team_icons._CANVAS_SIZE,
            team_icons._CANVAS_SIZE,
        )

    async def test_result_is_cached_per_pair(self):
        """Composing is CPU work; a matchup should pay for it once."""
        fetch = AsyncMock(return_value=_logo_png(RED))

        with patch.object(team_icons, "_fetch_logo", fetch):
            await render_matchup_icon(134, 112)
            await render_matchup_icon(134, 112)

        # Two logos on the first call, nothing on the second.
        assert fetch.await_count == 2

    async def test_order_is_part_of_the_cache_key(self):
        """134-over-112 and 112-over-134 are different images."""
        fetch = AsyncMock(return_value=_logo_png(RED))

        with patch.object(team_icons, "_fetch_logo", fetch):
            await render_matchup_icon(134, 112)
            await render_matchup_icon(112, 134)

        assert fetch.await_count == 4

    async def test_returns_none_when_artwork_is_unavailable(self):
        """A missing logo drops the icon rather than failing the push."""
        fetch = AsyncMock(side_effect=httpx.ConnectError("boom"))

        with patch.object(team_icons, "_fetch_logo", fetch):
            assert await render_matchup_icon(134, 112) is None

    async def test_failures_are_not_cached(self):
        """A transient CDN blip must not poison the matchup for a day."""
        failing = AsyncMock(side_effect=httpx.ConnectError("boom"))

        with patch.object(team_icons, "_fetch_logo", failing):
            await render_matchup_icon(134, 112)

        with patch.object(
            team_icons, "_fetch_logo", AsyncMock(return_value=_logo_png(RED))
        ):
            assert await render_matchup_icon(134, 112) is not None


class TestMatchupIconRoute:
    """
    The route carries a literal `.png` suffix after a path parameter, which is
    easy to get subtly wrong - so these cover the wiring, not just the image.
    """

    def test_serves_a_png(self):
        with patch.object(
            team_icons, "_fetch_logo", AsyncMock(return_value=_logo_png(RED))
        ):
            response = client.get("/notifications/matchup-icon/134/112.png")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert Image.open(BytesIO(response.content)).size == (
            team_icons._CANVAS_SIZE,
            team_icons._CANVAS_SIZE,
        )

    def test_is_cacheable_by_the_browser(self):
        """The icon is refetched on every notification otherwise."""
        with patch.object(
            team_icons, "_fetch_logo", AsyncMock(return_value=_logo_png(RED))
        ):
            response = client.get("/notifications/matchup-icon/134/112.png")

        assert "max-age=86400" in response.headers["cache-control"]

    def test_missing_artwork_is_a_404(self):
        with patch.object(
            team_icons,
            "_fetch_logo",
            AsyncMock(side_effect=httpx.ConnectError("boom")),
        ):
            response = client.get("/notifications/matchup-icon/134/112.png")

        assert response.status_code == 404

    @pytest.mark.parametrize("path", ["0/112", "134/99999", "-1/112"])
    def test_team_ids_outside_the_expected_range_are_rejected(self, path):
        """Bounds stop a caller walking arbitrary values into the fetcher."""
        assert client.get(f"/notifications/matchup-icon/{path}.png").status_code == 422

    def test_non_numeric_team_ids_are_rejected(self):
        response = client.get("/notifications/matchup-icon/abc/112.png")

        assert response.status_code == 422
