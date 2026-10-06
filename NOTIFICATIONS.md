# Scoring Alerts & Notifications System

## Overview

This system sends real-time push notifications to users' devices when scoring plays happen during MLB games. Notifications are delivered even when the browser is closed or the device is locked, making them perfect for staying updated on live baseball action.

**Key Design:** We poll the MLB API every 10 seconds and compare new scoring plays against what we've already sent, so users only get notified once per scoring event.

---

## End-to-End Connection Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        SUBSCRIPTION PHASE (Frontend → Backend)               │
└─────────────────────────────────────────────────────────────────────────────┘

    User's Browser                           Backend API              Database
         │                                        │                       │
         │ 1. Click notification bell             │                       │
         ├──────────────────────────────────────→ │                       │
         │                                        │                       │
         │ 2. Service worker creates PushSub      │                       │
         │    (endpoint, p256dh, auth keys)       │                       │
         │                                        │                       │
         │ 3. POST /notifications/subscribe       │                       │
         ├──────────────────────────────────────→ │                       │
         │    subscription + game_pk              │                       │
         │                                        │ 4. Store to DB        │
         │                                        ├──────────────────────→│
         │ ← ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┤                       │
         │         200 OK                         │                       │


┌─────────────────────────────────────────────────────────────────────────────┐
│                    POLLING & NOTIFICATION PHASE (Backend Loop)               │
└─────────────────────────────────────────────────────────────────────────────┘

    Backend Watcher                    MLB API              Browser Push Service
         │                                 │                        │
         │ Every 10 seconds:               │                        │
         │ 1. Query subscriptions          │                        │
         │                                 │                        │
         │ 2. Get subscribed game PKs      │                        │
         │                                 │                        │
         │ 3. Fetch live game feed    ────→│                        │
         │    /game/{id}/feed/live         │                        │
         │                                 │ 4. Return JSON         │
         │ ← ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│                        │
         │    (plays, scores, inning)      │                        │
         │                                 │                        │
         │ 5. Compare scoring plays        │                        │
         │    vs last_at_bat_index         │                        │
         │                                 │                        │
         │ 6. Found new scoring play!      │                        │
         │    Build payload:               │                        │
         │    - title (team + score)       │                        │
         │    - body (inning + play desc)  │                        │
         │                                 │                        │
         │ 7. VAPID-sign JWT               │                        │
         │    (using private key)          │                        │
         │                                 │                        │
         │ 8. AES-GCM encrypt payload      │                        │
         │    (using subscription keys)    │                        │
         │                                 │                        │
         │ 9. POST to push service     ───────────────────────────→ │
         │    endpoint={endpoint}          │                        │
         │    payload={encrypted}          │                        │
         │                                 │     10. Queue for      │
         │                                 │        delivery         │
         │ ← ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │
         │         200 Accepted            │                        │
         │                                 │


┌─────────────────────────────────────────────────────────────────────────────┐
│                       DELIVERY PHASE (Browser Display)                       │
└─────────────────────────────────────────────────────────────────────────────┘

    Push Service                      User's Browser          User's Device
         │                                  │                       │
         │ 1. Device wakes service worker   │                       │
         │    (even if closed/locked)  ────→│                       │
         │    'push' event + encrypted      │                       │
         │    payload                       │                       │
         │                                  │                       │
         │                                  │ 2. Service worker    │
         │                                  │    intercepts push   │
         │                                  │                      │
         │                                  │ 3. Decrypt payload  │
         │                                  │    (AES-GCM with    │
         │                                  │     stored keys)    │
         │                                  │                      │
         │                                  │ 4. Parse JSON:      │
         │                                  │    title, body,     │
         │                                  │    requireInteraction
         │                                  │                      │
         │                                  │ 5. Show notification │
         │                                  ├─────────────────────→│
         │                                  │    (persists until   │
         │                                  │     dismissed)       │
         │                                  │                      │
         │                                  │    ┌──────────────┐  │
         │                                  │    │ PIT scores!  │  │
         │                                  │    │ PIT 4-CHI 2  │  │
         │                                  │    │              │  │
         │                                  │    │ Top 5th ·    │  │
         │                                  │    │ Single to... │  │
         │                                  │    └──────────────┘  │
         │                                  │                      │
         │                                  │ 6. User clicks      │
         │                                  │ ← ────────────────  │
         │                                  │    notification     │
         │                                  │                      │
         │                                  │ 7. Focus game tab  │
         │                                  │    (handled by      │
         │                                  │     service worker  │
         │                                  │     click handler)  │
