import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, Text
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()


class QueueStates(StatesGroup):
    waiting_for_queue_name = State()
    waiting_for_admin_confirmation = State()


@dataclass
class Queue:
    id: int
    name: str
    max_places: int
    places: dict[int, int] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class SwapRequest:
    id: int
    queue_id: int
    from_user_id: int
    to_user_id: int
    from_place: int
    to_place: int
    status: str = "pending"


queues: dict[int, Queue] = {}
swap_requests: dict[int, SwapRequest] = {}
user_queues: dict[int, int] = {}
swap_counter = 0


def get_queue_keyboard(queue: Queue) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i in range(1, queue.max_places + 1):
        if i in queue.places:
            builder.add(
                InlineKeyboardButton(
                    text=f"[{i}] Занято",
                    callback_data=f"locked_{i}",
                )
            )
        else:
            builder.add(
                InlineKeyboardButton(
                    text=f"{i}",
                    callback_data=f"take_{queue.id}_{i}",
                )
            )
    builder.adjust(5)
    return builder.as_markup()


def get_queue_view(queue: Queue) -> str:
    lines = [f"📋 <b>{queue.name}</b>\n"]
    for i in range(1, queue.max_places + 1):
        if i in queue.places:
            lines.append(f"  {i}. ✅ Занято")
        else:
            lines.append(f"  {i}. ◻ Свободно")
    return "\n".join(lines)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await message.answer(
        "👋 Бот для управления очередями лабораторных работ.\n\n"
        "📌 <b>Админы группы:</b>\n"
        "/create_queue - создать новую очередь\n\n"
        "📌 <b>Для всех:</b>\n"
        "/queues - список активных очередей\n"
        "/myqueues - ваши очереди\n"
        "/swap_request - предложить обмен местами",
    )


@router.message(Command("create_queue"))
async def cmd_create_queue(message: Message, state: FSMContext):
    if message.chat.id != settings.ADMIN_GROUP_ID:
        await message.answer("❌ Эта команда только для админов группы!")
        return

    await state.set_state(QueueStates.waiting_for_queue_name)
    await message.answer(
        "📝 Введите название очереди (до 30 мест):\n"
        "Формат: <название> <количество_мест>\n"
        "Пример: Лаба 1 15"
    )


@router.message(QueueStates.waiting_for_queue_name)
async def process_queue_creation(message: Message, state: FSMContext):
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            await message.answer("❌ Неверный формат! Пример: Лаба 1 15")
            return

        name = parts[0]
        max_places = int(parts[1])

        if max_places < 1 or max_places > 30:
            await message.answer("❌ Количество мест: 1-30")
            return

        queue_id = len(queues) + 1
        queue = Queue(id=queue_id, name=name, max_places=max_places)
        queues[queue_id] = queue

        await state.clear()
        await message.answer(
            f"✅ Очередь <b>{name}</b> создана!\n"
            f"📊 Максимум мест: {max_places}\n\n"
            f"/queues - посмотреть очереди",
        )

    except ValueError:
        await message.answer("❌ Неверный формат! Пример: Лаба 1 15")


@router.message(Command("queues"))
async def cmd_queues(message: Message):
    if not queues:
        await message.answer("📭 Нет активных очередей.")
        return

    for queue in queues.values():
        text = get_queue_view(queue)
        keyboard = get_queue_keyboard(queue)
        await message.answer(text, reply_markup=keyboard)


@router.message(Command("myqueues"))
async def cmd_myqueues(message: Message):
    user_id = message.from_user.id
    user_queue_ids = [qid for qid, q in queues.items() if user_id in q.places.values()]

    if not user_queue_ids:
        await message.answer("📭 Вы не записаны ни в одну очередь.")
        return

    lines = ["📋 Ваши записи:\n"]
    for qid in user_queue_ids:
        queue = queues[qid]
        place = [p for p, uid in queue.places.items() if uid == user_id][0]
        lines.append(f"  • {queue.name} — место {place}")

    await message.answer("\n".join(lines))


