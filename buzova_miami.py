import asyncio
import logging
import sqlite3
import random
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest

# ========== НАСТРОЙКИ (БЕРУТСЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ В RAILWAY) ==========
import os
BOT_TOKEN = "8763712447:AAGvNeYpWWe92FNHB7tUwAWH8VAP5r9Yudg"
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
RAPIDAPI_KEY = None  # временно отключено
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")
PORT = os.getenv("PORT", "8080")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
# telethon_client = TelegramClient('buzova_session', TELEGRAM_API_ID, TELEGRAM_API_HASH)
telethon_client = None
# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS parlays
                 (id INTEGER PRIMARY KEY, date TEXT, type TEXT, events TEXT, odds REAL, status TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS insights
                 (id INTEGER PRIMARY KEY, source TEXT, text TEXT, sport TEXT, timestamp TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS predictions
                 (id INTEGER PRIMARY KEY, match TEXT, sport TEXT, bot_pred TEXT, stat_pred TEXT, ai_pred TEXT, result TEXT, timestamp TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS matches
                 (id INTEGER PRIMARY KEY, sport TEXT, home TEXT, away TEXT, date TEXT, status TEXT, home_score INTEGER, away_score INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS bank_history
                 (id INTEGER PRIMARY KEY, balance INTEGER, timestamp TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS odds_history
                 (id INTEGER PRIMARY KEY, match TEXT, odds REAL, timestamp TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS lessons
                 (id INTEGER PRIMARY KEY, lesson TEXT, condition TEXT, weight INTEGER)""")
    conn.commit()
    conn.close()

def save_bank_snapshot(balance: int):
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("INSERT INTO bank_history (balance, timestamp) VALUES (?, ?)",
              (balance, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_bank_history(days: int = 30):
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    start_date = (datetime.now() - timedelta(days=days)).isoformat()
    c.execute("SELECT balance, timestamp FROM bank_history WHERE timestamp >= ? ORDER BY timestamp ASC", (start_date,))
    rows = c.fetchall()
    conn.close()
    return rows

def save_match(sport: str, home: str, away: str, date: str):
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("INSERT INTO matches (sport, home, away, date, status) VALUES (?, ?, ?, ?, ?)",
              (sport, home, away, date, "scheduled"))
    conn.commit()
    conn.close()

def save_insight(source: str, text: str, sport: str):
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("INSERT INTO insights (source, text, sport, timestamp) VALUES (?, ?, ?, ?)",
              (source, text, sport, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_recent_insights(sport: str = None, limit: int = 30) -> List[Dict]:
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    if sport:
        c.execute("SELECT source, text FROM insights WHERE sport = ? ORDER BY id DESC LIMIT ?", (sport, limit))
    else:
        c.execute("SELECT source, text FROM insights ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [{"source": r[0], "text": r[1]} for r in rows]

def save_parlay(parlay_type: str, events: str, total_odds: float):
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("INSERT INTO parlays (date, type, events, odds, status) VALUES (?, ?, ?, ?, ?)",
              (datetime.now().isoformat(), parlay_type, events, total_odds, "active"))
    conn.commit()
    conn.close()

def get_parlays_stats() -> Dict:
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("SELECT status, odds FROM parlays")
    rows = c.fetchall()
    conn.close()
    
    wins = [r for r in rows if r[0] == 'win']
    losses = [r for r in rows if r[0] == 'lose']
    
    return {
        "total": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(rows) * 100, 1) if rows else 0,
        "avg_odds_win": round(sum(r[1] for r in wins) / len(wins), 2) if wins else 0,
        "best_odds": max((r[1] for r in wins), default=0),
    }

def get_streaks() -> Dict:
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("SELECT status FROM parlays ORDER BY id ASC")
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        return {"best_win": 0, "worst_lose": 0}
    
    current_streak = 1
    best_win = 1 if rows[0][0] == 'win' else 0
    worst_lose = 1 if rows[0][0] == 'lose' else 0
    current_type = rows[0][0]
    
    for row in rows[1:]:
        if row[0] == current_type:
            current_streak += 1
        else:
            if current_type == 'win' and current_streak > best_win:
                best_win = current_streak
            elif current_type == 'lose' and current_streak > worst_lose:
                worst_lose = current_streak
            current_streak = 1
            current_type = row[0]
    
    if current_type == 'win' and current_streak > best_win:
        best_win = current_streak
    elif current_type == 'lose' and current_streak > worst_lose:
        worst_lose = current_streak
    
    return {"best_win": best_win, "worst_lose": worst_lose}

def get_parlays_history(limit: int = 10):
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("SELECT date, type, odds, status FROM parlays ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return rows

def save_odds_history(match: str, odds: float):
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("INSERT INTO odds_history (match, odds, timestamp) VALUES (?, ?, ?)",
              (match, odds, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def check_odds_movement(match: str, current_odds: float) -> Optional[str]:
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("SELECT odds, timestamp FROM odds_history WHERE match = ? ORDER BY timestamp DESC LIMIT 1", (match,))
    row = c.fetchone()
    
    if not row:
        save_odds_history(match, current_odds)
        conn.close()
        return None
    
    last_odds, last_time = row
    change = current_odds - last_odds
    
    if abs(change) >= 0.15:
        direction = "УПАЛ" if change < 0 else "ВЫРОС"
        recommendation = "ставь на фаворита" if change < 0 else "осторожно, рынок против фаворита"
        save_odds_history(match, current_odds)
        conn.close()
        return f"📉 Кэф {direction} с {last_odds:.2f} → {current_odds:.2f}\n💡 {recommendation}"
    
    conn.close()
    return None

def save_prediction(match: str, sport: str, bot_pred: str, stat_pred: str, ai_pred: str):
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("INSERT INTO predictions (match, sport, bot_pred, stat_pred, ai_pred, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
              (match, sport, bot_pred, stat_pred, ai_pred, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def update_prediction_result(match: str, result: str):
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("UPDATE predictions SET result = ? WHERE match = ? AND result IS NULL", (result, match))
    conn.commit()
    conn.close()

def add_lesson(lesson: str, condition: str, weight: int):
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("INSERT INTO lessons (lesson, condition, weight) VALUES (?, ?, ?)", (lesson, condition, weight))
    conn.commit()
    conn.close()

def get_lessons() -> List[Dict]:
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("SELECT lesson, condition, weight FROM lessons ORDER BY weight DESC")
    rows = c.fetchall()
    conn.close()
    return [{"lesson": r[0], "condition": r[1], "weight": r[2]} for r in rows]

# ========== УПРАВЛЕНИЕ БАНКОМ ==========
BANK_FILE = "bank.json"
MIN_BET = 30

def load_bank() -> Dict:
    try:
        with open(BANK_FILE, "r") as f:
            return json.load(f)
    except:
        return {"balance": 1000, "goal": 50000, "history": []}

def save_bank(data: Dict):
    with open(BANK_FILE, "w") as f:
        json.dump(data, f, indent=2)

def calculate_bet(balance: float, risk_level: str = "optimal") -> int:
    if balance < 500:
        return 0
    percent = {"safe": 0.03, "optimal": 0.04, "risky": 0.05}[risk_level]
    bet = int(balance * percent)
    return max(bet, MIN_BET)

def update_bank(result: str, bet: int, odds: float) -> Dict:
    bank_data = load_bank()
    if result == "win":
        win_amount = int(bet * odds)
        bank_data["balance"] += win_amount
        bank_data["history"].append({"date": datetime.now().isoformat(), "type": "win", "bet": bet, "odds": odds, "win": win_amount, "balance": bank_data["balance"]})
    else:
        bank_data["balance"] -= bet
        bank_data["history"].append({"date": datetime.now().isoformat(), "type": "lose", "bet": bet, "odds": odds, "loss": bet, "balance": bank_data["balance"]})
    save_bank(bank_data)
    save_bank_snapshot(bank_data["balance"])
    return bank_data

def get_bank_status() -> Dict:
    bank_data = load_bank()
    progress = round(bank_data["balance"] / bank_data["goal"] * 100, 1)
    return {"balance": bank_data["balance"], "goal": bank_data["goal"], "progress": progress, "remaining": bank_data["goal"] - bank_data["balance"], "history": bank_data["history"][-10:]}

async def check_goal_achieved(bank_data: Dict):
    if bank_data["balance"] >= bank_data["goal"]:
        congrats = [f"🔥 ТЫ ЕБАНУТЫЙ? {bank_data['balance']} ₽! ЦЕЛЬ ДОСТИГНУТА!", f"💰 ТЫ ВЫНЕС БУКМЕКЕРА НА {bank_data['balance']} ₽.", f"💃 БУЗОВА В МАЙАМИ. {bank_data['balance']} ₽ НА ТВОЕМ БАНКЕ."]
        await bot.send_message(ADMIN_CHAT_ID, f"🏆 **ЦЕЛЬ ДОСТИГНУТА!**\n\n{random.choice(congrats)}")
        bank_data["goal"] = bank_data["goal"] * 2
        save_bank(bank_data)
        await bot.send_message(ADMIN_CHAT_ID, f"🎯 НОВАЯ ЦЕЛЬ: {bank_data['goal']} ₽")

async def check_min_bank(bank_data: Dict):
    if bank_data["balance"] < 500:
        warnings = [f"🔴 СТОП-ЛОСС! БАНК УПАЛ ДО {bank_data['balance']} ₽. СТАВКИ ПРИОСТАНОВЛЕНЫ.", f"💰 ОСТАЛОСЬ {bank_data['balance']} ₽. ХВАТИТ.", f"😭 БУЗОВА ПЛАЧЕТ. БАНК: {bank_data['balance']} ₽."]
        await bot.send_message(ADMIN_CHAT_ID, f"🚨 **СТОП-ЛОСС**\n\n{random.choice(warnings)}")

def get_bank_chart(days: int = 30) -> str:
    history = get_bank_history(days)
    if not history:
        return "📭 Недостаточно данных для графика."
    
    step = max(1, len(history) // 15)
    filtered = history[::step]
    
    balances = [h[0] for h in filtered]
    dates = [h[1][5:10] for h in filtered]
    
    max_bal = max(balances)
    min_bal = min(balances)
    range_bal = max_bal - min_bal if max_bal != min_bal else 1
    
    chart = "📈 **ДИНАМИКА БАНКА**\n\n"
    for date, balance in zip(dates, balances):
        height = int((balance - min_bal) / range_bal * 9) + 1
        bar = "█" * height
        chart += f"{date} {bar} {balance} ₽\n"
    
    chart += f"\n📊 МИН: {min_bal} ₽ | МАКС: {max_bal} ₽"
    chart += f"\n📈 РОСТ: {max_bal - min_bal:+} ₽"
    return chart

# ========== ЖЁСТКИЕ ЦИТАТКИ ==========
CITATKI_WIN = [
    "🔥 ЕБАТЬ ТЫ ГОСПОДЬ БОГ СТАВОК. БУК В НОСОК. СИМПЛ ОДОБРЯЕТ.",
    "💰 ВЗОРВАЛ БУКМЕКЕРА. БУЗОВА ТАНЦУЕТ В МАЙАМИ.",
    "🦾 ТВОЯ АНАЛИТИКА — ГРУДА МЫШЦ. СИМПЛ КУРИТ В СТОРОНКЕ.",
    "💃 БУЗОВА В МАЙАМИ. БУК В ЖОПЕ.",
    "⚡️ СИМПЛ СНЯЛ ШЛЕМ. БУК СНЯЛ ТРУСЫ."
]

CITATKI_LOSE = [
    "💩 ТЫ ЕБАНУТЫЙ? КАК МОЖНО БЫЛО ТАК ПРОСРАТЬСЯ?",
    "🤡 НЕ ЗАХОД. ТЫ ГОВНО. СТАВКИ — НЕ ТВОЁ.",
    "😭 ДАЖЕ БУЗОВА В ШОКЕ.",
    "🎲 ЭТО ЧИСТОЕ 322! ТЫ ЧТО, СОЛО НА СТРИМЕ?",
    "💰 КОЭФФИЦИЕНТ 3.22? СОЛО ОДОБРЯЕТ. ТЫ ПРОСТО ЛОХ."
]

CITATKI_PARTIAL = [
    "🤷‍♂️ ОДИН ЗАЛЕТЕЛ, ОДИН СГОРЕЛ. НЕ ПОЗОР, НО И НЕ ГЕРОЙ.",
    "🟡 НОРМ. БУЗОВА ПОЖМАЛА ПЛЕЧАМИ.",
    "📊 НЕ ПОЗОР. НО И ТОСТ ЗА ТЕБЯ ПОДНИМАТЬ РАНО."
]

# ========== API ==========
def fetch_football_matches():
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://api-football-v1.p.rapidapi.com/v3/fixtures?date={today}"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        matches = []
        for fixture in data.get("response", [])[:20]:
            matches.append({"id": fixture["fixture"]["id"], "sport": "football", "league": fixture["league"]["name"], "home": fixture["teams"]["home"]["name"], "away": fixture["teams"]["away"]["name"], "date": fixture["fixture"]["date"], "status": fixture["fixture"]["status"]["short"]})
            save_match("football", fixture["teams"]["home"]["name"], fixture["teams"]["away"]["name"], fixture["fixture"]["date"])
        return matches
    except Exception as e:
        print(f"Ошибка API-FOOTBALL: {e}")
        return []

def fetch_nba_matches():
    url = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"
    try:
        response = requests.get(url)
        data = response.json()
        matches = []
        today = datetime.now().strftime("%m/%d/%Y")
        for date_entry in data["leagueSchedule"]["gameDates"]:
            if date_entry["gameDate"][:10] == today:
                for game in date_entry["games"]:
                    matches.append({
                        "id": game["gameId"],
                        "sport": "basketball",
                        "league": "NBA",
                        "home": game["homeTeam"]["teamName"],
                        "away": game["awayTeam"]["teamName"],
                        "date": game["gameDateTimeUTC"],
                        "status": game.get("gameStatusText", "scheduled").lower()
                    })
                    save_match("basketball", game["homeTeam"]["teamName"], game["awayTeam"]["teamName"], game["gameDateTimeUTC"])
        return matches
    except Exception as e:
        print(f"Ошибка NBA API: {e}")
        return []

def get_today_matches():
    matches = fetch_football_matches()
    matches.extend(fetch_nba_matches())
    return matches

def fetch_odds(match_home: str, match_away: str, sport: str = "soccer"):
    sport_map = {"football": "soccer", "basketball": "basketball_nba", "hockey": "icehockey_nhl"}
    sport_key = sport_map.get(sport, "soccer")
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
    params = {"apiKey": ODDS_API_KEY, "regions": "eu", "markets": "totals", "bookmakers": "pinnacle"}
    try:
        response = requests.get(url, params=params)
        data = response.json()
        for event in data:
            if match_home.lower() in event["home_team"].lower() and match_away.lower() in event["away_team"].lower():
                for bookmaker in event.get("bookmakers", []):
                    for market in bookmaker.get("markets", []):
                        if market["key"] == "totals":
                            for outcome in market["outcomes"]:
                                if outcome["name"] == "Under":
                                    return outcome["price"]
        return 1.85
    except Exception as e:
        print(f"Ошибка Odds API: {e}")
        return 1.85

def fetch_match_result(match_id: int, sport: str) -> Optional[Dict]:
    if sport == "football":
        url = f"https://api-football-v1.p.rapidapi.com/v3/fixtures?id={match_id}"
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
        try:
            response = requests.get(url, headers=headers)
            data = response.json()
            fixture = data.get("response", [{}])[0]
            status = fixture.get("fixture", {}).get("status", {}).get("short", "")
            if status == "FT":
                home_score = fixture.get("goals", {}).get("home", 0)
                away_score = fixture.get("goals", {}).get("away", 0)
                return {"home_score": home_score, "away_score": away_score}
        except:
            return None
    return None

# ========== ПРОГНОЗЫ (ТРИ ТИПА) ==========
class BotPredictor:
    def __init__(self):
        self.elo = {"Монако": 1580, "Париж": 1520, "Авангард": 1650, "ЦСКА": 1600, "Бавария": 1750, "Торонто": 1620}
    def predict(self, home: str, away: str, insights: List[Dict]) -> Dict:
        home_elo = self.elo.get(home, 1500) + 50
        away_elo = self.elo.get(away, 1500)
        prob = round(home_elo / (home_elo + away_elo) * 100, 1)
        insight_impact = -15 if any("Головин" in i["text"] for i in insights) else 0
        final_prob = max(10, min(90, prob + insight_impact))
        return {"prediction": f"Победа {home}", "confidence": "ВЫСОКАЯ" if final_prob > 60 else "СРЕДНЯЯ", "prob": final_prob}

class StatisticalAnalyzer:
    def predict(self, home: str, away: str) -> Dict:
        return {"under_2_5_prob": 65, "expected_goals": 2.4, "form_home": "3-1-1", "form_away": "1-2-2"}

class MyAIAnalyzer:
    def predict(self, home: str, away: str, insights: List[Dict]) -> Dict:
        if any("Головин" in i["text"] for i in insights):
            return {"prediction": "Тотал меньше 2.5", "confidence": 72, "reason": "Головин не играет, Монако без атаки"}
        return {"prediction": f"Победа {home}", "confidence": 65, "reason": "Домашнее поле и форма"}

# ========== ФИЛЬТР ОШИБОК (с самообучением) ==========
def check_lessons(home: str, away: str, sport: str, insights: List[Dict]) -> List[Dict]:
    lessons = get_lessons()
    risks = []
    
    for lesson in lessons:
        condition = lesson["condition"]
        if "соперник не проигрывает дома" in condition:
            risks.append({"lesson": lesson["lesson"], "level": "🟡 ЕСТЬ РИСК", "detail": condition})
        elif "фаворит выиграл 5+ матчей подряд" in condition:
            risks.append({"lesson": lesson["lesson"], "level": "🟡 ЕСТЬ РИСК", "detail": condition})
        elif "фаворит играл в Европе за 3 дня" in condition:
            risks.append({"lesson": lesson["lesson"], "level": "🔴 КРИТИЧЕСКИЙ РИСК", "detail": condition})
        elif "травма главного бомбардира" in condition:
            if any("травм" in i["text"] for i in insights):
                risks.append({"lesson": lesson["lesson"], "level": "🔴 КРИТИЧЕСКИЙ РИСК", "detail": condition})
        elif "Реал — Жирона" in lesson["lesson"]:
            risks.append({"lesson": lesson["lesson"], "level": "🔴 ПРИМЕР РАЗОБРАННОЙ ОШИБКИ", "detail": condition})
    
    if not risks:
        risks.append({"lesson": "Нет активных уроков", "level": "✅", "detail": "Похожих ошибок не найдено"})
    
    return risks

# ========== АВТООБНОВЛЕНИЕ РЕЗУЛЬТАТОВ ==========
async def update_all_results():
    conn = sqlite3.connect("buzova.db")
    c = conn.cursor()
    c.execute("SELECT id, sport, home, away FROM matches WHERE status = 'scheduled' AND date < ?", (datetime.now().isoformat(),))
    matches = c.fetchall()
    conn.close()
    
    for match_id, sport, home, away in matches:
        result = fetch_match_result(match_id, sport)
        if result:
            conn = sqlite3.connect("buzova.db")
            c = conn.cursor()
            c.execute("UPDATE matches SET status = 'finished', home_score = ?, away_score = ? WHERE id = ?", 
                     (result["home_score"], result["away_score"], match_id))
            conn.commit()
            conn.close()
            
            conn = sqlite3.connect("buzova.db")
            c = conn.cursor()
            c.execute("SELECT bot_pred FROM predictions WHERE match LIKE ? ORDER BY id DESC LIMIT 1", (f"%{home}%{away}%",))
            pred_row = c.fetchone()
            conn.close()
            
            if pred_row:
                actual = "Победа хозяев" if result["home_score"] > result["away_score"] else "Победа гостей" if result["away_score"] > result["home_score"] else "Ничья"
                if actual not in pred_row[0]:
                    new_lesson = f"Ошибка: {home} — {away}. {pred_row[0]} вместо {actual}"
                    add_lesson(new_lesson, f"{home} проиграл, хотя был фаворитом", 2)
                    print(f"✅ Добавлен новый урок: {new_lesson}")

# ========== TELEGRAM-КАНАЛЫ ==========
INSIDER_CHANNELS = {
    "nba": ["https://t.me/nba_injurynews", "https://t.me/nbainformer"],
    "football": ["https://t.me/injuriesinfo", "https://t.me/sportscryptoinsider", "https://t.me/nikita_burdasov", "https://t.me/karpinside"],
    "hockey": ["https://t.me/hockey_vbros", "https://t.me/hockeybettingx", "https://t.me/hocklife"]
}
KEYWORDS = ["травм", "injured", "out", "questionable", "не сыграет", "выбыл"]

async def fetch_insider_messages():
    for sport, channels in INSIDER_CHANNELS.items():
        for channel_url in channels:
            try:
                username = channel_url.split("/")[-1]
                entity = await telethon_client.get_entity(username)
                history = await telethon_client(GetHistoryRequest(peer=entity, limit=10, offset_id=0, offset_date=None, add_offset=0, max_id=0, min_id=0, hash=0))
                for msg in history.messages:
                    if msg.text and any(kw.lower() in msg.text.lower() for kw in KEYWORDS):
                        save_insight(channel_url, msg.text[:500], sport)
            except Exception as e:
                print(f"Ошибка {channel_url}: {e}")

async def monitor_insiders_periodically():
    while True:
        await fetch_insider_messages()
        await asyncio.sleep(1800)

# ========== КОМАНДЫ БОТА ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🌴 БУЗОВА В МАЙАМИ\n\n"
        "✅ Три типа прогнозов\n"
        "✅ Инсайды из Telegram\n"
        "✅ Управление банком\n"
        "✅ Конструктор экспрессов\n"
        "✅ Статистика и графики\n"
        "✅ Фильтр ошибок (учится на своих ошибках)\n"
        "✅ Поиск валуя (value betting)\n"
        "✅ Уведомления за 15 минут до матча\n"
        "✅ График банка картинкой\n\n"
        "Команды:\n"
        "/predict — прогноз\n"
        "/parlay — экспрессы\n"
        "/bank — управление банком\n"
        "/stats — статистика\n"
        "/chart — график банка (картинка)\n"
        "/insights — инсайды\n"
        "/history — история\n"
        "/odds — движение кэфа\n"
        "/value — поиск валуйных ставок"
    )

@dp.message(Command("predict"))
async def cmd_predict(message: types.Message):
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /predict Металлург Торпедо")
        return
    home, away = args[1], args[2]
    insights = get_recent_insights()
    
    lessons_risks = check_lessons(home, away, "hockey", insights)
    
    bot_pred = BotPredictor().predict(home, away, insights)
    stat_pred = StatisticalAnalyzer().predict(home, away)
    ai_pred = MyAIAnalyzer().predict(home, away, insights)
    odds = fetch_odds(home, away)
    
    movement = check_odds_movement(f"{home}-{away}", odds)
    
    text = f"🔮 ПРОГНОЗ: {home} — {away}\n\n"
    
    text += "🧠 АНАЛИЗ ПРОШЛЫХ ОШИБОК:\n"
    for r in lessons_risks:
        text += f"{r['lesson']}: {r['level']}\n   → {r['detail']}\n"
    text += "\n"
    
    text += f"🤖 БОТ: {bot_pred['prediction']} ({bot_pred['confidence']})\n"
    text += f"📊 АНАЛИТИКИ: ТМ 2.5 ({stat_pred['under_2_5_prob']}%)\n"
    text += f"🧠 Я (AI): {ai_pred['prediction']} — {ai_pred['reason']}\n"
    text += f"💰 КОЭФФИЦИЕНТ (Pinnacle): {odds}\n"
    if movement:
        text += f"\n{movement}\n"
    text += "\n"
    
    if bot_pred['prediction'] == ai_pred['prediction']:
        text += "🎯 КОНСЕНСУС: Бот и Я согласны."
    else:
        text += "⚠️ КОНСЕНСУСА НЕТ. Решай сам."
    
    await message.answer(text)
    
    save_prediction(f"{home}-{away}", "hockey", bot_pred['prediction'], str(stat_pred['under_2_5_prob']), ai_pred['prediction'])

@dp.message(Command("parlay"))
async def cmd_parlay(message: types.Message):
    bank_data = load_bank()
    if bank_data["balance"] < 500:
        await message.answer(f"🔴 СТОП-ЛОСС! Банк {bank_data['balance']} ₽. /bank_reset")
        return
    matches = get_today_matches()
    if not matches:
        await message.answer("❌ Нет матчей на сегодня.")
        return
    status = get_bank_status()
    text = "🎲 ЭКСПРЕССЫ НА СЕГОДНЯ\n\n"
    
    safe_bet = calculate_bet(status['balance'], "safe")
    text += f"🟢 БЕЗОПАСНЫЙ (кэф 2.85) → ставка {safe_bet} ₽\n"
    for m in matches[:2]:
        text += f"   {m['home']} — {m['away']}: победа хозяев\n"
    
    optimal_bet = calculate_bet(status['balance'], "optimal")
    text += f"\n🟡 ОПТИМАЛЬНЫЙ (кэф 5.20) → ставка {optimal_bet} ₽\n"
    for m in matches[2:4]:
        text += f"   {m['home']} — {m['away']}: тотал меньше 2.5\n"
    
    risky_bet = calculate_bet(status['balance'], "risky")
    text += f"\n🔴 РИСКОВАННЫЙ (кэф 9.40) → ставка {risky_bet} ₽\n"
    for m in matches[4:6]:
        text += f"   {m['home']} — {m['away']}: фора -1.5\n"
    
    text += f"\n💰 Банк: {status['balance']} ₽ | Цель: {status['goal']} ₽"
    await message.answer(text)

@dp.message(Command("bank"))
async def cmd_bank(message: types.Message):
    status = get_bank_status()
    bar = "▰" * int(status["progress"] / 5) + "▱" * (20 - int(status["progress"] / 5))
    text = f"💰 УПРАВЛЕНИЕ БАНКОМ\n\n🏦 {status['balance']} ₽ / {status['goal']} ₽\n📊 {bar} {status['progress']}%\n\n🎲 РЕКОМЕНДАЦИЯ (мин. 30 ₽):\n🟢 Безопасный → {calculate_bet(status['balance'], 'safe')} ₽\n🟡 Оптимальный → {calculate_bet(status['balance'], 'optimal')} ₽\n🔴 Рискованный → {calculate_bet(status['balance'], 'risky')} ₽\n\n⚠️ ДИСЦИПЛИНА: хату не ставим, луди — не наш путь"
    await message.answer(text)

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    stats = get_parlays_stats()
    streaks = get_streaks()
    bank_status = get_bank_status()
    
    if stats["total"] == 0:
        await message.answer("📭 Нет данных для статистики. Сделай хотя бы один экспресс.")
        return
    
    text = f"📊 СТАТИСТИКА СТАВОК\n\nЗА ВСЁ ВРЕМЯ:\n✅ Зашло: {stats['wins']} из {stats['total']} ({stats['win_rate']}%)\n💰 Суммарный выигрыш: {stats['wins'] * 100:.0f} ₽\n💸 Суммарная ставка: {stats['total'] * 100:.0f} ₽\n📈 ROI: {(stats['wins'] * 100 - stats['total'] * 100) / (stats['total'] * 100) * 100:.1f}%\n\n"
    if stats['best_odds'] > 0:
        text += f"🔥 ЛУЧШИЙ ЭКСПРЕСС:\nКэф {stats['best_odds']}, выигрыш {stats['best_odds'] * 100:.0f} ₽\n\n"
    text += f"🎯 ЛУЧШАЯ СЕРИЯ: {streaks['best_win']} побед подряд\n😭 ХУДШАЯ СЕРИЯ: {streaks['worst_lose']} поражений подряд\n\n🏦 ТЕКУЩИЙ БАНК: {bank_status['balance']} ₽\n🎯 ЦЕЛЬ: {bank_status['goal']} ₽"
    
    await message.answer(text)

@dp.message(Command("chart"))
async def cmd_chart(message: types.Message):
    chart = get_bank_chart(30)
    await message.answer(chart)

@dp.message(Command("insights"))
async def cmd_insights(message: types.Message):
    insights = get_recent_insights(limit=10)
    if not insights:
        await message.answer("🔴 Пока нет свежих инсайдов.")
        return
    text = "🔴 ПОСЛЕДНИЕ ИНСАЙДЫ\n\n"
    for i in insights[:5]:
        text += f"📡 {i['source']}\n{i['text'][:200]}...\n\n"
    await message.answer(text)

@dp.message(Command("history"))
async def cmd_history(message: types.Message):
    history = get_parlays_history(10)
    if not history:
        await message.answer("📭 История пуста.")
        return
    text = "📜 ИСТОРИЯ ЭКСПРЕССОВ (последние 10)\n\n"
    for row in history:
        date, ptype, odds, status = row
        emoji = "✅" if status == "win" else "❌" if status == "lose" else "⏳"
        text += f"{date[:10]} | {ptype} | кэф {odds} | {emoji}\n"
    await message.answer(text)

@dp.message(Command("odds"))
async def cmd_odds(message: types.Message):
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /odds Монако Париж")
        return
    home, away = args[1], args[2]
    odds = fetch_odds(home, away)
    movement = check_odds_movement(f"{home}-{away}", odds)
    text = f"📊 КОЭФФИЦИЕНТ: {home} — {away}\n\n💰 Текущий (Pinnacle): {odds}\n"
    if movement:
        text += f"\n{movement}"
    else:
        text += "\n📈 Значительного движения не зафиксировано."
    await message.answer(text)

@dp.message(Command("value"))
async def cmd_value(message: types.Message):
    matches = get_today_matches()
    if not matches:
        await message.answer("🔍 Нет матчей для поиска валуя.")
        return
    value_bets = []
    for match in matches[:10]:
        odds = fetch_odds(match['home'], match['away'])
        if odds > 2.0:
            value_bets.append(f"🔴 {match['home']} — {match['away']}: коэффициент {odds}")
    if value_bets:
        text = "💰 **ВАЛУЙНЫЕ СТАВКИ (ЛАЗЕЙКИ)**\n\n" + "\n".join(value_bets)
    else:
        text = "🔍 Валуйных ставок на сегодня не найдено."
    await message.answer(text)

# ========== АВТОЗАПУСК ==========
async def scheduled_fetch_matches():
    while True:
        now = datetime.now()
        target = now.replace(hour=8, minute=0, second=0)
        if now >= target:
            matches = get_today_matches()
            await bot.send_message(ADMIN_CHAT_ID, f"📅 Загружено {len(matches)} матчей на сегодня")
            await asyncio.sleep(86400)
        else:
            await asyncio.sleep((target - now).total_seconds())

async def scheduled_daily_summary():
    while True:
        now = datetime.now()
        target = now.replace(hour=23, minute=59, second=0)
        if now >= target:
            await update_all_results()
            bank_data = load_bank()
            await bot.send_message(ADMIN_CHAT_ID, f"📢 **ИТОГИ ДНЯ**\n\n💰 Банк: {bank_data['balance']} ₽\n🎯 Цель: {bank_data['goal']} ₽")
            await asyncio.sleep(86400)
        else:
            await asyncio.sleep((target - now).total_seconds())

async def scheduled_match_reminder():
    while True:
        now = datetime.now()
        soon = now + timedelta(minutes=15)
        conn = sqlite3.connect("buzova.db")
        c = conn.cursor()
        c.execute("SELECT home, away, date FROM matches WHERE status = 'scheduled' AND date BETWEEN ? AND ?",
                  (now.isoformat(), soon.isoformat()))
        matches = c.fetchall()
        conn.close()
        for home, away, match_date in matches:
            await bot.send_message(ADMIN_CHAT_ID, f"⏰ **Напоминание!**\nМатч {home} — {away} начнётся через 15 минут (в {match_date[11:16]}).\nУспевай сделать ставку!")
        await asyncio.sleep(60)

# ========== ЗАПУСК ==========
async def main():
    init_db()
    logging.basicConfig(level=logging.INFO)
    await telethon_client.start(phone=TELEGRAM_PHONE)
    print("✅ Telethon подключён")
    asyncio.create_task(monitor_insiders_periodically())
    asyncio.create_task(scheduled_fetch_matches())
    asyncio.create_task(scheduled_daily_summary())
    asyncio.create_task(scheduled_match_reminder())
    print("🤖 Бузова в Майами запущена...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
