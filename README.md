# Lab Queue Bot

Telegram-бот для управления очередью лабораторных работ.

## Функциональность
- **Управление очередью** - создание и управление очередями (до 30 человек)
- **Уведомления** - бот уведомляет студентов о подходящей очереди
- Автоматическое управление статусами очереди
- Интеграция с Telegram API

## Установка

`ash
pip install -r lab-queue-bot/requirements.txt
``n
## Настройка

1. Создайте бота через [@BotFather](https://t.me/BotFather)
2. Создайте .env файл:

`env
BOT_TOKEN=your_bot_token
ADMIN_GROUP_ID=your_group_id
``n
3. Запустите бота:

`ash
python lab-queue-bot/bot.py
``n
## Доступные команды

| Команда | Описание |
|---------|----------|
| /start | Регистрация пользователя |
| /create_queue | Создание очереди (админ) |
| /queues | Список очередей |
| /myqueues | Мои очереди |
| /submit_lab | Сдать лабораторную работу |
| /swap_request | Запрос на обмен |

## Выполненная работа
- Реализован на базе aiogram 3.x
- Использование FSM (Finite State Machine) для управления состояниями
- Хранение данных в памяти (InMemoryStorage)
- Обработка команд и callback-запросов

## Технологии
- Python 3
- Aiogram 3.x
- Telegram Bot API
- python-dotenv