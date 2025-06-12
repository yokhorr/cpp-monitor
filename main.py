import requests
import csv
import io
import asyncio
import logging
import os
import json
import subprocess
import re
from typing import List, Dict, Optional, Set, Tuple, Any
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties

# ====== CONFIGURATION ======
BOT_TOKEN: str = open('bot_token.txt').read().strip()
SPREADSHEET_ID: str = "1PlQVDjbfnTrUBmgltN2JwDnq3ZUjs8l4ei_MkaGzL1A"
SHEET_GIDS: List[str] = ["730603969", "928911897"]
FETCH_INTERVAL: int = 10
ENTRIES_FILE: str = "entries.json"
IMAGE_PATH: str = "/home/yokhor/Pictures/moth.png"
PERIODIC_NOTIFY_INTERVAL: int = 30
# ===========================

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s:%(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

bot: Bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="MarkdownV2")
)
dp: Dispatcher = Dispatcher()
monitoring_task: Optional[asyncio.Task] = None
notify_task: Optional[asyncio.Task] = None
waiting_for_entry: Set[int] = set()
waiting_for_delete: Set[int] = set()
notified_on_review: Set[str] = set()


def escape_md(text: str) -> str:
    """
    Escape special characters for MarkdownV2 Telegram format.
    """
    escape_chars = r'_*\[\]()~`>#+-=|{}.!'
    return re.sub(r'([%s])' % re.escape(escape_chars), r'\\\1', text)


def monospace_block(text: str) -> str:
    """
    Return string formatted as code block for MarkdownV2.
    Does not escape content inside the block!
    """
    return f"```\n{text}\n```"


def get_gsheet_csv(spreadsheet_id: str, sheet_gid: str) -> List[Dict[str, str]]:
    """
    Fetch CSV data from Google Sheets and return as list of dictionaries.
    """
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={sheet_gid}"
    resp = requests.get(url)
    resp.raise_for_status()
    f = io.StringIO(resp.content.decode('utf-8'))
    return list(csv.DictReader(f))


def find_entry(
        data: List[Dict[str, str]],
        timestamp: str,
        name: str,
        task: str
) -> Optional[Dict[str, str]]:
    """
    Find specific entry in the data by timestamp, name and task.
    """
    for row in data:
        if (
                row.get("Метка времени") == timestamp
                and row.get("ФИО") == name
                and row.get("Задание") == task
        ):
            return row
    return None


def check_entry_status(
        timestamp: str,
        name: str,
        task: str
) -> Tuple[str, Optional[Dict[str, str]]]:
    """
    Check entry status across all sheets.
    Returns tuple of (status, row_data).
    """
    for gid in SHEET_GIDS:
        try:
            data = get_gsheet_csv(SPREADSHEET_ID, gid)
            row = find_entry(data, timestamp, name, task)
            if row:
                if row.get("Оценка"):
                    return "checked", row
                if row.get("Проверяющий") and not row.get("Оценка"):
                    return "on_review", row
                return "exists", row
        except Exception as e:
            logger.error(f"Error checking entry in GID {gid}: {e}")
    return "not_found", None


def load_entries() -> List[Dict[str, str]]:
    """
    Load entries from JSON file.
    """
    if os.path.exists(ENTRIES_FILE):
        with open(ENTRIES_FILE, encoding='utf-8') as f:
            try:
                return json.load(f)
            except Exception as e:
                logger.error(f"JSON read error: {e}")
                return []
    return []


