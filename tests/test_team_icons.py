"""
Tests for the staggered matchup icon compositor.

The geometry here is the whole point of the feature: Android masks a
notification icon to a circle, so a pair of logos that looks fine as a square
can lose its corners on a real phone. These tests pin that invariant down
without needing a device.
"""

from io import BytesIO
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

    MLB's "spots" logos are roundels, so a circle - not a full-bleed square -
    is the shape the layout is designed around.
    """
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((0, 0, size - 1, size - 1), fill=colour)

    buffer = BytesIO()
    image.save(buffer, format="PNG")

    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _clear_cache():
    team_icons._icon_cache.clear()
    yield
    team_icons._icon_cache.clear()


class TestCompose:
    def test_output_is_a_192px_rgba_png(self):
        image = Image.open(BytesIO(_compose(_logo_png(RED), _logo_png(BLUE))))

        assert image.size == (192, 192)
        assert image.mode == "RGBA"

    def test_artwork_survives_androids_circular_crop(self):
        """
        Nothing may fall outside the inscribed circle.

        This is the constraint that forced the diagonal offset. If someone
        enlarges the logos or pushes them further apart, this fails before a
        phone ever shows a clipped logo.
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

        assert image.getpixel((96, 96)) == BLUE

    def test_both_logos_remain_visible(self):
        """A stagger that hides either team is just a single logo."""
        image = Image.open(BytesIO(_compose(_logo_png(RED), _logo_png(BLUE))))

        assert image.getpixel((40, 66)) == RED
        assert image.getpixel((152, 126)) == BLUE

    def test_background_stays_transparent(self):
        """The canvas corners must not become an opaque box."""
        image = Image.open(BytesIO(_compose(_logo_png(RED), _logo_png(BLUE))))

        assert image.getpixel((0, 0))[3] == 0


class TestRenderMatchupIcon:
    async def test_composes_from_fetched_artwork(self):
        with patch.object(
            team_icons, "_fetch_logo", AsyncMock(return_value=_logo_png(RED))
        ):
            icon = await render_matchup_icon(134, 112)

        assert icon is not None
        assert Image.open(BytesIO(icon)).size == (192, 192)

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
        assert Image.open(BytesIO(response.content)).size == (192, 192)

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
