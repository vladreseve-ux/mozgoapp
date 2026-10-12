# FastAPI's dependency injection API intentionally uses callable defaults.
# ruff: noqa: B008

import hmac
import json
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
from aiogram import Bot
from aiogram.types import LabeledPrice, Update
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import add_pending_payment
from app.bot import create_dispatcher
from app.config import STAR_PACKAGES, settings
from app.db import Base, engine, get_db
from app.games import (
    blackjack_payout,
    dice_roll,
    hand_value,
    is_natural,
    resolve_blackjack,
    server_seed_commitment,
    shuffled_deck,
    weighted_choice,
)
from app.models import AiAsset, Game, LedgerEntry, User
from app.security import TelegramIdentity, get_telegram_identity


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Convenient for the prototype. Production deployments should use Alembic migrations.
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await app.state.redis.ping()
    app.state.bot = Bot(settings.bot_token) if settings.bot_token else None
    app.state.dispatcher = create_dispatcher()
    yield
    await app.state.redis.aclose()
    if app.state.bot:
        await app.state.bot.session.close()
    await engine.dispose()


app = FastAPI(title="МОЗГИНО API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class InvoiceRequest(BaseModel):
    package_key: str


class FairCommitRequest(BaseModel):
    client_seed: str = Field(min_length=1, max_length=128)


class BlackjackStartRequest(BaseModel):
    bet: int = Field(ge=10, le=50_000)
    commit_id: str
    client_seed: str = Field(min_length=1, max_length=128)


class BlackjackActionRequest(BaseModel):
    action: Literal["hit", "stand", "double"]
    action_id: uuid.UUID


class DiceRequest(BaseModel):
    bet: int = Field(ge=1, le=50_000)
    commit_id: str
    client_seed: str = Field(min_length=1, max_length=128)
    mode: Literal["exact", "odd", "even", "low", "high"]
    pick: int | None = Field(default=None, ge=1, le=6)


class WheelRequest(BaseModel):
    commit_id: str
    client_seed: str = Field(min_length=1, max_length=128)


class TrophyRequest(BaseModel):
    game_id: uuid.UUID


async def get_current_user(
    identity: TelegramIdentity = Depends(get_telegram_identity),
    session: AsyncSession = Depends(get_db),
) -> User:
    statement = (
        pg_insert(User)
        .values(
            tg_id=identity.user_id,
            username=identity.username,
            first_name=identity.first_name,
            balance=0,
            avatar_credits=0,
        )
        .on_conflict_do_update(
            index_elements=[User.tg_id],
            set_={"username": identity.username, "first_name": identity.first_name},
        )
        .returning(User)
    )
    user = (await session.execute(statement)).scalar_one()
    await session.commit()
    return user


async def take_fair_commit(
    redis: Redis, *, commit_id: str, tg_id: int, client_seed: str
) -> tuple[str, str]:
    key = f"fair:commit:{commit_id}"
    raw = await redis.get(key)
    if not raw:
        raise HTTPException(
            status_code=409, detail="Fairness seed expired; create a new commitment"
        )
    data = json.loads(raw)
    if data.get("tg_id") != tg_id or data.get("client_seed") != client_seed:
        raise HTTPException(
            status_code=403, detail="Fairness commitment does not match this user/seed"
        )
    await redis.delete(key)
    return data["server_seed"], data["server_seed_hash"]


def safe_blackjack_state(state: dict) -> dict:
    dealer = state["dealer"]
    if state["status"] == "active":
        dealer_view = [dealer[0], {"hidden": True}]
    else:
        dealer_view = dealer
    result = state.get("result")
    return {
        "game_id": state["game_id"],
        "game": "blackjack",
        "status": state["status"],
        "bet": state["bet"],
        "hand": state["hand"],
        "dealer": dealer_view,
        "hand_value": hand_value(state["hand"]),
        "dealer_value": None if state["status"] == "active" else hand_value(dealer),
        "can_double": state["status"] == "active" and len(state["hand"]) == 2,
        "server_seed_hash": state["server_seed_hash"],
        "result": result,
        "balance": state.get("balance"),
    }


async def persist_blackjack_settlement(
    session: AsyncSession, state: dict, outcome: str
) -> dict:
    game_id = uuid.UUID(state["game_id"])
    game = await session.scalar(
        select(Game).where(Game.id == game_id).with_for_update()
    )
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if game.status == "finished":
        state["status"] = "finished"
        state["result"] = game.result
        return state

    user = await session.scalar(
        select(User).where(User.tg_id == game.user_id).with_for_update()
    )
    if not user:
        raise HTTPException(status_code=404, detail="Player not found")
    bet = int(state["bet"])
    payout = blackjack_payout(
        bet,
        outcome,
        natural_win=outcome == "player" and is_natural(state["hand"]),
    )

    if payout:
        user.balance += payout
        session.add(
            LedgerEntry(
                user_id=user.tg_id,
                delta=payout,
                entry_type="game_payout",
                reference_id=str(game_id),
            )
        )

    result = {
        "outcome": outcome,
        "player_total": hand_value(state["hand"]),
        "dealer_total": hand_value(state["dealer"]),
        "payout": payout,
        "net": payout - bet,
        "server_seed": state["server_seed"],
        "client_seed": state["client_seed"],
        "nonce": game.nonce,
    }
    game.status = "finished"
    game.bet = bet
    game.payout = payout
    game.server_seed_reveal = state["server_seed"]
    game.result = result | {"hand": state["hand"], "dealer": state["dealer"]}
    game.finished_at = datetime.now(UTC)
    state["status"] = "finished"
    state["result"] = result
    state["balance"] = user.balance
    await session.commit()
    return state


async def play_dealer_and_settle(
    session: AsyncSession, state: dict, redis: Redis
) -> dict:
    while hand_value(state["dealer"]) < 17:
        state["dealer"].append(state["deck"].pop())
    outcome = resolve_blackjack(state["hand"], state["dealer"])
    state = await persist_blackjack_settlement(session, state, outcome)
    await redis.set(
        f"game:blackjack:{state['game_id']}", json.dumps(state), ex=60 * 60 * 24
    )
    return state


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    return {
        "user_id": user.tg_id,
        "first_name": user.first_name,
        "username": user.username,
        "balance": user.balance,
        "avatar_credits": user.avatar_credits,
        "avatar_url": user.avatar_url,
        "currency": "нейронов",
        "packages": STAR_PACKAGES,
    }


@app.post("/api/billing/invoice")
async def create_invoice(
    body: InvoiceRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    bot = request.app.state.bot
    if not bot:
        raise HTTPException(status_code=503, detail="Telegram bot is not configured")
    package = STAR_PACKAGES.get(body.package_key)
    if not package:
        raise HTTPException(status_code=404, detail="Unknown Stars package")
    payment = await add_pending_payment(session, user, body.package_key)
    await session.commit()
    product_title = str(package["title"])
    product_description = (
        "Одна генерация уникального AI-аватара"
        if body.package_key == "avatar"
        else f"{package['neurons']} нейронов для МОЗГИНО"
    )
    price_label = (
        "Генерация AI-аватара"
        if body.package_key == "avatar"
        else f"{package['neurons']} нейронов"
    )
    try:
        invoice_url = await bot.create_invoice_link(
            title=product_title,
            description=product_description,
            payload=payment.payload,
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=price_label,
                    amount=int(package["stars"]),
                )
            ],
        )
    except Exception as exc:
        # A timeout can happen after Telegram accepted the request; keep the payload pending.
        raise HTTPException(
            status_code=502, detail="Could not create Telegram invoice"
        ) from exc
    return {"invoice_url": invoice_url, "payload": payment.payload}


