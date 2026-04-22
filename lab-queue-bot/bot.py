import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
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
from config import settings, is_admin

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
users: dict[int, str] = {}


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


def get_queue_view(queue: Queue, users: dict[int, str]) -> str:
    lines = [f"📋 {queue.name}\n"]
    for i in range(1, queue.max_places + 1):
        if i in queue.places:
            uid = queue.places[i]
            name = users.get(uid, f"@{uid}")
            lines.append(f"  {i}. ✅ {name}")
        else:
            lines.append(f"  {i}. ◻ Свободно")
    return "\n".join(lines)


async def show_main_menu(callback, user_id: int = None):
    """Показать главное меню, удалив предыдущее сообщение"""
    if user_id is None:
        user_id = callback.from_user.id

    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(text="📋 Очереди", callback_data="menu_queues"),
        InlineKeyboardButton(text="📝 Мои записи", callback_data="menu_myqueues"),
    )
    builder.add(
        InlineKeyboardButton(text="🔄 Обмен с другим", callback_data="menu_swap"),
        InlineKeyboardButton(text="📍 Сменить место", callback_data="menu_freeswap"),
    )
    if is_admin(user_id):
        builder.add(
            InlineKeyboardButton(text="⚙️ Создать очередь", callback_data="menu_create"),
        )
    builder.adjust(2)

    await callback.message.delete()
    await callback.message.answer(
        "👋 Бот для управления очередями лабораторных работ.\n\n"
        "Выберите действие:",
        reply_markup=builder.as_markup(),
    )


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Главное меню"""
    # Сохраняем имя пользователя
    if message.from_user.username:
        users[message.from_user.id] = f"@{message.from_user.username}"
    elif message.from_user.first_name:
        users[message.from_user.id] = message.from_user.first_name
    else:
        users[message.from_user.id] = str(message.from_user.id)

    await show_main_menu(callback)


@router.callback_query(lambda c: c.data == "menu_queues")
async def menu_queues(callback: CallbackQuery, state: FSMContext):
    """Показать список всех очередей"""
    if not queues:
        await callback.message.edit_text("📭 Нет активных очередей.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀ Назад", callback_data="back_to_menu"),
            ]]))
        return

    builder = InlineKeyboardBuilder()
    for queue_id, queue in queues.items():
        builder.add(
            InlineKeyboardButton(
                text=queue.name,
                callback_data=f"queue_view_{queue_id}",
            )
        )
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(text="◀ Назад", callback_data="back_to_menu"),
    )

    await callback.message.edit_text(
        "📋 Выберите очередь:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(lambda c: c.data == "menu_myqueues")
async def menu_myqueues(callback: CallbackQuery, state: FSMContext):
    """Показать очереди пользователя"""
    user_id = callback.from_user.id
    user_queue_ids = [(qid, q) for qid, q in queues.items() if user_id in q.places.values()]

    if not user_queue_ids:
        await callback.message.edit_text("📭 Вы не записаны ни в одну очередь.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀ Назад", callback_data="back_to_menu"),
            ]]))
        return

    lines = ["📋 Ваши записи:\n"]
    for qid, queue in user_queue_ids:
        place = [p for p, uid in queue.places.items() if uid == user_id][0]
        lines.append(f"  • {queue.name} — место {place}")

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="◀ Назад", callback_data="back_to_menu"),
    )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=builder.as_markup(),
    )


@router.callback_query(lambda c: c.data == "menu_swap")
async def menu_swap(callback: CallbackQuery, state: FSMContext):
    """Запрос на обмен местами"""
    user_id = callback.from_user.id
    user_queue_ids = [(qid, q) for qid, q in queues.items() if user_id in q.places.values()]

    if not user_queue_ids:
        await callback.message.edit_text("❌ Сначала запишитесь в очередь!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀ Назад", callback_data="back_to_menu"),
            ]]))
        return

    if len(user_queue_ids) == 1:
        queue_id, queue = user_queue_ids[0]
        await show_swap_targets(callback.message, queue_id, queue, user_id)
        return

    builder = InlineKeyboardBuilder()
    for qid, queue in user_queue_ids:
        builder.add(
            InlineKeyboardButton(text=queue.name, callback_data=f"swap_q_{qid}"),
        )
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(text="◀ Назад", callback_data="back_to_menu"),
    )

    await callback.message.edit_text(
        "📋 Выберите очередь:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(lambda c: c.data == "menu_freeswap")
async def menu_freeswap(callback: CallbackQuery, state: FSMContext):
    """Сменить место на свободное"""
    user_id = callback.from_user.id
    user_queue_ids = [(qid, q) for qid, q in queues.items() if user_id in q.places.values()]

    if not user_queue_ids:
        await callback.message.edit_text("❌ Сначала запишитесь в очередь!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="◀ Назад", callback_data="back_to_menu"),
            ]]))
        return

    if len(user_queue_ids) == 1:
        queue_id, queue = user_queue_ids[0]
        await show_free_swap_targets(callback.message, queue_id, queue, user_id)
        return

    builder = InlineKeyboardBuilder()
    for qid, queue in user_queue_ids:
        builder.add(
            InlineKeyboardButton(text=queue.name, callback_data=f"free_q_{qid}"),
        )
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(text="◀ Назад", callback_data="back_to_menu"),
    )

    await callback.message.edit_text(
        "📋 Выберите очередь:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(lambda c: c.data == "menu_create")
async def menu_create(callback: CallbackQuery, state: FSMContext):
    """Создать очередь (только админ)"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Только для админов!", show_alert=True)
        return

    await state.set_state(QueueStates.waiting_for_queue_name)
    await callback.message.edit_text(
        "📝 Введите название очереди (до 30 мест):\n"
        "Формат: <название> <количество_мест>\n"
        "Пример: Лаба1 15",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="◀ Назад", callback_data="back_to_menu"),
            ]]
        ),
    )


