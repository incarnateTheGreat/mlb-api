"""
Content-related MLB API endpoints via GraphQL.

Handles video highlights, articles, and other rich content.
"""

from app.models.game import (
    GameArticle,
    GameContent,
    GameVideo,
    VideoPlayback,
    VideoTag,
)
from app.services.memory_cache import cached_game_content

# GraphQL query for game content
GAME_CONTENT_QUERY = """
query getGamesByGamePks(
    $gamePks: [Int],
    $locale: Language,
    $gameRecapTags: [String]!,
    $relatedArticleTags: [String]!,
    $contentSource: ContentSource
) {
    getGamesByGamePks(gamePks: $gamePks) {
        gamePk
        gameDate
        content {
            videoContent(locale: $locale) {
                headline
                duration
                title
                description
                slug
                blurb
                guid: playGuid
                contentDate
                preferredPlaybackScenarioURL(preferredPlaybacks: ["hlsCloud", "mp4Avc"])
                playbacks: playbackScenarios {
                    name: playback
                    url: location
                }
                thumbnail {
                    templateUrl
                }
                tags {
                    ... on GameTag {
                        slug
                        type
                        title
                    }
                    ... on TaxonomyTag {
                        slug
                        type
                        title
                    }
                    ... on InternalTag {
                        slug
                        type
                        title
                    }
                }
            }
            ... on GameContent {
                recapArticle: articleContent(
                    locale: $locale,
                    tags: $gameRecapTags,
                    limit: 1,
                    contentSource: $contentSource
                ) {
                    contentDate
                    description
                    headline
                    slug
                    blurb: summary
                    templateUrl: thumbnail
                    type
                }
                relatedArticles: articleContent(
                    locale: $locale,
                    tags: $relatedArticleTags,
                    excludeTags: $gameRecapTags,
                    limit: 5
                ) {
                    contentDate
                    description
                    headline
                    slug
                    blurb: summary
                    templateUrl: thumbnail
                    type
                }
            }
        }
    }
}
"""


class ContentMixin:
    """Mixin providing content-related API methods via GraphQL."""
    
    @cached_game_content
    async def get_game_content(self, game_id: int) -> GameContent:
        """
        Fetch rich content (videos, articles) for a game via GraphQL.
        
        This calls MLB's data-graph.mlb.com GraphQL endpoint to get:
        - Video highlights
        - Recap article
        - Related articles
        """
        variables = {
            "gamePks": [game_id],
            "locale": "EN_US",
            "gameRecapTags": ["game-recap"],
            "relatedArticleTags": ["storytype-article"],
            "contentSource": "MLB",
        }
        
        client = await self._get_client_graphql()
        response = await client.post(
            "",
            json={
                "operationName": "getGamesByGamePks",
                "query": GAME_CONTENT_QUERY,
                "variables": variables,
            },
        )
        response.raise_for_status()
        data = response.json()
        
        games = data.get("data", {}).get("getGamesByGamePks", [])
        if not games:
            return GameContent(game_pk=game_id)
        
        game = games[0]
        content = game.get("content", {}) or {}
        
        videos = self._parse_videos(content.get("videoContent", []) or [])
        recap_article = self._parse_recap_article(content.get("recapArticle") or [])
        related_articles = self._parse_related_articles(content.get("relatedArticles", []) or [])
        
        return GameContent(
            videoContent=videos,
            recap_article=recap_article,
            related_articles=related_articles,
        )
    
    def _parse_videos(self, video_data: list[dict]) -> list[GameVideo]:
        """Parse video content from GraphQL response."""
        videos = []
        for v in video_data:
            videos.append(GameVideo(
                headline=v.get("headline"),
                title=v.get("title"),
                description=v.get("description"),
                duration=v.get("duration"),
                slug=v.get("slug", ""),
                guid=v.get("guid"),
                blurb=v.get("blurb"),
                content_date=v.get("contentDate"),
                thumbnail_url=(
                    v.get("thumbnail", {}).get("templateUrl")
                    if v.get("thumbnail")
                    else None
                ),
                preferred_playback_url=v.get("preferredPlaybackScenarioURL"),
                playbacks=[
                    VideoPlayback(name=p.get("name", ""), url=p.get("url", ""))
                    for p in (v.get("playbacks") or [])
                ],
                tags=[
                    VideoTag(slug=t.get("slug"), type=t.get("type"), title=t.get("title"))
                    for t in (v.get("tags") or [])
                ],
            ))
        return videos
    
    def _parse_recap_article(self, recap_list: list[dict]) -> GameArticle | None:
        """Parse recap article from GraphQL response."""
        if not recap_list:
            return None
        
        r = recap_list[0]
        return GameArticle(
            headline=r.get("headline"),
            description=r.get("description"),
            slug=r.get("slug", ""),
            blurb=r.get("blurb"),
            thumbnail_url=r.get("templateUrl"),
            content_date=r.get("contentDate"),
            type=r.get("type"),
        )
    
    def _parse_related_articles(self, articles_data: list[dict]) -> list[GameArticle]:
        """Parse related articles from GraphQL response."""
        articles = []
        for a in articles_data:
            articles.append(GameArticle(
                headline=a.get("headline"),
                description=a.get("description"),
                slug=a.get("slug", ""),
                blurb=a.get("blurb"),
                thumbnail_url=a.get("templateUrl"),
                content_date=a.get("contentDate"),
                type=a.get("type"),
            ))
        return articles