@app.post("/api/fair/commit")
async def create_fair_commit(
    body: FairCommitRequest,
    request: Request,
    user: User = Depends(get_current_user),
) -> dict:
    server_seed = secrets.token_hex(32)
    server_seed_hash = server_seed_commitment(server_seed)
    commit_id = str(uuid.uuid4())
    data = {
        "tg_id": user.tg_id,
        "client_seed": body.client_seed,
        "server_seed": server_seed,
        "server_seed_hash": server_seed_hash,
    }
    await request.app.state.redis.set(
        f"fair:commit:{commit_id}", json.dumps(data), ex=10 * 60
    )
    return {
        "commit_id": commit_id,
        "server_seed_hash": server_seed_hash,
        "expires_in_seconds": 600,
    }


@app.post("/api/games/blackjack/start")
async def blackjack_start(
    body: BlackjackStartRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if body.bet % 2:
        raise HTTPException(
            status_code=422, detail="Для выплаты 3:2 ставка должна быть чётной"
        )
    redis: Redis = request.app.state.redis
    server_seed, commitment = await take_fair_commit(
        redis,
        commit_id=body.commit_id,
        tg_id=user.tg_id,
        client_seed=body.client_seed,
    )
    db_user = await session.scalar(
        select(User).where(User.tg_id == user.tg_id).with_for_update()
    )
    if not db_user or db_user.balance < body.bet:
        await session.rollback()
        raise HTTPException(status_code=402, detail="Недостаточно нейронов")

    game_id = uuid.uuid4()
    deck = shuffled_deck(server_seed, body.client_seed)
    player_first = deck.pop()
    dealer_first = deck.pop()
    player_second = deck.pop()
    dealer_second = deck.pop()
    state = {
        "game_id": str(game_id),
        "status": "active",
        "bet": body.bet,
        "hand": [player_first, player_second],
        "dealer": [dealer_first, dealer_second],
        "deck": deck,
        "server_seed": server_seed,
        "server_seed_hash": commitment,
        "client_seed": body.client_seed,
        "last_action_id": None,
    }
    db_user.balance -= body.bet
    session.add(
        Game(
            id=game_id,
            user_id=user.tg_id,
            game_type="blackjack",
            bet=body.bet,
            server_seed_hash=commitment,
            client_seed=body.client_seed,
            nonce=0,
            status="active",
            result=state,
        )
    )
    session.add(
        LedgerEntry(
            user_id=user.tg_id,
            delta=-body.bet,
            entry_type="wager",
            reference_id=str(game_id),
        )
    )
    await session.commit()

    if is_natural(state["hand"]) or is_natural(state["dealer"]):
        if is_natural(state["hand"]) and is_natural(state["dealer"]):
            outcome = "push"
        elif is_natural(state["hand"]):
            outcome = "player"
        else:
            outcome = "dealer"
        state = await persist_blackjack_settlement(session, state, outcome)
    else:
        state["balance"] = db_user.balance

    await redis.set(f"game:blackjack:{game_id}", json.dumps(state), ex=60 * 60 * 24)
    return safe_blackjack_state(state)


@app.post("/api/games/blackjack/{game_id}/action")
async def blackjack_action(
    game_id: uuid.UUID,
    body: BlackjackActionRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    redis: Redis = request.app.state.redis
    key = f"game:blackjack:{game_id}"
    lock = redis.lock(f"lock:blackjack:{game_id}", timeout=10, blocking_timeout=5)
    async with lock:
        game = await session.scalar(
            select(Game).where(Game.id == game_id).with_for_update()
        )
        if not game or game.user_id != user.tg_id:
            raise HTTPException(status_code=404, detail="Game not found")
        if game.status == "finished" and isinstance(game.result, dict):
            audit = game.result
            state = {
                "game_id": str(game.id),
                "status": "finished",
                "bet": game.bet,
                "hand": audit["hand"],
                "dealer": audit["dealer"],
                "deck": [],
                "server_seed": audit["server_seed"],
                "server_seed_hash": game.server_seed_hash,
                "client_seed": game.client_seed,
                "result": {
                    key: audit[key]
                    for key in (
                        "outcome",
                        "player_total",
                        "dealer_total",
                        "payout",
                        "net",
                        "server_seed",
                        "client_seed",
                        "nonce",
                    )
                    if key in audit
                },
                "balance": user.balance,
            }
        elif isinstance(game.result, dict) and "deck" in game.result:
            # PostgreSQL is authoritative; Redis is the active-session cache/lock layer.
            state = game.result
        else:
            raw = await redis.get(key)
            if not raw:
                raise HTTPException(status_code=404, detail="Game session not found")
            state = json.loads(raw)
        await redis.set(key, json.dumps(state), ex=60 * 60 * 24)
        if state["status"] == "finished":
            state["balance"] = user.balance
            return safe_blackjack_state(state)
        if state.get("forced_stand"):
            state = await play_dealer_and_settle(session, state, redis)
            return safe_blackjack_state(state)
        if state.get("last_action_id") == str(body.action_id):
            state["balance"] = user.balance
            return safe_blackjack_state(state)

        state["last_action_id"] = str(body.action_id)

        if body.action == "hit":
            state["hand"].append(state["deck"].pop())
            if hand_value(state["hand"]) > 21:
                state = await persist_blackjack_settlement(session, state, "dealer")
                await redis.set(key, json.dumps(state), ex=60 * 60 * 24)
            elif hand_value(state["hand"]) == 21:
                state = await play_dealer_and_settle(session, state, redis)
        elif body.action == "double":
            if len(state["hand"]) != 2:
                raise HTTPException(
                    status_code=409, detail="Double is available on the first move only"
                )
            db_user = await session.scalar(
                select(User).where(User.tg_id == user.tg_id).with_for_update()
            )
            if not db_user or db_user.balance < state["bet"]:
                raise HTTPException(
                    status_code=402, detail="Недостаточно нейронов для удвоения"
                )
            extra_bet = int(state["bet"])
            db_user.balance -= extra_bet
            state["bet"] = extra_bet * 2
            state["forced_stand"] = True
            game.bet = state["bet"]
            state["hand"].append(state["deck"].pop())
            game.result = state
            session.add(
                LedgerEntry(
                    user_id=user.tg_id,
                    delta=-extra_bet,
                    entry_type="wager_double",
                    reference_id=str(game_id),
                )
            )
            await session.commit()
            await redis.set(key, json.dumps(state), ex=60 * 60 * 24)
            state = await play_dealer_and_settle(session, state, redis)
        else:
            state = await play_dealer_and_settle(session, state, redis)

        if state["status"] == "active":
            game.result = state
            game.bet = state["bet"]
            await session.commit()
            await redis.set(key, json.dumps(state), ex=60 * 60 * 24)
            fresh_user = await session.scalar(
                select(User).where(User.tg_id == user.tg_id)
            )
            state["balance"] = fresh_user.balance if fresh_user else None
        return safe_blackjack_state(state)


@app.post("/api/games/dice")
async def play_dice(
    body: DiceRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if body.mode == "exact" and body.pick is None:
        raise HTTPException(status_code=422, detail="Для точного числа выберите грань")
    server_seed, commitment = await take_fair_commit(
        request.app.state.redis,
        commit_id=body.commit_id,
        tg_id=user.tg_id,
        client_seed=body.client_seed,
    )
    db_user = await session.scalar(
        select(User).where(User.tg_id == user.tg_id).with_for_update()
    )
    if not db_user or db_user.balance < body.bet:
        await session.rollback()
        raise HTTPException(status_code=402, detail="Недостаточно нейронов")

    roll = dice_roll(server_seed, body.client_seed)
    if body.mode == "exact":
        won = roll == body.pick
        multiplier_tenths = 57
    elif body.mode == "odd":
        won, multiplier_tenths = roll % 2 == 1, 19
    elif body.mode == "even":
        won, multiplier_tenths = roll % 2 == 0, 19
    elif body.mode == "low":
        won, multiplier_tenths = roll <= 3, 19
    else:
        won, multiplier_tenths = roll >= 4, 19

    game_id = uuid.uuid4()
    payout = body.bet * multiplier_tenths // 10 if won else 0
    db_user.balance -= body.bet
    if payout:
        db_user.balance += payout
    result = {
        "roll": roll,
        "mode": body.mode,
        "pick": body.pick,
        "won": won,
        "payout": payout,
        "net": payout - body.bet,
        "server_seed": server_seed,
        "client_seed": body.client_seed,
        "nonce": 0,
    }
    session.add(
        Game(
            id=game_id,
            user_id=user.tg_id,
            game_type="dice",
            bet=body.bet,
            payout=payout,
            status="finished",
            server_seed_hash=commitment,
            client_seed=body.client_seed,
            nonce=0,
            server_seed_reveal=server_seed,
            result=result,
            finished_at=datetime.now(UTC),
        )
    )
    session.add(
        LedgerEntry(
            user_id=user.tg_id,
            delta=-body.bet,
            entry_type="wager",
            reference_id=str(game_id),
        )
    )
    if payout:
        session.add(
            LedgerEntry(
                user_id=user.tg_id,
                delta=payout,
                entry_type="game_payout",
                reference_id=str(game_id),
            )
        )
    await session.commit()
    return {
        "game_id": str(game_id),
        "game": "dice",
        "balance": db_user.balance,
        "server_seed_hash": commitment,
        "result": result,
    }


@app.post("/api/games/wheel")
async def play_wheel(
    body: WheelRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    db_user = await session.scalar(
        select(User).where(User.tg_id == user.tg_id).with_for_update()
    )
    now = datetime.now(UTC)
    if db_user and db_user.last_wheel_spin_at:
        eligible_at = db_user.last_wheel_spin_at + timedelta(hours=24)
        if eligible_at > now:
            seconds = int((eligible_at - now).total_seconds())
            raise HTTPException(
                status_code=429,
                detail={
                    "message": "Следующий спин позже",
                    "retry_after_seconds": seconds,
                },
            )
    server_seed, commitment = await take_fair_commit(
        request.app.state.redis,
        commit_id=body.commit_id,
        tg_id=user.tg_id,
        client_seed=body.client_seed,
    )
    if not db_user:
        raise HTTPException(status_code=404, detail="Player not found")

    prizes = [(5, 30), (10, 25), (25, 20), (50, 15), (100, 8), (500, 2)]
    reward = weighted_choice(prizes, server_seed, body.client_seed)
    game_id = uuid.uuid4()
    result = {
        "reward": reward,
        "server_seed": server_seed,
        "client_seed": body.client_seed,
        "nonce": 0,
    }
    db_user.balance += reward
    db_user.last_wheel_spin_at = now
    session.add(
        Game(
            id=game_id,
            user_id=user.tg_id,
            game_type="wheel",
            bet=0,
            payout=reward,
            status="finished",
            server_seed_hash=commitment,
            client_seed=body.client_seed,
            nonce=0,
            server_seed_reveal=server_seed,
            result=result,
            finished_at=now,
        )
    )
    session.add(
        LedgerEntry(
            user_id=user.tg_id,
            delta=reward,
            entry_type="wheel_reward",
            reference_id=str(game_id),
        )
    )
    await session.commit()
    return {
        "game_id": str(game_id),
        "game": "wheel",
        "balance": db_user.balance,
        "server_seed_hash": commitment,
        "result": result,
    }


@app.post("/api/ai/trophy")
async def generate_trophy(
    body: TrophyRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if not settings.replicate_api_token:
        raise HTTPException(
            status_code=503, detail="Replicate generation is not configured"
        )
    game = await session.scalar(
        select(Game)
        .where(
            Game.id == body.game_id,
            Game.user_id == user.tg_id,
            Game.status == "finished",
        )
        .with_for_update()
    )
    if (
        not game
        or game.game_type != "blackjack"
        or game.payout - game.bet < settings.trophy_min_net_win
    ):
        raise HTTPException(
            status_code=403, detail="This win is not eligible for a trophy"
        )

    existing = await session.scalar(select(AiAsset).where(AiAsset.game_id == game.id))
    if existing:
        return {
            "prediction_id": existing.prediction_id,
            "status": existing.status,
            "image_url": existing.image_url,
        }

    prompt = (
        "Square collectible victory art for МОЗГИНО, an original cyberpunk brain-casino universe: "
        "a luminous magenta and cyan neural brain wearing a small gold crown, intricate glowing "
        "synapse circuitry, premium holographic trading-card composition, dark midnight background, "
        f"tasteful gold accents, victory reward {game.payout - game.bet} virtual neurons, no text, no logos."
    )
    asset = AiAsset(
        user_id=user.tg_id,
        game_id=game.id,
        prediction_id=f"pending:{uuid.uuid4()}",
        status="starting",
        image_url=None,
        prompt=prompt,
    )
    session.add(asset)
    await session.commit()

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"https://api.replicate.com/v1/models/{settings.replicate_model}/predictions",
                headers={
                    "Authorization": f"Bearer {settings.replicate_api_token}",
                    "Content-Type": "application/json",
                    "Prefer": "wait=10",
                },
                json={"input": {"prompt": prompt, "aspect_ratio": "1:1"}},
            )
    except httpx.HTTPError as exc:
        asset.status = "failed"
        await session.commit()
        raise HTTPException(status_code=502, detail="Replicate request failed") from exc
    if response.is_error:
        asset.status = "failed"
        await session.commit()
        raise HTTPException(status_code=502, detail="Replicate request failed")
    try:
        prediction = response.json()
    except ValueError as exc:
        asset.status = "failed"
        await session.commit()
        raise HTTPException(
            status_code=502, detail="Invalid Replicate response"
        ) from exc
    if not prediction.get("id"):
        asset.status = "failed"
        await session.commit()
        raise HTTPException(
            status_code=502, detail="Replicate response did not contain a prediction id"
        )
    output = prediction.get("output")
    image_url = (
        output[0]
        if isinstance(output, list) and output
        else (output if isinstance(output, str) else None)
    )
    asset.prediction_id = prediction["id"]
    asset.status = prediction.get("status", "starting")
    asset.image_url = image_url
    await session.commit()
    return {
        "prediction_id": asset.prediction_id,
        "status": asset.status,
        "image_url": image_url,
    }


@app.post("/api/ai/avatar")
async def generate_avatar(
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    if not settings.replicate_api_token:
        raise HTTPException(
            status_code=503, detail="Replicate generation is not configured"
        )
    db_user = await session.scalar(
        select(User).where(User.tg_id == user.tg_id).with_for_update()
    )
    if not db_user or db_user.avatar_credits < 1:
        raise HTTPException(
            status_code=402, detail="Сначала купите генерацию AI-аватара за Stars"
        )

    db_user.avatar_credits -= 1
    prompt = (
        "Square profile avatar for МОЗГИНО, an original cyberpunk neural arcade universe: "
        "stylish luminous brain-inspired character portrait, magenta and cyan synapse circuits, "
        "high contrast dark background, premium digital collectible, centered face, no text, no logos."
    )
    asset = AiAsset(
        user_id=user.tg_id,
        kind="avatar",
        prediction_id=f"pending:{uuid.uuid4()}",
        status="starting",
        prompt=prompt,
    )
    session.add(asset)
    await session.commit()

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"https://api.replicate.com/v1/models/{settings.replicate_model}/predictions",
                headers={
                    "Authorization": f"Bearer {settings.replicate_api_token}",
                    "Content-Type": "application/json",
                    "Prefer": "wait=10",
                },
                json={"input": {"prompt": prompt, "aspect_ratio": "1:1"}},
            )
        if response.is_error:
            raise ValueError("Replicate rejected the avatar request")
        prediction = response.json()
        if not prediction.get("id"):
            raise ValueError("Replicate response did not contain a prediction id")
    except (httpx.HTTPError, ValueError) as exc:
        asset.status = "failed"
        await session.execute(
            update(User)
            .where(User.tg_id == user.tg_id)
            .values(avatar_credits=User.avatar_credits + 1)
        )
        await session.commit()
        raise HTTPException(
            status_code=502, detail="Could not generate AI avatar"
        ) from exc

    output = prediction.get("output")
    image_url = (
        output[0]
        if isinstance(output, list) and output
        else (output if isinstance(output, str) else None)
    )
    asset.prediction_id = prediction["id"]
    asset.status = prediction.get("status", "starting")
    asset.image_url = image_url
    if asset.status in {"failed", "canceled"}:
        await session.execute(
            update(User)
            .where(User.tg_id == user.tg_id)
            .values(avatar_credits=User.avatar_credits + 1)
        )
    if image_url:
        await session.execute(
            update(User).where(User.tg_id == user.tg_id).values(avatar_url=image_url)
        )
    await session.commit()
    remaining_avatar_credits = await session.scalar(
        select(User.avatar_credits).where(User.tg_id == user.tg_id)
    )
    return {
        "prediction_id": asset.prediction_id,
        "status": asset.status,
        "image_url": image_url,
        "avatar_credits": remaining_avatar_credits,
    }


@app.get("/api/ai/assets/{prediction_id}")
@app.get("/api/ai/trophy/{prediction_id}")
async def get_trophy_status(
    prediction_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    asset = await session.scalar(
        select(AiAsset)
        .where(
            AiAsset.prediction_id == prediction_id,
            AiAsset.user_id == user.tg_id,
        )
        .with_for_update()
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Trophy prediction not found")
    if asset.status in {
        "succeeded",
        "successful",
        "failed",
        "canceled",
    } or prediction_id.startswith("pending:"):
        return {
            "prediction_id": asset.prediction_id,
            "status": asset.status,
            "image_url": asset.image_url,
        }
    if not settings.replicate_api_token:
        raise HTTPException(
            status_code=503, detail="Replicate generation is not configured"
        )

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"https://api.replicate.com/v1/predictions/{prediction_id}",
                headers={"Authorization": f"Bearer {settings.replicate_api_token}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail="Replicate status request failed"
        ) from exc
    if response.is_error:
        raise HTTPException(status_code=502, detail="Replicate status request failed")
    try:
        prediction = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502, detail="Invalid Replicate response"
        ) from exc

    output = prediction.get("output")
    image_url = (
        output[0]
        if isinstance(output, list) and output
        else (output if isinstance(output, str) else asset.image_url)
    )
    asset.status = prediction.get("status", asset.status)
    asset.image_url = image_url
    if asset.kind == "avatar" and asset.status in {"failed", "canceled"}:
        await session.execute(
            update(User)
            .where(User.tg_id == asset.user_id)
            .values(avatar_credits=User.avatar_credits + 1)
        )
    elif (
        asset.kind == "avatar"
        and asset.status in {"succeeded", "successful"}
        and image_url
    ):
        await session.execute(
            update(User).where(User.tg_id == asset.user_id).values(avatar_url=image_url)
        )
    await session.commit()
    return {
        "prediction_id": asset.prediction_id,
        "status": asset.status,
        "image_url": asset.image_url,
    }


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(
        default="", alias="X-Telegram-Bot-Api-Secret-Token"
    ),
) -> dict[str, bool]:
    bot: Bot | None = request.app.state.bot
    if not bot:
        raise HTTPException(status_code=503, detail="Telegram bot is not configured")
    if not settings.telegram_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret is not configured")
    if not hmac.compare_digest(
        settings.telegram_webhook_secret, x_telegram_bot_api_secret_token
    ):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await request.app.state.dispatcher.feed_update(bot, update)
    return {"ok": True}


# Serve the built Mini App from the same origin as the API for simple local
# Telegram testing through a single HTTPS tunnel.
frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if (frontend_dist / "index.html").is_file():
    app.mount(
        "/",
        StaticFiles(directory=str(frontend_dist), html=True),
        name="mini-app",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
