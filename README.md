# mlb-api

FastAPI backend service for MLB stats and AI-powered analysis. Works alongside the mlb-remix frontend.

## Architecture

```
mlb-remix (Remix frontend) → mlb-api (this service) → MLB Stats API
                                                     → Anthropic API
                                                     → Neon Postgres (cache)
```

## Features

- **Game Summaries**: AI-generated recaps with headlines, key moments, and player highlights
- **Player Stats**: Basic stats plus advanced sabermetrics (wOBA, wRC+, OPS+, FIP, etc.)
- **Scouting Reports**: AI-generated player analysis
- **Matchup Analysis**: Batter vs pitcher predictions
- **Caching**: Two-tier caching (in-memory + Postgres) to reduce latency and API costs

## Quick Start

### Prerequisites

- **Python 3.11+** is required
- On macOS, check your version with `python3 --version`

#### Installing Python 3.11+ on macOS

The system Python on macOS is often outdated. We recommend using **pyenv** to manage Python versions:

```bash
# Install pyenv via Homebrew
brew install pyenv

# Install Python 3.12 (compiles from source, avoids library conflicts)
pyenv install 3.12.3

# Use pyenv's Python to create your venv (see below)
```

### Setup

```bash
# Clone and enter directory
cd mlb-api

# Create virtual environment using pyenv's Python
~/.pyenv/versions/3.12.3/bin/python -m venv venv

# Activate the virtual environment
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# If you get SSL certificate errors (common with corporate VPNs), use:
# pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your actual values

# Run the development server
uvicorn app.main:app --reload

# Open http://localhost:8000/docs for interactive API documentation
```

### Troubleshooting

**`python -m venv venv` fails with ensurepip error**
Your Python installation may have library conflicts. Use pyenv instead (see Prerequisites above).

**SSL certificate errors during `pip install`**
Add `--trusted-host pypi.org --trusted-host files.pythonhosted.org` to your pip command.

**`uvicorn: command not found`**
Make sure you've activated the virtual environment: `source venv/bin/activate`

## API Endpoints

### Games

| Method | Endpoint                    | Description               |
| ------ | --------------------------- | ------------------------- |
| GET    | `/games/{game_id}/boxscore` | Raw boxscore data         |
| GET    | `/games/{game_id}/summary`  | AI-generated game summary |
| GET    | `/games/schedule`           | Game schedule by date     |

### Players

| Method | Endpoint                               | Description                    |
| ------ | -------------------------------------- | ------------------------------ |
| GET    | `/players/{player_id}`                 | Player bio information         |
| GET    | `/players/{player_id}/stats`           | Season stats with sabermetrics |
| GET    | `/players/{player_id}/scouting-report` | AI-generated scouting report   |

### Matchups

| Method | Endpoint                                | Description                |
| ------ | --------------------------------------- | -------------------------- |
| GET    | `/matchups/{batter_id}/vs/{pitcher_id}` | Batter vs pitcher analysis |

### Analysis

| Method | Endpoint           | Description                     |
| ------ | ------------------ | ------------------------------- |
| POST   | `/analysis/custom` | Custom AI analysis with context |

## Example: Calling from Remix

```typescript
// app/routes/game.$gameId.tsx
import { json, type LoaderFunctionArgs } from "@remix-run/node";

const API_URL = process.env.MLB_API_URL || "http://localhost:8000";

export async function loader({ params }: LoaderFunctionArgs) {
  const response = await fetch(`${API_URL}/games/${params.gameId}/summary`);

  if (!response.ok) {
    throw new Response("Game not found", { status: 404 });
  }

  const summary = await response.json();
  return json({ summary });
}
```

## Database Setup

This project uses Neon Postgres for caching. Tables are created automatically on startup.

### Cache Tables

- `cached_responses`: Stores API responses and AI-generated content with TTL
- `ai_generations`: Tracks AI generation metrics for analytics

## Caching

The service uses a two-tier caching strategy:

### In-Memory Cache (TTLCache)

Fast, per-instance caching for high-frequency MLB API data. Implemented in `app/services/memory_cache.py`.

| Cache        | TTL    | Max Size | Use Case                       |
| ------------ | ------ | -------- | ------------------------------ |
| Game Feed    | 10 sec | 500      | Live game data (scores, plays) |
| Game Content | 90 sec | 200      | Highlights and videos          |
| Schedule     | 2 min  | 50       | Daily game schedules           |

**Trade-offs:**

- Fast (nanoseconds vs milliseconds)
- Not persistent (lost on restart)
- Per-instance (not shared across workers)

### Postgres Cache (CacheService)

Persistent caching for expensive-to-compute data. Implemented in `app/services/cache_service.py`.

**Use for:**

- AI-generated content (summaries, scouting reports)
- Data that should survive restarts
- Shared state across multiple workers

## Sabermetrics

The service calculates advanced stats not provided by the MLB API:

### Batting

- **wOBA** (Weighted On-Base Average): Values each hit type by run value
- **wRC+** (Weighted Runs Created Plus): Run production normalized to 100
- **OPS+**: OPS adjusted for park/league (100 = average)
- **ISO** (Isolated Power): SLG - AVG, measures raw power
- **BABIP**: Batting average on balls in play (luck indicator)

### Pitching

- **FIP** (Fielding Independent Pitching): What ERA "should" be
- **ERA+**: ERA adjusted for park/league (higher = better)
- **K/9, BB/9, HR/9**: Rate stats per 9 innings

## Development

```bash
# Run tests
pytest

# Run with auto-reload
uvicorn app.main:app --reload

# Check types (optional)
pip install mypy
mypy app/
```

## Environment Variables

| Variable                 | Required | Description                                                 |
| ------------------------ | -------- | ----------------------------------------------------------- |
| `DATABASE_URL`           | Yes      | Neon Postgres connection string                             |
| `ANTHROPIC_API_KEY`      | Yes      | Anthropic API key for Claude                                |
| `MLB_STATS_API_BASE_URL` | No       | MLB API base URL (default: https://statsapi.mlb.com/api/v1) |
| `DEBUG`                  | No       | Enable debug mode (default: false)                          |
| `CACHE_TTL_SECONDS`      | No       | Default cache TTL (default: 300)                            |

> **Note:** For `DATABASE_URL`, just paste the connection string from Neon as-is. The app automatically converts it for asyncpg compatibility (handles `sslmode`, `channel_binding`, and other parameters).

## Project Structure

```
mlb-api/
├── app/
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # Environment configuration
│   ├── database.py          # Postgres connection
│   ├── routers/
│   │   ├── games.py         # Game endpoints
│   │   ├── players.py       # Player endpoints
│   │   ├── matchups.py      # Matchup endpoints
│   │   └── analysis.py      # Analysis endpoints
│   ├── services/
│   │   ├── mlb_client.py    # MLB Stats API wrapper
│   │   ├── ai_service.py    # Anthropic integration
│   │   ├── sabermetrics.py  # Stat calculations
│   │   ├── cache_service.py # Postgres caching
│   │   └── memory_cache.py  # In-memory TTL caching
│   └── models/
│       ├── game.py          # Game Pydantic models
│       ├── player.py        # Player Pydantic models
│       ├── analysis.py      # Analysis models
│       └── cache.py         # SQLAlchemy ORM models
├── requirements.txt
├── .env.example
└── README.md
```
