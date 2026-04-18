# main.py

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional, List
import os
import uuid
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from PIL import Image
import io

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
SUPABASE_URL = "https://onkpedemixygmtllrehp.supabase.co"
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9ua3BlZGVtaXh5Z210bGxyZWhwIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MzA0NzYzOSwiZXhwIjoyMDg4NjIzNjM5fQ.LSUU4rwARPZNhCyeXYdsW4qeffD2UD524KYlFRNz9U8"  # НЕ anon key! Получи в настройках Supabase: Project Settings -> API -> service_role key (сохрани в секрете)
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
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
# ========== МОДЕЛь ответа на заявку о смене аватара ==========
class AvatarReviewRequest(BaseModel):
    request_id: str
    action: str  # 'approve' или 'reject'
    reason: Optional[str] = None
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
# ========== Вспомогательная функция – проверка, является ли пользователь админом: ==========
def is_admin(user_id: str) -> bool:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT is_admin FROM players WHERE id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row and row['is_admin'] == True
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

# Эндпоинт – получение списка заявок (только для админа):
@app.get("/admin/avatar-requests")
def get_avatar_requests(user_id: str):
    if not is_admin(user_id):
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT ar.id, ar.user_id, p.username, ar.original_filename, ar.status, ar.created_at, ar.storage_path, ar.reason
        FROM avatar_requests ar
        JOIN players p ON ar.user_id = p.id
        ORDER BY ar.created_at DESC
    """)
    requests = cur.fetchall()
    cur.close()
    conn.close()
    return {"requests": requests}

#   Эндпоинт – обработка заявки (одобрить/отклонить):
@app.post("/admin/avatar-review")
def review_avatar(req: AvatarReviewRequest, admin_user_id: str):
    if not is_admin(admin_user_id):
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    
    conn = get_db()
    cur = conn.cursor()
    
    # Получаем заявку
    cur.execute("SELECT user_id, storage_path, status FROM avatar_requests WHERE id = %s", (req.request_id,))
    request = cur.fetchone()
    if not request:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if request['status'] != 'pending':
        raise HTTPException(status_code=400, detail="Заявка уже обработана")
    
    user_id = request['user_id']
    storage_path = request['storage_path']
    
    if req.action == 'approve':
        # 1. Перемещаем файл из pending в approved
        if storage_path:
            new_path = storage_path.replace('pending/', 'approved/')
            try:
                # Копируем файл (move не работает напрямую, используем copy + delete)
                file_data = supabase.storage.from_("avatars").download(storage_path)
                supabase.storage.from_("avatars").upload(new_path, file_data)
                supabase.storage.from_("avatars").remove([storage_path])
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Ошибка перемещения файла: {e}")
        else:
            new_path = None
        
        # 2. Добавляем запись в библиотеку аватаров
        cur.execute("""
            INSERT INTO user_avatars (user_id, storage_path, is_active)
            VALUES (%s, %s, %s)
            RETURNING id
        """, (user_id, new_path, False))
        avatar_id = cur.fetchone()['id']
                # 2.1. Если у пользователя ещё нет активного аватара, сделаем этот активным
        cur.execute("SELECT id FROM user_avatars WHERE user_id = %s AND is_active = true", (user_id,))
        active = cur.fetchone()
        if not active:
            cur.execute("UPDATE user_avatars SET is_active = true WHERE id = %s", (avatar_id,))

        # 3. Обновляем статус заявки
        cur.execute("""
            UPDATE avatar_requests
            SET status = 'approved', reviewed_at = NOW()
            WHERE id = %s
        """, (req.request_id,))
        
    elif req.action == 'reject':
        # 1. Удаляем файл из Storage (если есть)
        if storage_path:
            try:
                supabase.storage.from_("avatars").remove([storage_path])
            except:
                pass
        
        # 2. Обновляем статус заявки с причиной отказа
        cur.execute("""
            UPDATE avatar_requests
            SET status = 'rejected', reason = %s, reviewed_at = NOW()
            WHERE id = %s
        """, (req.reason, req.request_id))
        
        # 3. Возвращаем фолиант в инвентарь
        cur.execute("""
            INSERT INTO inventory (user_id, item_id, quantity)
            VALUES (%s, 'avatar_certificate', 1)
            ON CONFLICT (user_id, item_id)
            DO UPDATE SET quantity = inventory.quantity + 1
        """, (user_id,))
    
    else:
        raise HTTPException(status_code=400, detail="Неверное действие")
    
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}

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

# Библиотека аватаров, редактирование профиля.
@app.get("/user-avatars/{user_id}")
def get_user_avatars(user_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, storage_path, is_active FROM user_avatars
        WHERE user_id = %s
        ORDER BY created_at DESC
    """, (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    avatars = []
    for row in rows:
        url = supabase.storage.from_("avatars").get_public_url(row['storage_path']) if row['storage_path'] else None
        avatars.append({
            "id": row['id'],
            "url": url,
            "is_active": row['is_active']
        })
    return {"avatars": avatars}

@app.post("/profile/update")
def update_profile(req: dict):
    user_id = req.get('user_id')
    motto = req.get('motto', '')
    bio = req.get('bio', '')
    avatar_id = req.get('avatar_id')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE players SET motto = %s, bio = %s WHERE id = %s", (motto, bio, user_id))
    if avatar_id:
        # Сбросить активный флаг у всех аватаров пользователя
        cur.execute("UPDATE user_avatars SET is_active = false WHERE user_id = %s", (user_id,))
        cur.execute("UPDATE user_avatars SET is_active = true WHERE id = %s AND user_id = %s", (avatar_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}

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

@app.post("/avatar/upload")
async def upload_avatar(
    user_id: str = Form(...),
    request_id: str = Form(...),
    file: UploadFile = File(...)
):
    # 1. Проверка заявки
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT status FROM avatar_requests WHERE id = %s AND user_id = %s", (request_id, user_id))
    req = cur.fetchone()
    if not req or req['status'] != 'pending':
        cur.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Заявка не найдена или уже обработана")

    # 2. Проверки файла
    if file.size > 80 * 1024:
        raise HTTPException(status_code=400, detail="Файл превышает 80 KB")
    if file.content_type not in ['image/png', 'image/webp', 'image/gif']:
        raise HTTPException(status_code=400, detail="Разрешены только PNG, WebP, GIF")

    # 3. Читаем содержимое файла (ОДИН РАЗ!)
    contents = await file.read()
    original_filename = file.filename

    # 4. Проверка размеров через Pillow
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(contents))
        width, height = img.size
        if width != height:
            raise HTTPException(status_code=400, detail="Изображение должно быть квадратным (1:1)")
        if width < 150 or height < 150:
            raise HTTPException(status_code=400, detail="Минимальный размер 150×150 пикселей")
        if width > 512:
            raise HTTPException(status_code=400, detail="Максимальный размер 512×512 пикселей")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Не удалось прочитать изображение: {e}")

    # 5. Генерация пути
    ext = file.filename.split('.')[-1].lower()
    file_name = f"{user_id}_{uuid.uuid4()}.{ext}"
    file_path = f"pending/{file_name}"

    # 6. Загрузка в Storage
    try:
        res = supabase.storage.from_("avatars").upload(file_path, contents)
        if hasattr(res, 'error') and res.error:
            raise Exception(str(res.error))
    except Exception as e:
        cur.close()
        conn.close()
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки в хранилище: {e}")

    # 7. Обновление заявки
    cur.execute("UPDATE avatar_requests SET storage_path = %s, original_filename = %s WHERE id = %s",
            (file_path, original_filename, request_id))
    conn.commit()
    cur.close()
    conn.close()

    return {"success": True}
   #Отображение Аватаров пользователей в их профиле
@app.get("/user-avatar/{user_id}")
def get_user_avatar(user_id: str):
    conn = get_db()
    cur = conn.cursor()
    # Ищем активный аватар (is_active = true)
    cur.execute("""
        SELECT storage_path FROM user_avatars
        WHERE user_id = %s AND is_active = true
        LIMIT 1
    """, (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        # Формируем публичную ссылку на файл в Storage
        public_url = supabase.storage.from_("avatars").get_public_url(row['storage_path'])
        return {"avatar_url": public_url}
    else:
        return {"avatar_url": None}
print("=== ALL ROUTES REGISTERED ===")