def save_entries(entries: List[Dict[str, str]]) -> None:
    """
    Save entries to JSON file.
    """
    with open(ENTRIES_FILE, "w", encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def open_image() -> None:
    """
    Open image using system default application.
    """
    try:
        subprocess.Popen(['xdg-open', IMAGE_PATH])
        logger.info("Image opened via xdg-open")
    except Exception as e:
        logger.error(f"Failed to open image: {e}")


async def periodic_notify(user_id: int, text: str) -> None:
    """
    Send periodic notifications with configured interval.
    """
    while True:
        try:
            await bot.send_message(user_id, escape_md(text))
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
        await asyncio.sleep(PERIODIC_NOTIFY_INTERVAL)


async def monitor_gsheet(user_id: int) -> None:
    """
    Monitor Google Sheets for entry status changes.
    """
    global notify_task
    notified_on_review.clear()
    logger.info("Monitoring started")
    while True:
        entries = load_entries()
        if not entries:
            logger.info("No entries to monitor")
            await asyncio.sleep(FETCH_INTERVAL)
            continue
        for gid in SHEET_GIDS:
            try:
                data = get_gsheet_csv(SPREADSHEET_ID, gid)
                for entry in entries:
                    timestamp, name, task = entry["timestamp"], entry["name"], entry["task"]
                    row = find_entry(data, timestamp, name, task)
                    if not row:
                        continue

                    # On review
                    if row.get("Проверяющий") and not row.get("Оценка"):
                        entry_id = f"{timestamp}|{name}|{task}"
                        if entry_id not in notified_on_review:
                            notified_on_review.add(entry_id)
                            message_text = f"Посылка `{escape_md(task)}` для `{escape_md(name)}` на проверке{escape_md('!')}"
                            await bot.send_message(
                                user_id,
                                message_text,
                                parse_mode="MarkdownV2"
                            )
                            open_image()
                            logger.info("Sent 'На проверке' notification and opened image.")

                    # Checked
                    if row.get("Оценка"):
                        message_text = f"Посылка `{escape_md(task)}` для `{escape_md(name)}` проверена{escape_md('!')} Мониторинг остановлен{escape_md('.')}"
                        await bot.send_message(
                            user_id,
                            message_text,
                            parse_mode="MarkdownV2"
                        )
                        open_image()
                        logger.info("Task checked - stopping monitoring and starting periodic notification.")
                        if monitoring_task:
                            monitoring_task.cancel()
                        if notify_task:
                            notify_task.cancel()
                        notify_task = asyncio.create_task(periodic_notify(
                            user_id, f"Посылка '{task}' для {name} проверена!"
                        ))
                        return
            except Exception as e:
                logger.error(f"Error fetching/searching GID {gid}: {e}")
        await asyncio.sleep(FETCH_INTERVAL)


@dp.message(Command("start"))
async def start_command(message: types.Message) -> None:
    """
    Handle /start command by showing help.
    """
    await help_command(message)


@dp.message(Command("monitor"))
async def start_monitoring(message: types.Message) -> None:
    """
    Handle /monitor command to start monitoring.
    """
    global monitoring_task
    user_id = message.from_user.id
    if not monitoring_task or monitoring_task.done():
        monitoring_task = asyncio.create_task(monitor_gsheet(user_id))
        await message.answer(escape_md("Мониторинг запущен!"))
        logger.info(f"Monitoring started by user {user_id}")
    else:
        await message.answer(escape_md("Мониторинг уже работает."))


@dp.message(Command("stop"))
async def stop_monitoring(message: types.Message) -> None:
    """
    Handle /stop command to stop monitoring and notifications.
    """
    global monitoring_task, notify_task
    if monitoring_task and not monitoring_task.done():
        monitoring_task.cancel()
        monitoring_task = None
    if notify_task and not notify_task.done():
        notify_task.cancel()
        notify_task = None
    await message.answer(escape_md("Мониторинг и уведомления остановлены."))
    logger.info(f"Monitoring and notifications stopped by user {message.from_user.id}")


@dp.message(Command("addentry"))
async def add_entry_command(message: types.Message) -> None:
    """
    Handle /addentry command to add new entry for monitoring.
    """
    user_id = message.from_user.id
    waiting_for_entry.add(user_id)
    await message.answer(escape_md("Отправьте ровно три строки: timestamp, name, task.\nПример:"))
    example_block = monospace_block(
        "25.05.2025 18:39:30\n"
        "Соляник Егор Юрьевич\n"
        "socow-vector"
    )
    await message.answer(example_block)


@dp.message(Command("listentries"))
async def list_entries(message: types.Message) -> None:
    """
    Handle /listentries command to show all monitored entries.
    """
    entries = load_entries()
    if not entries:
        await message.answer(escape_md("Нет отслеживаемых посылок."))
        return
    await message.answer(escape_md("Текущие отслеживаемые посылки:"))

    # Collect all code blocks in one message
    blocks = []
    for entry in entries:
        blocks.append(monospace_block(f"{entry['timestamp']}\n{entry['name']}\n{entry['task']}"))

    # Combine all blocks into one message
    combined_message = "\n".join(blocks)
    await message.answer(combined_message)


@dp.message(Command("help"))
async def help_command(message: types.Message) -> None:
    """
    Handle /help command to show available commands.
    """
    text = (
        "cpp-ct monitor bot\n\n"
        "Доступные команды:\n"
        "/monitor — Запустить мониторинг\n"
        "/stop — Остановить мониторинг и уведомления\n"
        "/addentry — Добавить новую посылку для мониторинга\n"
        "/delentry — Удалить посылку из отслеживаемых\n"
        "/listentries — Показать все текущие отслеживаемые посылки\n"
        "/info — Информация о боте\n"
        "/help — Показать эту справку"
    )
    await message.answer(escape_md(text))


@dp.message(Command("info"))
async def info_command(message: types.Message) -> None:
    """
    Handle /info command to show bot information.
    """
    info_text = (
        "cpp-ct monitor bot\n"
        "Версия: 1.0\n"
        "Разработчик: @yokhor\n"
        "GitHub: https://github.com/yokhorr\n\n"
        "Бот предназначен для мониторинга состояния посылок по заданиям курса C++.\n"
    )
    await message.answer(escape_md(info_text))


@dp.message(Command("klenin"))
async def klenin_command(message: types.Message) -> None:
    """
    Handle /klenin easter egg command (hidden from help).
    """
    easter_egg_text = (
        "🎓 Александр Сергеевич Кленин\n"
        "Легендарный преподаватель и создатель множества олимпиадных задач по программированию!\n\n"
        "\"Программирование — это искусство превращения кофе в код\"\n"
        "— Народная мудрость (приписываемая А.С. Кленину)\n\n"
        "🔥 Fun fact: Говорят, что если написать идеальный код, Кленин материализуется и поставит зачёт автоматом!"
    )
    await message.answer(escape_md(easter_egg_text))


@dp.message(Command("delentry"))
async def delete_entry_command(message: types.Message) -> None:
    """
    Handle /delentry command to delete entry from monitoring.
    """
    entries = load_entries()
    if not entries:
        await message.answer(escape_md("Нет отслеживаемых посылок для удаления."))
        return
    await message.answer(escape_md("Отправьте посылку, которую хотите удалить (три строки: timestamp, name, task):"))
    waiting_for_delete.add(message.from_user.id)


@dp.message(F.text)
async def handle_entry_lines(message: types.Message) -> None:
    """
    Handle text messages for entry addition/deletion.
    """
    user_id = message.from_user.id

    # Delete entry
    if user_id in waiting_for_delete:
        waiting_for_delete.discard(user_id)
        lines = message.text.strip().split('\n')
        if len(lines) != 3:
            await message.answer(escape_md("Ошибка: нужно отправить ровно три строки: timestamp, name, task."))
            return

        timestamp, name, task = [line.strip() for line in lines]
        entries = load_entries()
        entry_to_remove = {
            "timestamp": timestamp,
            "name": name,
            "task": task,
        }

        if entry_to_remove in entries:
            entries.remove(entry_to_remove)
            save_entries(entries)
            await message.answer(escape_md("Посылка удалена:"))
            block = monospace_block(f"{timestamp}\n{name}\n{task}")
            await message.answer(block)
        else:
            await message.answer(escape_md("Такая посылка не найдена в списке отслеживаемых."))
        return

    # Add new entry
    if user_id not in waiting_for_entry:
        return
    lines = message.text.strip().split('\n')
    if len(lines) != 3:
        await message.answer(escape_md("Ошибка: нужно отправить ровно три строки: timestamp, name, task.\nПример:"))
        example_block = monospace_block(
            "25.05.2025 18:39:30\n"
            "Соляник Егор Юрьевич\n"
            "socow-vector"
        )
        await message.answer(example_block)
        return
    timestamp, name, task = [line.strip() for line in lines]
    await message.answer(escape_md("Проверяю наличие в таблицах..."))
    loop = asyncio.get_event_loop()
    status, row = await loop.run_in_executor(
        None, check_entry_status, timestamp, name, task
    )
    entries = load_entries()
    new_entry = {
        "timestamp": timestamp,
        "name": name,
        "task": task,
    }
    if status == "not_found":
        await message.answer(escape_md("Такой посылки нет ни в одной из таблиц. Добавление отменено."))
    elif status == "checked":
        await message.answer(escape_md("Эта посылка уже проверена. Добавление отменено."))
    elif status == "on_review":
        await message.answer(escape_md("Эта посылка уже на проверке и будет добавлена для отслеживания."))
        if new_entry not in entries:
            entries.append(new_entry)
            save_entries(entries)
            await message.answer(escape_md("Посылка добавлена к мониторингу."))
        else:
            await message.answer(escape_md("Эта посылка уже отслеживается."))
    elif status == "exists":
        if new_entry not in entries:
            entries.append(new_entry)
            save_entries(entries)
            await message.answer(escape_md("Посылка добавлена к мониторингу."))
        else:
            await message.answer(escape_md("Эта посылка уже отслеживается."))
    waiting_for_entry.discard(user_id)


async def main() -> None:
    """
    Main function to start the bot.
    """
    logger.info("Bot started.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
