# Telegram Bot (aiogram v3)

Минимальный каркас большого бота на aiogram 3.

## Быстрый старт

1. Установите зависимости:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Создайте файл `.env` (можно скопировать из `.env.example`) и укажите `BOT_TOKEN`.

3. Запустите бота:

```bash
python main.py
```

## Структура

```
.
├── bot
│   ├── __init__.py
│   ├── config.py        # загрузка конфигурации из окружения
│   └── handlers.py      # маршрутизатор и базовые хендлеры
├── main.py              # точка входа, запуск диспетчера
├── requirements.txt
├── .env.example
└── README.md
```

## Как расширять
- Создавайте отдельные модули и роутеры (например, `bot/features/...`) и подключайте их в `main.py` через `dp.include_router(...)`.
- Для состояний используйте `FSMContext` и `MemoryStorage`/БД при необходимости.
- Для фоновых задач — `asyncio.create_task` или внешние воркеры.