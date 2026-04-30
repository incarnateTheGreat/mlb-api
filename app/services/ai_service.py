"""
Anthropic AI service for generating game summaries and analysis.

The anthropic Python SDK is similar to the JS SDK — same core concepts,
just Python syntax. Key pattern: we structure prompts carefully to get
consistent, parseable JSON output that matches our Pydantic models.
"""

import json
import time
from typing import Optional

import anthropic

from app.config import get_settings
from app.models.game import GameBoxscore, GameSummary, TopPerformer
from app.models.analysis import MatchupAnalysis, AIGenerationMetadata


class AIService:
    """
    Service for AI-powered content generation using Claude.
    
    This wraps the Anthropic client and provides domain-specific
    methods for generating MLB-related content.
    """
    
    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)
        self.model = "claude-sonnet-4-20250514"
    
    async def generate_game_summary(
        self,
        boxscore: GameBoxscore,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> tuple[GameSummary, AIGenerationMetadata]:
        """
        Generate an AI-powered game summary from boxscore data.
        
        Returns both the summary and metadata about the generation.
        """
        start_time = time.time()
        
        # Format boxscore data for the prompt
        boxscore_context = self._format_boxscore_for_prompt(boxscore)
        
        prompt = f"""You are a professional baseball analyst and sportswriter. 
Generate a compelling game summary based on the following boxscore data.

{boxscore_context}

Respond with a JSON object containing:
- "headline": A punchy, engaging headline (10-15 words max)
- "summary": A 2-3 paragraph game recap in an engaging sportswriter style
- "key_moments": An array of 3-5 pivotal moments from the game
- "player_of_the_game": Object with "player_id", "player_name", "position", "stat_line" for the standout player (or null if unclear)

IMPORTANT: Return ONLY valid JSON, no markdown code blocks or extra text."""

        # Note: The Anthropic Python SDK's create() is synchronous, but we
        # can wrap it for our async context. For true async, you'd use
        # anthropic.AsyncAnthropic (similar to axios vs fetch patterns).
        message = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[
                {"role": "user", "content": prompt}
            ],
        )
        
        generation_time_ms = int((time.time() - start_time) * 1000)
        
        # Parse the response
        response_text = message.content[0].text
        parsed = self._parse_json_response(response_text)
        
        # Build the GameSummary
        player_of_game = None
        if parsed.get("player_of_the_game"):
            potg = parsed["player_of_the_game"]
            player_of_game = TopPerformer(
                player_id=potg.get("player_id", 0),
                player_name=potg.get("player_name", "Unknown"),
                position=potg.get("position", ""),
                stat_line=potg.get("stat_line", ""),
            )
        
        summary = GameSummary(
            game_id=boxscore.game_id,
            game_date=boxscore.game_date,
            status=boxscore.status,
            home=boxscore.home,
            away=boxscore.away,
            headline=parsed.get("headline", ""),
            summary=parsed.get("summary", ""),
            key_moments=parsed.get("key_moments", []),
            player_of_the_game=player_of_game,
            cached=False,
        )
        
        metadata = AIGenerationMetadata(
            model=self.model,
            tokens_used=message.usage.input_tokens + message.usage.output_tokens,
            generation_time_ms=generation_time_ms,
            cached=False,
        )
        
        return summary, metadata
    
    async def generate_matchup_analysis(
        self,
        batter_name: str,
        batter_id: int,
        batter_stats: dict,
        pitcher_name: str,
        pitcher_id: int,
        pitcher_stats: dict,
        historical_matchup: Optional[dict] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> tuple[MatchupAnalysis, AIGenerationMetadata]:
        """Generate AI-powered batter vs pitcher matchup analysis."""
        start_time = time.time()
        
        prompt = f"""You are an expert baseball analyst specializing in matchup analysis.
Analyze the following batter vs pitcher matchup:

BATTER: {batter_name} (ID: {batter_id})
Season Stats: {json.dumps(batter_stats, indent=2)}

PITCHER: {pitcher_name} (ID: {pitcher_id})
Season Stats: {json.dumps(pitcher_stats, indent=2)}

{"Historical matchup data: " + json.dumps(historical_matchup) if historical_matchup else "No historical matchup data available."}

Respond with a JSON object containing:
- "advantage": "batter", "pitcher", or "neutral"
- "confidence": A number between 0 and 1 indicating confidence in your analysis
- "analysis": A detailed 2-3 paragraph breakdown of the matchup
- "key_factors": An array of 3-5 key factors influencing this matchup
- "prediction": A one-sentence prediction for how this matchup will play out

IMPORTANT: Return ONLY valid JSON, no markdown code blocks or extra text."""

        message = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        
        generation_time_ms = int((time.time() - start_time) * 1000)
        
        parsed = self._parse_json_response(message.content[0].text)
        
        # Extract historical stats if available
        hist = historical_matchup or {}
        
        analysis = MatchupAnalysis(
            batter_id=batter_id,
            batter_name=batter_name,
            pitcher_id=pitcher_id,
            pitcher_name=pitcher_name,
            career_at_bats=hist.get("at_bats", 0),
            career_hits=hist.get("hits", 0),
            career_home_runs=hist.get("home_runs", 0),
            career_strikeouts=hist.get("strikeouts", 0),
            career_walks=hist.get("walks", 0),
            career_avg=hist.get("avg"),
            advantage=parsed.get("advantage", "neutral"),
            confidence=parsed.get("confidence", 0.5),
            analysis=parsed.get("analysis", ""),
            key_factors=parsed.get("key_factors", []),
            prediction=parsed.get("prediction", ""),
        )
        
        metadata = AIGenerationMetadata(
            model=self.model,
            tokens_used=message.usage.input_tokens + message.usage.output_tokens,
            generation_time_ms=generation_time_ms,
            cached=False,
        )
        
        return analysis, metadata
    
    async def generate_scouting_report(
        self,
        player_name: str,
        player_id: int,
        batting_stats: Optional[dict] = None,
        pitching_stats: Optional[dict] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> tuple[str, AIGenerationMetadata]:
        """Generate an AI-powered scouting report for a player."""
        start_time = time.time()
        
        stats_section = ""
        if batting_stats:
            stats_section += f"Batting Stats: {json.dumps(batting_stats, indent=2)}\n"
        if pitching_stats:
            stats_section += f"Pitching Stats: {json.dumps(pitching_stats, indent=2)}\n"
        
        prompt = f"""You are a veteran baseball scout writing a professional scouting report.
Generate a detailed scouting report for:

PLAYER: {player_name} (ID: {player_id})
{stats_section}

Write a 3-4 paragraph scouting report covering:
1. Overall assessment and player profile
2. Key strengths and tools
3. Areas for improvement or concerns
4. Projection and potential impact

Write in a professional scouting report style. Be specific and analytical."""

        message = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        
        generation_time_ms = int((time.time() - start_time) * 1000)
        
        metadata = AIGenerationMetadata(
            model=self.model,
            tokens_used=message.usage.input_tokens + message.usage.output_tokens,
            generation_time_ms=generation_time_ms,
            cached=False,
        )
        
        return message.content[0].text, metadata
    
    def _format_boxscore_for_prompt(self, boxscore: GameBoxscore) -> str:
        """Format boxscore data into a readable prompt section."""
        lines = [
            f"GAME: {boxscore.away.team.name} @ {boxscore.home.team.name}",
            f"DATE: {boxscore.game_date.strftime('%B %d, %Y')}",
            f"STATUS: {boxscore.status.detailed_state}",
            "",
            "FINAL SCORE:",
            f"  {boxscore.away.team.abbreviation}: {boxscore.away.runs} runs, {boxscore.away.hits} hits, {boxscore.away.errors} errors",
            f"  {boxscore.home.team.abbreviation}: {boxscore.home.runs} runs, {boxscore.home.hits} hits, {boxscore.home.errors} errors",
            "",
        ]
        
        if boxscore.inning_scores:
            lines.append("INNING-BY-INNING:")
            lines.append(f"  {boxscore.away.team.abbreviation}: {' '.join(str(r) for r in boxscore.inning_scores.get('away', []))}")
            lines.append(f"  {boxscore.home.team.abbreviation}: {' '.join(str(r) for r in boxscore.inning_scores.get('home', []))}")
            lines.append("")
        
        if boxscore.winning_pitcher:
            lines.append(f"WINNING PITCHER: {boxscore.winning_pitcher.name}")
        if boxscore.losing_pitcher:
            lines.append(f"LOSING PITCHER: {boxscore.losing_pitcher.name}")
        if boxscore.save_pitcher:
            lines.append(f"SAVE: {boxscore.save_pitcher.name}")
        
        if boxscore.top_performers:
            lines.append("")
            lines.append("TOP PERFORMERS:")
            for performer in boxscore.top_performers:
                lines.append(f"  - {performer.player_name} ({performer.position}): {performer.stat_line}")
        
        return "\n".join(lines)
    
    def _parse_json_response(self, text: str) -> dict:
        """
        Parse JSON from Claude's response, handling common edge cases.
        
        Claude sometimes wraps JSON in markdown code blocks — this strips them.
        """
        # Strip markdown code blocks if present
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # If parsing fails, return empty dict and let caller handle defaults
            return {}


# Singleton for dependency injection
_ai_service: Optional[AIService] = None


def get_ai_service() -> AIService:
    """Returns a singleton AI service instance."""
    global _ai_service
    if _ai_service is None:
        _ai_service = AIService()
    return _ai_service
