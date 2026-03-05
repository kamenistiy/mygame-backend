# main.py

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sqlite3
from typing import Optional

# Создаём приложение FastAPI
app = FastAPI()

from fastapi.middleware.cors import CORSMiddleware

# Разрешаем запросы отовсюду (для разработки)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшне заменишь на конкретный адрес
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- Работа с базой данных ---
def init_db():
    """Создаёт таблицу players, если её нет"""
    conn = sqlite3.connect('game.db')  # подключение к файлу базы
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,   -- уникальный ID из Telegram
            name TEXT,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            gold INTEGER DEFAULT 100
        )
    ''')
    conn.commit()
    conn.close()

# Вызываем инициализацию при старте сервера
init_db()

# --- Модели данных (что мы ожидаем от клиента и что отдаём) ---
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

# Получить данные игрока по telegram_id
@app.get("/player/{telegram_id}")
def get_player(telegram_id: int):
    conn = sqlite3.connect('game.db')
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
        # Если игрока нет, можно вернуть пустоту или ошибку
        raise HTTPException(status_code=404, detail="Player not found")

# Создать нового игрока
@app.post("/player")
def create_player(player: PlayerCreate):
    conn = sqlite3.connect('game.db')
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO players (telegram_id, name) VALUES (?, ?)",
            (player.telegram_id, player.name)
        )
        conn.commit()
        # Возвращаем только что созданного игрока
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

# Обновить данные игрока (например, после клика)
@app.put("/player/{telegram_id}")
def update_player(telegram_id: int, update: PlayerUpdate):
    conn = sqlite3.connect('game.db')
    cursor = conn.cursor()
    # Сначала проверим, есть ли игрок
    cursor.execute("SELECT * FROM players WHERE telegram_id = ?", (telegram_id,))
    player = cursor.fetchone()
    if not player:
        conn.close()
        raise HTTPException(status_code=404, detail="Player not found")
    
    # Распаковываем текущие данные игрока
    player_id, tg_id, name, current_level, current_exp, current_gold = player
    
    # Определяем новый опыт (если передан)
    new_exp = current_exp
    if update.exp is not None:
        new_exp = update.exp
    
    # Определяем новое золото (если передан)
    new_gold = current_gold
    if update.gold is not None:
        new_gold = update.gold
    
    # --- Логика расчёта уровня по опыту ---
    
    # Уровни 1-7(каждый последующий требует х2 опыта): 2: 20-39, 3: 40-79, 4: 80-159, 5: 160-319, 6: 320-639, 7: 640-1279(пока финальный).
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
    # Можно добавить больше уровней или придумать формулу посложнее
    
    # Если уровень изменился, обновляем его
    if new_level != current_level:
        # Обновляем уровень в базе
        cursor.execute(
            "UPDATE players SET exp = ?, gold = ?, level = ? WHERE telegram_id = ?",
            (new_exp, new_gold, new_level, telegram_id)
        )
    else:
        # Иначе обновляем только опыт и золото
        cursor.execute(
            "UPDATE players SET exp = ?, gold = ? WHERE telegram_id = ?",
            (new_exp, new_gold, telegram_id)
        )
    
    conn.commit()
    
    # Получаем обновлённые данные
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