@router.callback_query(lambda c: c.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    """Вернуться в главное меню"""
    await show_main_menu(callback)


@router.callback_query(lambda c: c.data and c.data.startswith("queue_view_"))
async def view_queue(callback: CallbackQuery, state: FSMContext):
    queue_id = int(callback.data.split("_")[-1])
    queue = queues[queue_id]

    text = get_queue_view(queue, users)
    builder = InlineKeyboardBuilder()
    for i in range(1, queue.max_places + 1):
        if i in queue.places:
            builder.add(
                InlineKeyboardButton(
                    text=f"[{i}] Занято",
                    callback_data=f"take_{queue_id}_{i}",
                )
            )
        else:
            builder.add(
                InlineKeyboardButton(
                    text=f"{i}",
                    callback_data=f"take_{queue_id}_{i}",
                )
            )
    builder.adjust(5)
    builder.row(
        InlineKeyboardButton(
            text="◀ Назад",
            callback_data="back_to_queues",
        )
    )

    await callback.message.edit_text(text, reply_markup=builder.as_markup())


@router.callback_query(lambda c: c.data == "back_to_queues")
async def back_to_queues(callback: CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    for queue_id, queue in queues.items():
        builder.add(
            InlineKeyboardButton(
                text=queue.name,
                callback_data=f"queue_view_{queue_id}",
            )
        )
    builder.adjust(2)

    await callback.message.edit_text(
        "📋 Выберите очередь:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("take_"))
async def take_place(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    parts = callback.data.split("_")
    queue_id = int(parts[-2])
    place = int(parts[-1])

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

    text = get_queue_view(queue, users)
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
                    callback_data=f"take_{queue_id}_{i}",
                )
            )
    builder.adjust(5)
    builder.row(
        InlineKeyboardButton(
            text="◀ Назад к списку",
            callback_data="back_to_queues",
        )
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer(f"✅ Вы записаны на место {place}!")


async def show_swap_targets(message: Message, queue_id: int, queue: Queue, user_id: int):
    """Показать места для обмена"""
    user_place = None
    for p, uid in queue.places.items():
        if uid == user_id:
            user_place = p
            break
    
    if user_place is None:
        await message.answer("❌ Вы не записаны в эту очередь!")
        return

    builder = InlineKeyboardBuilder()
    for place, uid in queue.places.items():
        if uid != user_id:
            name = users.get(uid, f"@{uid}")
            builder.add(
                InlineKeyboardButton(
                    text=f"Место {place} ({name})",
                    callback_data=f"swap_init_{queue_id}_{place}",
                )
            )
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(text="◀ Назад", callback_data="back_to_menu"),
    )

    if not builder.buttons:
        await message.answer("❌ Больше нет доступных мест для обмена.")
        return

    await message.answer(
        "🔄 Выберите пользователя для обмена местами:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("swap_q_"))
async def select_swap_queue(callback: CallbackQuery, state: FSMContext):
    queue_id = int(callback.data.split("_")[-1])
    queue = queues[queue_id]
    user_id = callback.from_user.id

    if user_id not in queue.places.values():
        await callback.answer("❌ Вы не записаны в эту очередь!", show_alert=True)
        return

    await show_swap_targets(callback.message, queue_id, queue, user_id)


@router.message(Command("free_swap"))
async def cmd_free_swap(message: Message):
    user_id = message.from_user.id

    user_queue_ids = [
        (queue_id, queue)
        for queue_id, queue in queues.items()
        if user_id in queue.places.values()
    ]

    if not user_queue_ids:
        await message.answer("❌ Сначала запишитесь в очередь!")
        return

    if len(user_queue_ids) == 1:
        queue_id, queue = user_queue_ids[0]
        await show_free_swap_targets(message, queue_id, queue, message.from_user.id)
        return

    builder = InlineKeyboardBuilder()
    for queue_id, queue in user_queue_ids:
        builder.add(
            InlineKeyboardButton(
                text=queue.name,
                callback_data=f"free_q_{queue_id}",
            )
        )
    builder.adjust(2)

    await message.answer(
        "📋 Выберите очередь:",
        reply_markup=builder.as_markup(),
    )


async def show_free_swap_targets(message: Message, queue_id: int, queue: Queue, user_id: int):
    user_place = None
    for p, uid in queue.places.items():
        if uid == user_id:
            user_place = p
            break
    
    if user_place is None:
        await message.answer("❌ Вы не записаны в эту очередь!")
        return

    builder = InlineKeyboardBuilder()
    for i in range(1, queue.max_places + 1):
        if i not in queue.places:
            builder.add(
                InlineKeyboardButton(
                    text=f"Место {i}",
                    callback_data=f"free_swap_{queue_id}_{user_place}_{i}",
                )
            )
    builder.adjust(5)
    builder.row(
        InlineKeyboardButton(text="◀ Назад", callback_data="back_to_menu"),
    )

    if not builder.buttons:
        await message.answer("❌ Нет свободных мест.")
        return

    await message.answer(
        "🔄 Выберите свободное место:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("free_q_"))
async def select_free_queue(callback: CallbackQuery, state: FSMContext):
    queue_id = int(callback.data.split("_")[-1])
    queue = queues[queue_id]
    user_id = callback.from_user.id

    if user_id not in queue.places.values():
        await callback.answer("❌ Вы не записаны в эту очередь!", show_alert=True)
        return

    await show_free_swap_targets(callback.message, queue_id, queue, user_id)


@router.callback_query(lambda c: c.data and c.data.startswith("swap_init_"))
async def init_swap(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    queue_id = int(parts[2])
    target_place = int(parts[3])

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
        f"🔄 Запрос на обмен местами!\n\n"
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


@router.callback_query(lambda c: c.data and c.data.startswith("swap_accept_"))
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


@router.callback_query(lambda c: c.data and c.data.startswith("free_swap_"))
async def free_swap(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    queue_id = int(parts[2])
    old_place = int(parts[3])
    new_place = int(parts[4])

    queue = queues[queue_id]

    if new_place in queue.places:
        await callback.answer("❌ Место уже занято!", show_alert=True)
        return

    user_id = callback.from_user.id

    del queue.places[old_place]
    queue.places[new_place] = user_id

    text = get_queue_view(queue, users)
    keyboard = get_queue_keyboard(queue)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer(f"✅ Вы перешли на место {new_place}!")


@router.callback_query(lambda c: c.data and c.data.startswith("swap_decline_"))
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


@router.message(QueueStates.waiting_for_queue_name)
async def process_queue_creation(message: Message, state: FSMContext):
    """Обработка создания очереди"""
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            await message.answer("❌ Неверный формат! Пример: Лаба1 15")
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
            f"✅ Очередь {name} создана!\n📊 Максимум мест: {max_places}",
        )

    except ValueError:
        await state.clear()
        await message.answer("❌ Неверный формат! Пример: Лаба1 15")


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