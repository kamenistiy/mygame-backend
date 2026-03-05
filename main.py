# main.py

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sqlite3
from typing import Optional

print("=== STARTING APP ===")

app = FastAPI()

# Простой тестовый эндпоинт (оставим для проверки)
@app.get("/ping")
def ping():
    return {"ping": "pong"}

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Работа с базой данных ---
DB_PATH = '/tmp/game.db'  # используем доступную для записи директорию

def init_db():
    print("Initializing database...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            name TEXT,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            gold INTEGER DEFAULT 100
        )
    ''')
    conn.commit()
    conn.close()
    print("Database initialized (table ensured)")

# Вызываем при старте
init_db()

# --- Модели данных ---
class PlayerCreate(BaseModel):
    telegram_id: int
    name: str

class PlayerUpdate(BaseModel):
    exp: Optional[int] = None
    gold: Optional[int] = None
    level: Optional[int] = None

# --- API-эндпоинты ---

@app.get("/")
def root():
    return {"message": "Сервер игры работает!"}

@app.get("/test")
def test():
    return {"message": "test ok"}

@app.get("/player/{telegram_id}")
def get_player(telegram_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM players WHERE telegram_id = ?", (telegram_id,))
    player = cursor.fetchone()
    conn.close()
    if player:
        return {
            "id": player[0],
            "telegram_id": player[1],
            "name": player[2],
            "level": player[3],
            "exp": player[4],
            "gold": player[5]
        }
    else:
        raise HTTPException(status_code=404, detail="Player not found")

@app.post("/player")
def create_player(player: PlayerCreate):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO players (telegram_id, name) VALUES (?, ?)",
            (player.telegram_id, player.name)
        )
        conn.commit()
        new_id = cursor.lastrowid
        cursor.execute("SELECT * FROM players WHERE id = ?", (new_id,))
        new_player = cursor.fetchone()
        conn.close()
        return {
            "id": new_player[0],
            "telegram_id": new_player[1],
            "name": new_player[2],
            "level": new_player[3],
            "exp": new_player[4],
            "gold": new_player[5]
        }
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Player with this telegram_id already exists")

@app.put("/player/{telegram_id}")
def update_player(telegram_id: int, update: PlayerUpdate):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM players WHERE telegram_id = ?", (telegram_id,))
    player = cursor.fetchone()
    if not player:
        conn.close()
        raise HTTPException(status_code=404, detail="Player not found")

    # Распаковка текущих данных
    player_id, tg_id, name, current_level, current_exp, current_gold = player

    new_exp = current_exp if update.exp is None else update.exp
    new_gold = current_gold if update.gold is None else update.gold

    # Логика уровней (оставляем твою)
    new_level = 1
    if new_exp >= 20:
        new_level = 2
    if new_exp >= 40:
        new_level = 3
    if new_exp >= 80:
        new_level = 4
    if new_exp >= 160:
        new_level = 5
    if new_exp >= 320:
        new_level = 6
    if new_exp >= 640:
        new_level = 7

    if new_level != current_level:
        cursor.execute(
            "UPDATE players SET exp = ?, gold = ?, level = ? WHERE telegram_id = ?",
            (new_exp, new_gold, new_level, telegram_id)
        )
    else:
        cursor.execute(
            "UPDATE players SET exp = ?, gold = ? WHERE telegram_id = ?",
            (new_exp, new_gold, telegram_id)
        )

    conn.commit()
    cursor.execute("SELECT * FROM players WHERE telegram_id = ?", (telegram_id,))
    updated = cursor.fetchone()
    conn.close()
    return {
        "id": updated[0],
        "telegram_id": updated[1],
        "name": updated[2],
        "level": updated[3],
        "exp": updated[4],
        "gold": updated[5]
    }

print("=== ALL ROUTES REGISTERED ===")
