"""Response contract models for frontend-consumed endpoints.

These models intentionally validate only required shape while allowing
extra fields, so upstream MLB payload richness is preserved.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ScheduleDateEntry(ContractModel):
    date: str
    games: list[Any]


class ScheduleRangeResponseContract(ContractModel):
    dates: list[ScheduleDateEntry]


class DailyScheduleResponseContract(ContractModel):
    ALGames: list[Any]
    NLGames: list[Any]
    WBCGames: list[Any]
    isPostSeason: bool
    completedGames: int


class ScheduleRawResponseContract(ContractModel):
    dates: list[Any]


class StandingsResponseContract(ContractModel):
    standingsData: dict[str, Any]
    year: int
    lastUpdated: str


class TeamDetailedResponseContract(ContractModel):
    team: dict[str, Any]
    roster: list[Any]
    coaches: list[Any]
