# main.py

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional
import os

print("=== STARTING APP ===")

app = FastAPI()

# --- Тестовый эндпоинт ---
@app.get("/ping")
def ping():
    return {"ping": "pong"}

# --- CORS ---
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Подключение к PostgreSQL ---
# ВАЖНО: замени на свою Internal Connection String
DB_URL = "postgresql://postgres.onkpedemixygmtllrehp:6rQ7yNV2gjIsttit@db.onkpedemixygmtllrehp.supabase.co:5432/postgres?sslmode=require&hostaddr=3.71.225.44"

def get_db():
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    return conn

# --- Инициализация таблицы ---
def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE,
            name TEXT,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            gold INTEGER DEFAULT 100
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("Database initialized (PostgreSQL)")

init_db()

# --- Модели данных ---
class PlayerCreate(BaseModel):
    telegram_id: int
    name: str

class PlayerUpdate(BaseModel):
    exp: Optional[int] = None
    gold: Optional[int] = None
    level: Optional[int] = None

# --- Эндпоинты ---

@app.get("/")
def root():
    return {"message": "Сервер игры работает!"}

@app.get("/test")
def test():
    return {"message": "test ok"}

@app.get("/player/{telegram_id}")
def get_player(telegram_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM players WHERE telegram_id = %s", (telegram_id,))
    player = cur.fetchone()
    cur.close()
    conn.close()
    if player:
        return {
            "id": player["id"],
            "telegram_id": player["telegram_id"],
            "name": player["name"],
            "level": player["level"],
            "exp": player["exp"],
            "gold": player["gold"]
        }
    else:
        raise HTTPException(status_code=404, detail="Player not found")

@app.post("/player")
def create_player(player: PlayerCreate):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO players (telegram_id, name) VALUES (%s, %s) RETURNING *",
            (player.telegram_id, player.name)
        )
        new_player = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {
            "id": new_player["id"],
            "telegram_id": new_player["telegram_id"],
            "name": new_player["name"],
            "level": new_player["level"],
            "exp": new_player["exp"],
            "gold": new_player["gold"]
        }
    except psycopg2.IntegrityError:
        conn.rollback()
        cur.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Player with this telegram_id already exists")

@app.put("/player/{telegram_id}")
def update_player(telegram_id: int, update: PlayerUpdate):
    conn = get_db()
    cur = conn.cursor()
    # Сначала получаем текущие данные
    cur.execute("SELECT * FROM players WHERE telegram_id = %s", (telegram_id,))
    player = cur.fetchone()
    if not player:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Player not found")
    # Распаковываем текущие значения
    current_exp = player["exp"]
    current_gold = player["gold"]
    current_level = player["level"]

    # Определяем новые значения (если переданы в запросе)
    new_exp = current_exp if update.exp is None else update.exp
    new_gold = current_gold if update.gold is None else update.gold

    
       # --- Универсальная логика расчёта уровня по опыту ---
    # Формула: для уровня 1: опыт 0-19
    # Для уровня n (n>=2): минимальный опыт = 20 * 2^(n-2)
    # Порог для перехода на следующий уровень удваивается
    new_level = 1
    if new_exp >= 20:
        # Находим максимальный уровень, для которого опыт >= порога
        # Порог для уровня n: 20 * 2^(n-2)
        # Решаем неравенство: 20 * 2^(level-2) <= new_exp
        # 2^(level-2) <= new_exp / 20
        # level-2 <= log2(new_exp/20)
        # level <= log2(new_exp/20) + 2
        # Используем целочисленную арифметику для избежания ошибок с плавающей точкой
        exp_for_level = new_exp
        # Начинаем с уровня 2
        level_candidate = 2
        while True:
            min_exp_for_level = 20 * (2 ** (level_candidate - 2))
            if exp_for_level < min_exp_for_level:
                break
            level_candidate += 1
        new_level = level_candidate - 1

    # Обновляем
    cur.execute(
        "UPDATE players SET exp = %s, gold = %s, level = %s WHERE telegram_id = %s RETURNING *",
        (new_exp, new_gold, new_level, telegram_id)
    )
    updated = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return {
        "id": updated["id"],
        "telegram_id": updated["telegram_id"],
        "name": updated["name"],
        "level": updated["level"],
        "exp": updated["exp"],
        "gold": updated["gold"]
    }

@app.get("/admin/players")
def list_players():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, telegram_id, name, level, exp, gold FROM players")
    players = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) FROM players")
    total = cursor.fetchone()['count']
    conn.close()
    return {"total": total, "players": players}

print("=== ALL ROUTES REGISTERED ===")