```

Phase 1: Subscription (Frontend → Backend)

- User clicks notification bell
- Browser creates PushSubscription with encryption keys
- Frontend POSTs subscription data to backend
- Backend stores in database

Phase 2: Polling & Notifications (Backend Loop)

- Every 10 seconds, watcher queries subscribed games
- Fetches current game state from MLB API
- Compares scoring plays against saved cursor
- Builds notification payload (title, body, etc.)
- VAPID-signs the JWT token (using private key)
- AES-GCM encrypts payload (using subscription keys)
- POSTs encrypted message to browser push service

Phase 3: Delivery (Browser Display)

- Push service wakes the browser's service worker
- Service worker decrypts payload using stored keys
- Displays notification with requireInteraction: true
- User can click to focus the game tab

---

## How It Works

### 1. User Subscribes to a Game (Frontend)

- User clicks the notification bell icon on a game page
- Browser creates a `PushSubscription` (handled by service worker)
- Subscription (endpoint, encryption keys) is sent to backend and stored in database

### 2. Backend Polling Loop (Scoring Watcher)

- Every 10 seconds, the watcher queries all games with active subscriptions
- Fetches the latest MLB game feed for each subscribed game
- Compares scoring plays against `last_at_bat_index` (cursor) stored per subscription
- For each new scoring play, sends a VAPID-signed push notification

### 3. Browser Receives Notification

- Browser's service worker intercepts the push
- Decrypts the payload using stored subscription keys (AES-GCM)
- Displays the notification
- Notification persists until user dismisses it (thanks to `requireInteraction: true`)

---

## Notification Payloads

### Scoring Play

**What triggers it:** A run scores

**Format:**

```json
{
  "title": "PIT scores! PIT 4 - CHI 2",
  "body": "Top 5th · Single to left field",
  "gamePk": 123456,
  "atBatIndex": 42,
  "requireInteraction": true,
  "icon": "/api/notifications/matchup-icon/112/134.png"
}
```

**Breakdown:**

- **Title:** Team that scored + current score (always visible, won't get truncated)
- **Body:** Inning (Top/Bot + ordinal like "5th") + play description
- **Description:** Truncated to 120 chars max to avoid OS cutoff mid-word
- **requireInteraction:** Forces notification to stay until user dismisses it
- **icon:** Both teams' logos staggered into one image, with the team that
  scored drawn in front. Path order is `{back}/{front}`, so `112/134` means
  the Pirates (134) scored against the Cubs (112).

  The URL is **relative on purpose**: the service worker resolves it against
  the frontend origin, where `/api/*` proxies through to this API. An absolute
  URL would mean teaching the API its own public hostname.

  Degrades in two steps — to a single team logo on the MLB CDN when the feed
  only has one team ID, then omitted entirely when it has neither, at which
  point the service worker falls back to the app icon. Android crops the icon
  to a circle; iOS and macOS Safari ignore it and always show the manifest
  icon.

### Final Game Notification

**What triggers it:** Game reaches "Final" state

**Format:**

```json
{
  "title": "Final",
  "body": "PIT 5 – CHI 3",
  "gamePk": 123456,
  "atBatIndex": -1,
  "requireInteraction": true,
  "icon": "/api/notifications/matchup-icon/112/134.png"
}
```

**Breakdown:**

- **icon:** Same staggered pair, with the **winning** team drawn in front.

---

## Matchup Icon

**[GET /notifications/matchup-icon/{back_team_id}/{front_team_id}.png](app/routers/notifications.py)**

A notification carries a single `icon` URL, so there is no way to layer two
logos client side — the pairing has to happen on the server. This endpoint
fetches both teams' logos from MLB's CDN and composes them into one 256x256
transparent PNG.

The browser fetches it while rendering the notification, which can be long
after the page was closed, so it must stay publicly reachable (no auth).

**Layout constraints** — see [app/services/team_icons.py](app/services/team_icons.py):

- Android masks the icon to the **inscribed circle**, not the square, and
  MLB's artwork is full bleed with no transparent margin to reclaim. So the
  only lever on logo size is **how much the two overlap** — `_LOGO_OVERLAP` in
  [app/services/team_icons.py](app/services/team_icons.py). The box size is
  derived from it rather than set alongside it, so the two can't be put in an
  inconsistent pair that quietly clips.
- Raising the overlap buys larger logos but hides more of the back team. Two
  tests bracket it: one fails if artwork escapes the circle, the other if the
  back logo drops below 30% visible. At the current 45% overlap each logo is
  61% of the canvas and 58% of the back one still shows.
- The canvas is 256 because Android draws the large icon around that size on
  an xxxhdpi screen; a 192 canvas gets upscaled and goes soft.
- The front logo gets a white outline traced from its own alpha channel. The
  two overlap by roughly a third, and without it a dark logo over a dark logo
  reads as a single shape.
- Results are cached per `(back, front)` pair for a day. Order is part of the
  key, since swapping which team is in front is a different image. Failures
  are **not** cached, so a CDN blip doesn't poison a matchup.
- Composition runs in a worker thread — Pillow is CPU-bound and this event
  loop also drives the scoring watcher's polling.

---

## Code Structure

### Main Files

**[app/services/scoring_watcher.py](app/services/scoring_watcher.py)**

- `_ordinal(n: int)` — Converts numbers to ordinal format (1st, 2nd, 3rd, etc.)
- `_build_payload()` — Shapes a scoring play into a notification
- `_build_final_payload()` — Shapes the final game notification
- `_notify_subscriber()` — Sends notifications to one subscriber for new plays
- `_process_game()` — Main logic: fetch feed, compare plays, send notifs
- `run_scoring_watcher()` — Infinite polling loop (runs at startup)

**[app/services/team_icons.py](app/services/team_icons.py)**

- `team_logo_url()` — Single team's logo on MLB's CDN
- `matchup_icon_url()` — Relative URL for the composite, degrading to a single
  logo and then to None as team IDs go missing
- `render_matchup_icon()` — Fetches both logos and composes them, cached

**[app/routers/notifications.py](app/routers/notifications.py)**

- `GET /notifications/vapid-public-key` — Browser fetches this to create subscriptions
- `GET /notifications/matchup-icon/{back}/{front}.png` — Composite team logos
- `POST /notifications/subscribe` — Frontend sends push subscription to backend
- `POST /notifications/unsubscribe` — Frontend removes subscription

**[app/models/notifications.py](app/models/notifications.py)**

- `PushSubscription` — Database model storing endpoint + keys + game info

**[tests/test_notification_payloads.py](tests/test_notification_payloads.py)**

- 16 tests covering payload formatting, truncation, ordinal generation, etc.

---

## Key Features

✅ **Persistent Notifications** — Stay on screen until dismissed  
✅ **Locked Device Support** — Works even when computer is locked/sleeping (within 10-minute window)  
✅ **Smart Deduplication** — Uses `atBatIndex` cursor to avoid replaying old plays  
✅ **Graceful Cleanup** — Deletes expired subscriptions automatically  
✅ **Final Game Alerts** — Notifies when game ends  
✅ **Truncation Safety** — Descriptions capped at 120 chars to prevent cutoff

---

## Testing

### Run Unit Tests

```bash
./venv/bin/python -m pytest tests/test_notification_payloads.py -v
```

Tests cover:

- Ordinal formatting (1st, 2nd, 3rd, 11th, etc.)
- Scoring play payload structure
- Description truncation
- Final game payload
- Realistic multi-play sequences

### Manual Testing

1. **Subscribe to a game** via the frontend
2. **Run the test script** with a real push subscription:
   ```bash
   pbpaste > /tmp/sub.json  # Paste subscription from browser console
   PYTHONPATH=/Users/gtsacona/projects/mlb-api ./venv/bin/python scripts/send_test_scoring_play.py /tmp/sub.json
   ```
3. **Watch for notification** on your device

---

## Deployment Notes

### Environment Variables Required

None — the system uses existing VAPID keys from `.env`:

- `VAPID_PUBLIC_KEY`
- `VAPID_PRIVATE_KEY`
- `VAPID_SUBJECT`

### Database

- No migrations needed; `PushSubscription` table already exists
- Subscriptions auto-cleanup after game ends (abstract state = "Final")

### Performance

- **Polling interval:** 10 seconds (configurable via `WATCHER_POLL_SECONDS`)
- **Single-replica assumption:** The watcher assumes one replica. If you scale to 2+ replicas, add a Postgres advisory lock to prevent duplicate notifications.
- **Payload size limit:** ~4KB (encrypted); our payloads are well under this

### Monitoring

Look for these log messages:

```
Scoring play watcher started (every 10s)
Game {game_pk} final, cleared {N} subscriptions
Push subscription expired (410)
Push delivery failed (503)
```

---

## Troubleshooting

### Notifications Not Showing

1. **Check subscriptions exist:**

   ```sql
   SELECT * FROM push_subscription LIMIT 5;
   ```

2. **Check VAPID keys are configured:**

   ```sql
   -- Backend side
   SELECT vapid_public_key, vapid_private_key FROM config LIMIT 1;
   ```

3. **Check game is actually live/has scoring plays:**
   - Watcher only polls games with subscriptions
   - If no scoring has happened yet, there's nothing to send

4. **Check 10-minute sleep window:**
   - If device sleeps longer than TTL=600 (10 minutes) during a scoring play, the message is dropped

### Notifications Auto-Dismiss

This is OS behavior, not something we control. Browsers honor `requireInteraction: true` on most platforms (macOS, Windows, Android). iOS limitations may vary.

---

## Future Improvements

- [ ] Add option for game-start alerts
- [ ] Support strike/ball notifications (high-frequency, lower priority)
- [ ] Notification preferences per user (team alerts, playoff-only, etc.)
- [ ] Analytics: track notification click-through rates
- [ ] Adaptive polling: reduce frequency as game nears end

---

## References

- [Web Push API — MDN](https://developer.mozilla.org/en-US/docs/Web/API/Push_API)
- [VAPID — RFC 8292](https://tools.ietf.org/html/rfc8292)
- [MLB StatsAPI Feed Documentation](https://statsapi.mlb.com/api/v1/game/631218/feed/live)
