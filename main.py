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

    
# ========== МОДЕЛИ ДЛЯ ИНВЕНТАРЯ ==========
class AddItemRequest(BaseModel):
    item_id: str
    quantity: int = 1

class UseItemRequest(BaseModel):
    item_id: str
    quantity: int = 1

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def remove_item_from_inventory(user_id: str, item_id: str, quantity: int = 1) -> bool:
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT quantity FROM inventory WHERE user_id = %s AND item_id = %s", (user_id, item_id))
        row = cur.fetchone()
        if not row or row['quantity'] < quantity:
            return False
        new_qty = row['quantity'] - quantity
        if new_qty == 0:
            cur.execute("DELETE FROM inventory WHERE user_id = %s AND item_id = %s", (user_id, item_id))
        else:
            cur.execute("UPDATE inventory SET quantity = %s WHERE user_id = %s AND item_id = %s", (new_qty, user_id, item_id))
        conn.commit()
        return True
    except Exception as e:
        print("Error removing item:", e)
        return False
    finally:
        cur.close()
        conn.close()

# ========== ЭНДПОИНТЫ ==========
@app.get("/inventory/{user_id}")
def get_inventory(user_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT i.id, i.name, i.description, i.type, i.class, i.icon, inv.quantity
        FROM inventory inv
        JOIN items i ON inv.item_id = i.id
        WHERE inv.user_id = %s
    """, (user_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    return items

@app.post("/inventory/add")
def add_item(user_id: str, req: AddItemRequest):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO inventory (user_id, item_id, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, item_id)
            DO UPDATE SET quantity = inventory.quantity + EXCLUDED.quantity
            RETURNING *
        """, (user_id, req.item_id, req.quantity))
        result = cur.fetchone()
        conn.commit()
        return {"success": True, "item": result}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.post("/item/use")
def use_item(user_id: str, req: UseItemRequest):
    # 1. Проверяем существование предмета и его тип
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM items WHERE id = %s", (req.item_id,))
    item = cur.fetchone()
    if not item:
        raise HTTPException(status_code=404, detail="Предмет не найден")
    if item['type'] != 'consumable':
        raise HTTPException(status_code=400, detail="Этот предмет нельзя использовать")

    # 2. Специальная логика для фолианта
    if req.item_id == 'avatar_certificate':
        # Проверяем, нет ли уже активной заявки
        cur.execute("SELECT id FROM avatar_requests WHERE user_id = %s AND status = 'pending'", (user_id,))
        pending = cur.fetchone()
        if pending:
            cur.close()
            conn.close()
            raise HTTPException(status_code=400, detail="У вас уже есть аватар на модерации. Дождитесь решения.")

        # Удаляем один фолиант из инвентаря
        removed = remove_item_from_inventory(user_id, req.item_id, req.quantity)
        if not removed:
            cur.close()
            conn.close()
            raise HTTPException(status_code=400, detail="Недостаточно предметов в инвентаре")

        # Создаём заявку на аватар
        cur = conn.cursor()  # пересоздаём, т.к. remove_item_from_inventory закрыла соединение
        cur.execute("""
            INSERT INTO avatar_requests (user_id, status)
            VALUES (%s, 'pending')
            RETURNING id
        """, (user_id,))
        new_request = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {"success": True, "request_id": new_request['id']}

    # Если предмет не фолиант (пока не реализовано)
    raise HTTPException(status_code=400, detail="Использование этого предмета ещё не реализовано")
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

    # Определяем новое золото (если передано)
    new_gold = current_gold if update.gold is None else update.gold

    # Определяем новый уровень и опыт
    new_level = current_level
    new_exp = current_exp

    # Если передан опыт, добавляем его и пересчитываем уровень
    if update.exp is not None:
        exp_to_add = update.exp
        temp_exp = current_exp + exp_to_add
        print(f"🔹 Before: level={current_level}, exp={current_exp}, add={exp_to_add}, temp={temp_exp}")
        while temp_exp >= required_exp(new_level):
            print(f"   Level up: {new_level} -> {new_level+1}, need={required_exp(new_level)}, temp={temp_exp}")
            temp_exp -= required_exp(new_level)
            new_level += 1
        new_exp = temp_exp
        print(f"🔹 After: level={new_level}, exp={new_exp}")

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


