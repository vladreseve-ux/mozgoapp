# МОЗГИНО · Telegram Mini App prototype

Прототип игровой Telegram Mini App с виртуальными нейронами, Stars-биллингом, серверными играми и проверяемыми игровыми сидами.

> Баллы в этом прототипе — только виртуальные развлекательные очки: нет вывода, обмена на деньги или денежных выплат. Перед любым публичным запуском нужны проверка правил Telegram, юридическая оценка для целевых стран, антифрод и аудит платежной логики.

## Стек и структура

```text
backend/
  app/
    main.py       FastAPI: auth, billing, games, Replicate endpoint, webhook
    bot.py        Aiogram 3: /start, /buy, pre_checkout_query, successful_payment
    models.py     SQLAlchemy-модели PostgreSQL
    games.py      детерминированная колода, очки, исходы, dice RNG
    security.py   проверка подписи Telegram initData
    billing.py    проверка invoice и идемпотентное начисление Stars
  schema.sql      SQL DDL
  tests/          тесты игровой логики
frontend/
  src/App.tsx     React Mini App
  src/telegram.ts @tma.js/sdk bootstrap и Telegram invoice UI
  src/api.ts      API-клиент
  src/styles.css  адаптивный неон-CSS
docker-compose.yml
```

## Запуск локального демо

Нужны Python 3.11+, Node.js 20.19+ и Docker Compose.

1. Запустить PostgreSQL и Redis из корня проекта:

   ```bash
   docker compose up -d postgres redis
   ```

2. Подготовить backend:

   ```bash
   cd backend
   copy .env.example .env
   # В .env указать BOT_TOKEN (получить у @BotFather).
   py -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   uvicorn app.main:app --reload --port 8000
   ```

   Для локального просмотра без Telegram `BOT_TOKEN` можно оставить пустым: игровые API требуют подлинный `initData`, поэтому серверные ставки вне Telegram отключены.

3. В другом терминале запустить фронтенд:

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

   Открытие `http://localhost:5173` включает визуальный demo mode. В нём баланс и результаты локальные, а Stars-платежи не работают.

## Подключение Telegram

Mini App подключает актуальный пакет `@tma.js/sdk` — официальный successor для прежнего `@telegram-apps/sdk`; используемая точка входа `init()` сохранилась.

1. Настроить у @BotFather Mini App URL и команду `/setmenubutton`; URL должен быть HTTPS.
2. Заполнить `BOT_TOKEN`, `MINI_APP_URL`, `DATABASE_URL`, `REDIS_URL` в `backend/.env`.
3. Запустить API и бота **либо** через polling:

   ```bash
   # В отдельных процессах, из папки backend:
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   python -m app.bot
   ```

   **Либо** настроить Telegram webhook на `POST https://<api-host>/telegram/webhook`, передав тот же `TELEGRAM_WEBHOOK_SECRET` в заголовке `X-Telegram-Bot-Api-Secret-Token`. Не запускайте polling и webhook одновременно для одного бота.
4. Указать публичный адрес API в `frontend/.env.local`:

   ```dotenv
   VITE_API_URL=https://api.example.com
   ```

   Собрать и раздать frontend по HTTPS:

   ```bash
   npm run build
   ```

## Stars flow

- `POST /api/billing/invoice` создаёт внутреннюю запись `pending` и вызывает `createInvoiceLink` с валютой `XTR`.
- Frontend открывает ссылку методом `Telegram.WebApp.openInvoice`.
- Aiogram сверяет payload, владельца, валюту и сумму в `pre_checkout_query`.
- Баланс меняется только после `successful_payment`; Telegram charge ID и уникальный ledger reference защищают от повторного начисления.
- Покупка `AI-аватар` за Stars создаёт генерационный кредит в профиле; сама генерация доступна только после подтверждённой оплаты.
- Команда бота `/buy` демонстрирует отдельный `sendInvoice` flow.
- Сверка платежей, refunds/support и мониторинг необработанных успешных платежей до production не включены.

## Игры и provably fair

- Blackjack выполняется на сервере: ставка списывается до игры, двойная ставка блокируется балансом, туз считается как 1 или 11, обычная выплата — 1:1 net, натуральный blackjack — 3:2 net. Поэтому ставка для blackjack должна быть чётной.
- Dice: точное число имеет gross multiplier 5.7×; чёт/нечёт и диапазоны — 1.9×.
- Колесо: один бесплатный спин на пользователя за 24 часа.
- Перед началом frontend отдельно получает SHA-256 commitment от UTF-8 строки hex-сиды. После завершения сервер раскрывает seed; колода использует Fisher–Yates и детерминированный HMAC-SHA256 stream (ключ — декодированные байты server seed) с rejection sampling.
- Для проверки результата: `SHA256(server_seed_hex.encode("utf-8"))` должен совпасть с commitment; следующий stream-блок равен `HMAC-SHA256(bytes.fromhex(server_seed_hex), f"{client_seed}:{nonce}:{counter}".encode())`, где `counter` начинается с 0. `randbelow(n)` отбрасывает блоки выше наибольшей кратной `n` границы, чтобы избежать modulo bias; shuffle — Fisher–Yates.
- Redis хранит ожидающие seed commitments, кэш активных сессий blackjack и блокировки действий; полное состояние незавершённого blackjack также сохраняется в PostgreSQL, откуда API восстанавливает Redis-кэш. Для production всё равно нужны сценарии автоматической компенсации при длительном простое/ручной отмене игры.

Перед production также нужны Alembic-миграции, rate limiting, CSRF/origin policy для API, журналирование/алерты, бэкапы, ротация секретов, шифрование активных server seed в PostgreSQL, тесты интеграции с Telegram и независимый аудит fairness.

## AI Canvas / Replicate

В `backend/.env` задать `REPLICATE_API_TOKEN` и при необходимости `REPLICATE_MODEL`. Эндпоинт:

```http
POST /api/ai/trophy
X-Telegram-Init-Data: <raw initData>
Content-Type: application/json

{"game_id":"<uuid завершённой blackjack-игры>"}
```

Трофей: `POST /api/ai/trophy` сам строит prompt и разрешает генерацию только для крупной чистой победы. Аватар: `POST /api/ai/avatar` расходует один кредит, выданный после оплаты AI-аватара Telegram Stars. Оба метода возвращают prediction ID, status и доступный image URL; `GET /api/ai/assets/{prediction_id}` обновляет статус завершения. В прототипе профиль хранит Replicate URL; перед production копируйте изображение в своё постоянное объектное хранилище.

## Тесты

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```
