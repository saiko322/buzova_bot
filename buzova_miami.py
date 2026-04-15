import asyncio
import logging
import sqlite3
import random
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8763712447:AAGvNeYpWWe92FNHB7tUwAMH8VAP5r9Yudg"
ADMIN_CHAT_ID = 1635609048
PORT = 8080

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS parlays
                 (id INTEGER PRIMARY KEY, date TEXT, type TEXT, events TEXT, odds REAL, status TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS predictions
                 (id INTEGER PRIMARY KEY, match TEXT, sport TEXT, bot_pred TEXT, stat_pred TEXT, ai_pred TEXT, timestamp TEXT)""")
    conn.commit()
    conn.close()

# ========== КОМАНДЫ БОТА ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🌴 БУЗОВА В МАЙАМИ\n\n"
        "✅ Бот запущен и работает\n"
        "✅ Команды:\n"
        "/start — приветствие\n"
        "/parlay — экспрессы (в разработке)\n"
        "/bank — управление банком (в разработке)"
    )

@dp.message(Command("parlay"))
async def cmd_parlay(message: types.Message):
    await message.answer("🎲 Экспрессы скоро появятся!")

@dp.message(Command("bank"))
async def cmd_bank(message: types.Message):
    await message.answer("💰 Банк-менеджмент скоро появится!")

# ========== ЗАПУСК ==========
async def main():
    init_db()
    logging.basicConfig(level=logging.INFO)
    print("🤖 Бузова в Майами запущена...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