@router.callback_query(Text(startswith="take_"))
async def take_place(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    _, queue_id_str, place_str = callback.data.split("_")
    queue_id = int(queue_id_str)
    place = int(place_str)

    if queue_id not in queues:
        await callback.answer("❌ Очередь не найдена!", show_alert=True)
        return

    queue = queues[queue_id]

    if user_id in queue.places.values():
        await callback.answer("❌ Вы уже записаны в эту очередь!", show_alert=True)
        return

    if place in queue.places:
        await callback.answer("❌ Это место уже занято!", show_alert=True)
        return

    queue.places[place] = user_id
    user_queues[user_id] = queue_id

    await callback.message.edit_reply_markup(
        reply_markup=get_queue_keyboard(queue)
    )
    await callback.answer(f"✅ Вы записаны на место {place}!")


@router.message(Command("submit_lab"))
async def cmd_submit_lab(message: Message):
    user_id = message.from_user.id

    for queue_id, queue in queues.items():
        if user_id in queue.places.values():
            place = [p for p, uid in queue.places.items() if uid == user_id][0]
            del queue.places[place]

            if user_id in user_queues:
                del user_queues[user_id]

            await message.answer(
                f"✅ Вы отмечены как сдавший лабу!\n"
                f"📋 {queue.name}, место {place} освобождено."
            )
            return

    await message.answer("❌ Вы не записаны ни в одну очередь.")


@router.message(Command("swap_request"))
async def cmd_swap_request(message: Message):
    user_id = message.from_user.id

    user_queue_id = None
    user_place = None
    for queue_id, queue in queues.items():
        if user_id in queue.places.values():
            user_place = [p for p, uid in queue.places.items() if uid == user_id][0]
            user_queue_id = queue_id
            break

    if not user_queue_id:
        await message.answer("❌ Сначала запишитесь в очередь!")
        return

    queue = queues[user_queue_id]

    builder = InlineKeyboardBuilder()
    for place, uid in queue.places.items():
        if uid != user_id:
            builder.add(
                InlineKeyboardButton(
                    text=f"Место {place}",
                    callback_data=f"swap_init_{user_queue_id}_{place}",
                )
            )
    builder.adjust(2)

    if not builder.buttons:
        await message.answer("❌ Больше нет доступных мест для обмена.")
        return

    await message.answer(
        "🔄 Выберите пользователя для обмена местами:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(Text(startswith="swap_init_"))
async def init_swap(callback: CallbackQuery, state: FSMContext):
    _, _, queue_id_str, target_place_str = callback.data.split("_")
    queue_id = int(queue_id_str)
    target_place = int(target_place_str)

    from_user_id = callback.from_user.id
    queue = queues[queue_id]

    if target_place not in queue.places:
        await callback.answer("❌ Место не найдено!", show_alert=True)
        return

    to_user_id = queue.places[target_place]

    global swap_counter
    swap_counter += 1
    req_id = swap_counter

    swap_requests[req_id] = SwapRequest(
        id=req_id,
        queue_id=queue_id,
        from_user_id=from_user_id,
        to_user_id=to_user_id,
        from_place=queue.places[from_user_id],
        to_place=target_place,
    )

    await callback.bot.send_message(
        to_user_id,
        f"🔄 <b>Запрос на обмен местами!</b>\n\n"
        f"Пользователь хочет поменяться с вами местами.\n"
        f"📋 Очередь: {queue.name}\n"
        f"🔄 Его место: {swap_requests[req_id].from_place} → Ваше место: {target_place}\n\n"
        "Нажмите кнопку для согласия:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Согласен",
                        callback_data=f"swap_accept_{req_id}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Отказать",
                        callback_data=f"swap_decline_{req_id}",
                    ),
                ]
            ]
        ),
    )

    await callback.answer("✅ Запрос отправлен!")


@router.callback_query(Text(startswith="swap_accept_"))
async def accept_swap(callback: CallbackQuery, state: FSMContext):
    _, req_id_str = callback.data.split("_")
    req_id = int(req_id_str)

    if req_id not in swap_requests:
        await callback.answer("❌ Запрос не найден!", show_alert=True)
        return

    req = swap_requests[req_id]
    if req.to_user_id != callback.from_user.id:
        await callback.answer("❌ Это не для вас!", show_alert=True)
        return

    if req.status != "pending":
        await callback.answer("❌ Запрос уже обработан!", show_alert=True)
        return

    queue = queues[req.queue_id]
    queue.places[req.from_place] = req.to_user_id
    queue.places[req.to_place] = req.from_user_id
    req.status = "accepted"

    await callback.message.edit_text(
        "✅ Обмен местами согласован!",
    )
    await callback.bot.send_message(
        req.from_user_id,
        f"✅ Пользователь согласился на обмен!\n"
        f"📋 {queue.name}: места {req.from_place} и {req.to_place} обменяны.",
    )


@router.callback_query(Text(startswith="swap_decline_"))
async def decline_swap(callback: CallbackQuery, state: FSMContext):
    _, req_id_str = callback.data.split("_")
    req_id = int(req_id_str)

    if req_id not in swap_requests:
        await callback.answer("❌ Запрос не найден!", show_alert=True)
        return

    req = swap_requests[req_id]
    if req.to_user_id != callback.from_user.id:
        await callback.answer("❌ Это не для вас!", show_alert=True)
        return

    req.status = "declined"

    await callback.message.edit_text(
        "❌ Обмен отклонён.",
    )
    await callback.bot.send_message(
        req.from_user_id,
        "❌ Пользователь отклонил запрос на обмен.",
    )


async def main():
    load_dotenv()

    if not settings.BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен!")
        return

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await dp.start_polling(bot, commands={"start"})


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(main())