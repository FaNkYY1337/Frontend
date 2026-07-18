# Telegram Gifts Tracker

Telegram-бот для мониторинга resale-листингов Telegram Gifts, подписок, приватного чата, рефералов, Telegram Stars, CryptoBot и админ-панели.

## Запуск

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m app.main
```

## Что заполнить в `.env`

`BOT_TOKEN` — токен бота из BotFather.

`ADMIN_IDS` — Telegram ID админов через запятую.

`PRIVATE_CHAT_ID` — ID приватного чата или канала, куда бот выдаёт одноразовые инвайты подписчикам.

`LISTINGS_CHAT_ID` — ID чата или канала, куда бот отправляет найденные лоты.

`TELEGRAM_API_ID` и `TELEGRAM_API_HASH` — ключи приложения Telegram для MTProto-сессий.

`SESSION_SECRET` — ключ шифрования сессий. Сгенерировать:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`DATABASE_URL` — путь к базе. По умолчанию используется SQLite `bot.db`.

`TIMEZONE` — часовой пояс для отображения дат.

`STAR_TO_TON_RATE` — курс для пересчёта Stars в TON в карточках лотов.

`GIFT_POLL_INTERVAL_SECONDS` — пауза между проверками рынка.

`GIFT_RESALE_LIMIT_PER_GIFT` — сколько resale-лотов брать по одному подарку.

`GIFT_BASE_LIMIT` и `GIFT_MAX_LISTINGS_PER_POLL` — лимиты загрузки лотов.

`GIFT_CHECK_CONTACT_REQUIREMENTS` — проверять условия связи с продавцом через Telegram.

`GIFT_REQUIRE_SELLER_USERNAME` — отправлять только лоты с username продавца.

`GIFT_RU_ONLY` и `GIFT_RU_MIN_SCORE` — RU-фильтр аудитории.

`LISTING_SEND_DELAY_SECONDS` — задержка между отправкой лотов.

`INVITE_TTL_MINUTES` — срок жизни одноразовой ссылки в приват.

`TRIAL_HOURS` — длительность бесплатного триала.

`SUPPORT_USERNAME` — username поддержки без `@`.

`CREATOR_USERNAME` — username для строки `Creator By @...` в карточке лота.

`CRYPTOBOT_TOKEN` — токен CryptoBot. Если пустой, CryptoBot-оплата будет выключена.

`CRYPTOBOT_API_URL` — API CryptoBot.

## Админ-панель

Команда `/admin` открывает управление:

- запуск и остановка парсера;
- фильтр Level;
- фильтр цены;
- фильтр сообщений продавцу;
- пользователи;
- сессии;
- статистика;
- тарифы;
- рассылка.

## Сессии Telegram

Для мониторинга resale-рынка нужны MTProto user sessions. Добавление:

1. Открыть `/admin`.
2. Нажать `Сессии`.
3. Выбрать `Добавить по телефону` или `Добавить StringSession`.
4. Следовать сообщениям бота.

Сессии хранятся в базе в зашифрованном виде.

## Подписки и доступ

После триала или оплаты бот создаёт индивидуальную ссылку в приватный чат с `member_limit=1`. После входа пользователя сообщение со ссылкой редактируется, а ссылка исчезает из переписки.

Когда подписка заканчивается, бот удаляет пользователя из приватного чата и отправляет сообщение о продлении.

## Лоты

При нажатии `Занять` бот отправляет полный пост в личку пользователю, а в общем чате заменяет лог на короткий статус `Данный лог занят` и `Взято: @user`.

Для пользователей без активной подписки действует cooldown 5 секунд. Для платных пользователей cooldown нет.
