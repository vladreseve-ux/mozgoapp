-- PostgreSQL reference schema. ORM models in app/models.py are used by the prototype.
CREATE TABLE users (
    tg_id BIGINT PRIMARY KEY,
    username VARCHAR(64),
    first_name VARCHAR(128) NOT NULL DEFAULT '',
    balance BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0),
    avatar_credits INTEGER NOT NULL DEFAULT 0 CHECK (avatar_credits >= 0),
    avatar_url TEXT,
    last_wheel_spin_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ledger_entries (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE RESTRICT,
    delta BIGINT NOT NULL CHECK (delta <> 0),
    entry_type VARCHAR(32) NOT NULL,
    reference_id VARCHAR(80) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ledger_idempotency UNIQUE (user_id, entry_type, reference_id)
);
CREATE INDEX ix_ledger_user_created ON ledger_entries(user_id, created_at);

CREATE TABLE star_payments (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE RESTRICT,
    package_key VARCHAR(32) NOT NULL,
    stars INTEGER NOT NULL CHECK (stars > 0),
    neurons BIGINT NOT NULL CHECK (neurons >= 0),
    payload VARCHAR(128) NOT NULL UNIQUE,
    telegram_charge_id VARCHAR(128) UNIQUE,
    status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'paid', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at TIMESTAMPTZ
);
CREATE INDEX ix_payments_user_created ON star_payments(user_id, created_at);

CREATE TABLE games (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE RESTRICT,
    game_type VARCHAR(24) NOT NULL,
    bet BIGINT NOT NULL DEFAULT 0 CHECK (bet >= 0),
    payout BIGINT NOT NULL DEFAULT 0 CHECK (payout >= 0),
    status VARCHAR(16) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'finished', 'cancelled')),
    server_seed_hash VARCHAR(64) NOT NULL,
    client_seed VARCHAR(128) NOT NULL,
    nonce INTEGER NOT NULL DEFAULT 0,
    server_seed_reveal VARCHAR(128),
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX ix_games_user_created ON games(user_id, created_at);
CREATE INDEX ix_games_type_created ON games(game_type, created_at);

CREATE TABLE ai_assets (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE RESTRICT,
    game_id UUID UNIQUE REFERENCES games(id) ON DELETE SET NULL,
    kind VARCHAR(24) NOT NULL DEFAULT 'trophy' CHECK (kind IN ('trophy', 'avatar')),
    prediction_id VARCHAR(128) NOT NULL UNIQUE,
    status VARCHAR(24) NOT NULL,
    image_url TEXT,
    prompt TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
