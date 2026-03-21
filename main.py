# main.py

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional
import os
from fastapi.middleware.cors import CORSMiddleware

print("=== STARTING APP ===")

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Подключение к PostgreSQL (Supabase) ---
# (используй ту же строку, что и раньше, с портом 5432 или 6543)
DB_URL = "postgresql://postgres.onkpedemixygmtllrehp:6rQ7yNV2gjIsttit@db.onkpedemixygmtllrehp.supabase.co:5432/postgres?sslmode=require&hostaddr=3.71.225.44"

def get_db():
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    return conn

# --- Модели данных ---
class PlayerUpdate(BaseModel):
    exp: Optional[int] = None
    gold: Optional[int] = None
    level: Optional[int] = None

# --- Эндпоинты ---

@app.get("/")
def root():
    return {"message": "Сервер игры работает!"}

@app.get("/ping")
def ping():
    return {"ping": "pong"}

@app.get("/test")
def test():
    return {"test": "ok"}

# Получить данные игрока по UUID
@app.get("/player/{user_id}")
def get_player(user_id: str):
    conn = get_db()
    cur = conn.cursor()
    print(f"Получен запрос для user_id: {user_id}")
    cur.execute("SELECT * FROM players WHERE id = %s", (user_id,))
    player = cur.fetchone()
    cur.close()
    conn.close()
    if player:
        return player
    else:
        raise HTTPException(status_code=404, detail="Player not found")

# Обновить данные игрока
def required_exp(level: int) -> int:
    """Возвращает опыт, необходимый для перехода с level на level+1."""
    if level == 1:
        return 20
    else:
        return 20 * (2 ** (level - 1))

@app.put("/player/{user_id}")
def update_player(user_id: str, update: PlayerUpdate):
    conn = get_db()
    cur = conn.cursor()
    # Проверяем существование игрока
    cur.execute("SELECT * FROM players WHERE id = %s", (user_id,))
    player = cur.fetchone()
    if not player:
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Player not found")

    # Текущие значения
    current_exp = player["exp"]
    current_gold = player["gold"]
    current_level = player["level"]

    # Новые значения (если переданы)
    new_exp = current_exp if update.exp is None else update.exp
    new_gold = current_gold if update.gold is None else update.gold
    new_level = current_level

    # Если передан опыт, пересчитываем уровень
    if update.exp is not None:
        temp_exp = current_exp + update.exp
        while temp_exp >= required_exp(new_level):
            temp_exp -= required_exp(new_level)
            new_level += 1
        new_exp = temp_exp

    # Обновляем запись
    cur.execute(
        "UPDATE players SET exp = %s, gold = %s, level = %s WHERE id = %s RETURNING *",
        (new_exp, new_gold, new_level, user_id)
    )
    updated = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return updated

# Админка: список всех игроков
@app.get("/admin/players")
def list_players():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, serial_number, username, level, exp, gold, created_at FROM players")
    players = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM players")
    total = cur.fetchone()['count']
    cur.close()
    conn.close()
    return {"total": total, "players": players}

print("=== ALL ROUTES REGISTERED ===")

