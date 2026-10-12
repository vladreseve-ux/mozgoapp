import asyncio
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)
from sqlalchemy import select

from app.billing import (
    add_pending_payment,
    fulfill_successful_payment,
    validate_pre_checkout,
)
from app.config import STAR_PACKAGES, settings
from app.db import SessionLocal
from app.models import User

router = Router(name="moz-bot")


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    keyboard_rows = []
    app_url = settings.mini_app_url.strip()
    parsed_app_url = urlparse(app_url)
    if parsed_app_url.scheme == "https" and parsed_app_url.hostname not in {
        None,
        "example.com",
        "www.example.com",
    }:
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text="ОТКРЫТЬ МОЗГИНО",
                    web_app=WebAppInfo(url=app_url),
                )
            ]
        )
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="Купить нейроны · 50 ⭐", callback_data="buy:starter"
            ),
        ]
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await message.answer(
        "🧠 <b>МОЗГИНО</b>\nНеоновые игры для быстрой разминки мозга.\n\n"
        "Фишки виртуальные: их нельзя обменять на деньги или вывести.",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.message(Command("buy"))
async def buy_handler(message: Message, bot: Bot) -> None:
    if not message.from_user:
        return
    package_key = "starter"
    package = STAR_PACKAGES[package_key]
    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.tg_id == message.from_user.id)
        )
        if not user:
            user = User(
                tg_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name or "",
            )
            session.add(user)
            await session.flush()
        payment = await add_pending_payment(session, user, package_key)
        await session.commit()
    await bot.send_invoice(
        chat_id=message.chat.id,
        title=str(package["title"]),
        description=f"{package['neurons']} нейронов для МОЗГИНО",
        payload=payment.payload,
        provider_token="",
        currency="XTR",
        prices=[
            LabeledPrice(
                label=f"{package['neurons']} нейронов", amount=int(package["stars"])
            )
        ],
    )


@router.callback_query(F.data.startswith("buy:"))
async def buy_callback(query: CallbackQuery, bot: Bot) -> None:
    if not query.from_user or not query.message:
        await query.answer()
        return
    package_key = str(query.data).split(":", 1)[1]
    if package_key not in STAR_PACKAGES:
        await query.answer("Пакет не найден", show_alert=True)
        return
    package = STAR_PACKAGES[package_key]
    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.tg_id == query.from_user.id)
        )
        if not user:
            user = User(
                tg_id=query.from_user.id,
                username=query.from_user.username,
                first_name=query.from_user.first_name or "",
            )
            session.add(user)
            await session.flush()
        payment = await add_pending_payment(session, user, package_key)
        await session.commit()
    await bot.send_invoice(
        chat_id=query.message.chat.id,
        title=str(package["title"]),
        description=f"{package['neurons']} нейронов для МОЗГИНО",
        payload=payment.payload,
        provider_token="",
        currency="XTR",
        prices=[
            LabeledPrice(
                label=f"{package['neurons']} нейронов", amount=int(package["stars"])
            )
        ],
    )
    await query.answer()


@router.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery) -> None:
    async with SessionLocal() as session:
        accepted = await validate_pre_checkout(
            session,
            tg_id=query.from_user.id,
            payload=query.invoice_payload,
            currency=query.currency,
            total_amount=query.total_amount,
        )
    await query.answer(
        ok=accepted,
        error_message=None if accepted else "Платёж не найден или сумма изменилась.",
    )


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message) -> None:
    if not message.from_user or not message.successful_payment:
        return
    payment = message.successful_payment
    async with SessionLocal() as session:
        credited = await fulfill_successful_payment(
            session,
            tg_id=message.from_user.id,
            payload=payment.invoice_payload,
            currency=payment.currency,
            total_amount=payment.total_amount,
            telegram_charge_id=payment.telegram_payment_charge_id,
        )
        if credited:
            await session.commit()
        else:
            await session.rollback()
    if credited:
        await message.answer("✨ Оплата подтверждена! Покупка уже добавлена в профиль.")
    else:
        await message.answer("Не удалось подтвердить платёж. Напишите в поддержку.")


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("Set BOT_TOKEN before starting the bot")
    bot = Bot(settings.bot_token)
    dispatcher = create_dispatcher()
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
