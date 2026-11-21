from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _response_schema(path: str) -> dict:
    openapi = client.get("/openapi.json").json()
    return openapi["paths"][path]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]


def test_openapi_has_contract_schema_for_schedule() -> None:
    schema = _response_schema("/games/schedule")
    assert schema["$ref"].endswith("DailyScheduleResponseContract")


def test_openapi_has_contract_schema_for_schedule_range() -> None:
    schema = _response_schema("/games/schedule/range")
    assert schema["$ref"].endswith("ScheduleRangeResponseContract")


def test_openapi_has_contract_schema_for_schedule_raw() -> None:
    schema = _response_schema("/games/schedule/raw")
    assert schema["$ref"].endswith("ScheduleRawResponseContract")


def test_openapi_has_contract_schema_for_standings() -> None:
    schema = _response_schema("/standings")
    assert schema["$ref"].endswith("StandingsResponseContract")


def test_openapi_has_contract_schema_for_team_detailed() -> None:
    schema = _response_schema("/teams/{team_slug}/detailed")
    assert schema["$ref"].endswith("TeamDetailedResponseContract")
