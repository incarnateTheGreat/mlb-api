"""
SQLAlchemy ORM models for Web Push notification subscriptions.

A browser PushSubscription is the address the push service (FCM, Mozilla
autopush, Apple) uses to reach a specific browser install. It must be stored
durably: the device is not connected when a scoring play happens, so the
server needs a persisted list of who to notify.

One row per (endpoint, game) pair. `endpoint` uniquely identifies a browser
install, so the same device following three games has three rows sharing
`endpoint`, `p256dh` and `auth`.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from app.database import Base


class PushSubscription(Base):
    """
    A single browser's request to be notified about one game.

    `last_at_bat_index` is the dedup cursor. It records the highest scoring
    play already sent to this subscriber, so a restart (or an overlapping
    watcher tick) cannot replay notifications the user has already seen.
    """

    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # --- Push endpoint (from the browser's PushSubscription.toJSON()) ---
    # Endpoints can be long, so Text rather than a bounded String.
    endpoint = Column(Text, nullable=False)
    p256dh = Column(Text, nullable=False)  # Client public key for payload encryption
    auth = Column(Text, nullable=False)  # Client auth secret

    # --- What they're following ---
    game_pk = Column(Integer, nullable=False)

    # Highest atBatIndex already notified. -1 means "nothing sent yet"; the
    # watcher seeds it on first sight so subscribing mid-game does not
    # replay every run scored so far.
    last_at_bat_index = Column(Integer, nullable=False, default=-1)

    # Optional owner, for future per-user management. Not required to send.
    user_email = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        # One subscription per browser per game; used for upserts.
        Index(
            "ix_push_subscription_endpoint_game",
            "endpoint",
            "game_pk",
            unique=True,
        ),
        # The watcher's main lookup: "who is following live games?"
        Index("ix_push_subscription_game", "game_pk"),
    )
