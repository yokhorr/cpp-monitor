import requests
import csv
import io
import asyncio
import logging
import os
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command

# ====== CONFIGURATION ======
BOT_TOKEN = open('bot_token.txt').read().strip()
SPREADSHEET_ID = "1PlQVDjbfnTrUBmgltN2JwDnq3ZUjs8l4ei_MkaGzL1A"
SHEET_GIDS = ["730603969", "928911897"]  # List of GID sheets to monitor
FETCH_INTERVAL = 10  # 10 seconds for testing
ENTRIES_FILE = "entries.json"  # File for storing monitored entries (JSON)
# ===========================

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s:%(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
monitoring_task = None

# For per-user state while waiting for entry lines
waiting_for_entry = set()

def get_gsheet_csv(spreadsheet_id: str, sheet_gid: str) -> list[dict]:
    """Fetches CSV data from Google Sheets and returns a list of dicts."""
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={sheet_gid}"
    resp = requests.get(url)
    resp.raise_for_status()
    f = io.StringIO(resp.content.decode('utf-8'))
    reader = csv.DictReader(f)
    data = list(reader)
    logger.info(f"Fetched {len(data)} rows from sheet GID {sheet_gid}")
    return data

def find_entry(data, timestamp, name, task):
    """Searches for an entry by three fields and returns the row if found."""
    for row in data:
        if (
            row.get("Метка времени") == timestamp
            and row.get("ФИО") == name
            and row.get("Задание") == task
        ):
            logger.info(f"Entry found: {row}")
            return row
    logger.info("Entry not found: %s, %s, %s", timestamp, name, task)
    return None

def entry_exists_in_sheets(timestamp, name, task):
    """Checks if entry exists in any of the monitored Google Sheets."""
    for gid in SHEET_GIDS:
        try:
            data = get_gsheet_csv(SPREADSHEET_ID, gid)
            if find_entry(data, timestamp, name, task):
                return True
        except Exception as e:
            logger.error(f"Error checking entry in GID {gid}: {e}")
    return False

def load_entries():
    """Loads monitored entries from a JSON file."""
    if os.path.exists(ENTRIES_FILE):
        with open(ENTRIES_FILE, encoding='utf-8') as f:
            try:
                entries = json.load(f)
                logger.info(f"Loaded {len(entries)} entries from file")
                return entries
            except Exception as e:
                logger.error(f"JSON read error: {e}")
                return []
    return []

def save_entries(entries):
    """Saves monitored entries to a JSON file."""
    with open(ENTRIES_FILE, "w", encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    logger.info(f"Saved {len(entries)} entries to file")

async def monitor_gsheet():
    """Periodically fetches data from Google Sheets and searches for monitored entries."""
    logger.info("Monitoring started")
    while True:
        try:
            entries = load_entries()
            if not entries:
                logger.info("No entries to monitor")
            for gid in SHEET_GIDS:
                try:
                    data = get_gsheet_csv(SPREADSHEET_ID, gid)
                    for entry in entries:
                        timestamp = entry.get("timestamp")
                        name = entry.get("name")
                        task = entry.get("task")
                        result = find_entry(data, timestamp, name, task)
                        if result:
                            print(f"НАЙДЕНО в GID={gid}: {result}")
                            logger.info(f"FOUND in GID={gid}: {result}")
                        else:
                            print(f"НЕ НАЙДЕНО в GID={gid}: {entry}")
                            logger.info(f"NOT FOUND in GID={gid}: {entry}")
                except Exception as e:
                    logger.error(f"Error fetching or searching sheet GID {gid}: {e}")
        except Exception as e:
            logger.error(f"Monitoring error: {e}")
        await asyncio.sleep(FETCH_INTERVAL)

@dp.message(Command("monitor"))
async def start_monitoring(message: types.Message):
    """Starts background monitoring."""
    global monitoring_task
    if not monitoring_task or monitoring_task.done():
        monitoring_task = asyncio.create_task(monitor_gsheet())
        await message.answer("Мониторинг запущен!")
        logger.info(f"Monitoring started by user {message.from_user.id}")
    else:
        await message.answer("Мониторинг уже работает.")
        logger.info(f"Attempt to start monitoring again (user {message.from_user.id})")

@dp.message(Command("stop"))
async def stop_monitoring(message: types.Message):
    """Stops background monitoring."""
    global monitoring_task
    if monitoring_task and not monitoring_task.done():
        monitoring_task.cancel()
        monitoring_task = None
        await message.answer("Мониторинг остановлен.")
        logger.info(f"Monitoring stopped by user {message.from_user.id}")
    else:
        await message.answer("Мониторинг не был запущен.")
        logger.info(f"Attempt to stop not running monitoring (user {message.from_user.id})")

@dp.message(Command("addentry"))
async def add_entry_command(message: types.Message):
    """
    Starts the process of adding a new entry for monitoring.
    Usage: /addentry, then three lines (timestamp, name, task).
    """
    user_id = message.from_user.id
    waiting_for_entry.add(user_id)
    await message.answer(
        "Отправьте ровно три строки: timestamp, name, task.\n"
        "Пример:\n25.05.2025 18:39:30\nСоляник Егор Юрьевич\nsocow-vector"
    )

@dp.message(Command("listentries"))
async def list_entries(message: types.Message):
    """Shows all monitored entries."""
    entries = load_entries()
    if not entries:
        await message.answer("Нет отслеживаемых вхождений.")
        logger.info(f"User {message.from_user.id} requested entry list: no entries")
        return
    result = "\n\n".join(
        f"{i+1}.\n{entry['timestamp']}\n{entry['name']}\n{entry['task']}" for i, entry in enumerate(entries)
    )
    await message.answer(f"Текущие отслеживаемые вхождения:\n\n{result}")
    logger.info(f"User {message.from_user.id} requested entry list")

@dp.message(Command("help"))
async def help_command(message: types.Message):
    """Shows help for commands."""
    text = (
        "Доступные команды:\n"
        "/monitor — Запустить мониторинг Google Sheets\n"
        "/stop — Остановить мониторинг\n"
        "/addentry — Добавить новое вхождение для мониторинга (после команды отправьте три строки: timestamp, name, task)\n"
        "/listentries — Показать все текущие отслеживаемые вхождения\n"
        "/help — Показать эту справку\n\n"
        "Пример для /addentry:\n"
        "/addentry\n(после этого три строки)\n25.05.2025 18:39:30\nСоляник Егор Юрьевич\nsocow-vector"
    )
    await message.answer(text)

@dp.message(F.text)
async def handle_entry_lines(message: types.Message):
    """
    Handles messages with three lines for entry addition, if user is in waiting state.
    Ignores commands.
    """
    # Ignore commands in this handler
    if message.text.startswith("/"):
        return

    user_id = message.from_user.id
    if user_id not in waiting_for_entry:
        return  # Ignore if not expecting entry from this user

    lines = message.text.strip().split('\n')
    if len(lines) != 3:
        await message.answer(
            "Ошибка: нужно отправить ровно три строки: timestamp, name, task.\n"
            "Попробуйте снова."
        )
        return
    timestamp, name, task = [line.strip() for line in lines]
    # Check existence in Google Sheets
    await message.answer("Проверяю наличие в таблицах...")
    exists = await asyncio.get_event_loop().run_in_executor(
        None, entry_exists_in_sheets, timestamp, name, task
    )
    if not exists:
        await message.answer("Такого вхождения нет ни в одной из таблиц. Добавление отменено.")
        waiting_for_entry.discard(user_id)
        return

    entries = load_entries()
    new_entry = {
        "timestamp": timestamp,
        "name": name,
        "task": task,
    }
    if new_entry in entries:
        await message.answer("Это вхождение уже отслеживается.")
        logger.info(f"Attempt to add duplicate: {new_entry}")
    else:
        entries.append(new_entry)
        save_entries(entries)
        await message.answer("Вхождение добавлено к мониторингу.")
        logger.info(f"Added new entry: {new_entry}")
    waiting_for_entry.discard(user_id)

async def main():
    logger.info("Bot started.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
