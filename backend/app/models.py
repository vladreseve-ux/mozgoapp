import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(128), default="")
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    avatar_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    last_wheel_spin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_users_balance_nonnegative"),
        CheckConstraint(
            "avatar_credits >= 0", name="ck_users_avatar_credits_nonnegative"
        ),
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.tg_id", ondelete="RESTRICT"), index=True
    )
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "entry_type", "reference_id", name="uq_ledger_idempotency"
        ),
        CheckConstraint("delta <> 0", name="ck_ledger_delta_nonzero"),
        Index("ix_ledger_user_created", "user_id", "created_at"),
    )


class Payment(Base):
    __tablename__ = "star_payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.tg_id", ondelete="RESTRICT"), index=True
    )
    package_key: Mapped[str] = mapped_column(String(32), nullable=False)
    stars: Mapped[int] = mapped_column(Integer, nullable=False)
    neurons: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    telegram_charge_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "stars > 0 AND neurons >= 0", name="ck_payment_amounts_positive"
        ),
        CheckConstraint(
            "status IN ('pending', 'paid', 'failed')", name="ck_payment_status"
        ),
        Index("ix_payments_user_created", "user_id", "created_at"),
    )


class Game(Base):
    __tablename__ = "games"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.tg_id", ondelete="RESTRICT"), index=True
    )
    game_type: Mapped[str] = mapped_column(String(24), nullable=False)
    bet: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    payout: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    server_seed_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    client_seed: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    server_seed_reveal: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("bet >= 0 AND payout >= 0", name="ck_game_amounts_nonnegative"),
        CheckConstraint(
            "status IN ('active', 'finished', 'cancelled')", name="ck_game_status"
        ),
        Index("ix_games_user_created", "user_id", "created_at"),
        Index("ix_games_type_created", "game_type", "created_at"),
    )


class AiAsset(Base):
    __tablename__ = "ai_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.tg_id", ondelete="RESTRICT"), index=True
    )
    game_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("games.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="trophy")
    prediction_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("game_id", name="uq_ai_asset_game"),
        CheckConstraint("kind IN ('trophy', 'avatar')", name="ck_ai_asset_kind"),
    )
