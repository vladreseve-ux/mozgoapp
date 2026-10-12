import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import STAR_PACKAGES
from app.models import LedgerEntry, Payment, User


async def add_pending_payment(
    session: AsyncSession, user: User, package_key: str
) -> Payment:
    package = STAR_PACKAGES[package_key]
    payment = Payment(
        user_id=user.tg_id,
        package_key=package_key,
        stars=int(package["stars"]),
        neurons=int(package["neurons"]),
        payload=f"moz:v1:{uuid.uuid4()}",
        status="pending",
    )
    session.add(payment)
    await session.flush()
    return payment


async def validate_pre_checkout(
    session: AsyncSession, *, tg_id: int, payload: str, currency: str, total_amount: int
) -> bool:
    payment = await session.scalar(
        select(Payment).where(Payment.payload == payload, Payment.user_id == tg_id)
    )
    return bool(
        payment
        and payment.status == "pending"
        and currency == "XTR"
        and total_amount == payment.stars
    )


async def fulfill_successful_payment(
    session: AsyncSession,
    *,
    tg_id: int,
    payload: str,
    currency: str,
    total_amount: int,
    telegram_charge_id: str,
) -> bool:
    """Credit once, and only for a matching Telegram Stars payment."""
    payment = await session.scalar(
        select(Payment).where(Payment.payload == payload).with_for_update()
    )
    if not payment or payment.user_id != tg_id:
        return False
    if payment.status == "paid":
        return payment.telegram_charge_id == telegram_charge_id
    if (
        payment.status != "pending"
        or currency != "XTR"
        or total_amount != payment.stars
        or not telegram_charge_id
        or (payment.neurons == 0 and payment.package_key != "avatar")
    ):
        return False
    if await session.scalar(
        select(Payment.id).where(Payment.telegram_charge_id == telegram_charge_id)
    ):
        return False

    user = await session.scalar(
        select(User).where(User.tg_id == tg_id).with_for_update()
    )
    if not user:
        return False

    payment.status = "paid"
    payment.telegram_charge_id = telegram_charge_id
    payment.paid_at = datetime.now(UTC)
    if payment.neurons:
        user.balance += payment.neurons
        session.add(
            LedgerEntry(
                user_id=tg_id,
                delta=payment.neurons,
                entry_type="star_purchase",
                reference_id=str(payment.id),
            )
        )
    else:
        user.avatar_credits += 1
    await session.flush()
    return True
