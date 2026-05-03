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

import os

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
DB_URL = os.environ.get("DB_URL")

import time
import logging
from psycopg2 import OperationalError

def get_db():
    max_retries = 3
    for i in range(max_retries):
        try:
            conn = psycopg2.connect(
                DB_URL,
                cursor_factory=RealDictCursor,
                prepare_threshold=0  # обязательно для Transaction Pooler
            )
            return conn
        except OperationalError as e:
            if i == max_retries - 1:
                raise
            print(f"Попытка {i+1} не удалась: {e}")
            time.sleep(2 ** i)

# Проверка, что переменные заданы (опционально, но полезно для отладки)
if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not DB_URL:
    raise ValueError("Не заданы обязательные переменные окружения: SUPABASE_URL, SUPABASE_SERVICE_KEY, DB_URL")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
def get_db():
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    return conn

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ  ==========
def add_default_avatars_for_user(user_id: str):
    """Добавляет стандартные аватары пользователю"""
    with get_db() as conn:
        with conn.cursor() as cur:
            avatars = ['default_avatars/E.png', 'default_avatars/F.png', 'default_avatars/G.png',
                       'default_avatars/H.png', 'default_avatars/I.png', 'default_avatars/K.png',
                       'default_avatars/M.png', 'default_avatars/S.png', 'default_avatars/V.png',
                       'default_avatars/X.png']
            for path in avatars:
                cur.execute("""
                    INSERT INTO user_avatars (user_id, storage_path, is_active)
                    VALUES (%s, %s, false)
                    ON CONFLICT (user_id, storage_path) DO NOTHING
                """, (user_id, path))
            conn.commit()

def add_achievement_for_user(user_id: str, achievement_id: str):
    """Добавляет достижение пользователю"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_achievements (user_id, achievement_id, current_progress, is_unlocked, unlocked_at)
                VALUES (%s, %s, 1, true, NOW())
                ON CONFLICT (user_id, achievement_id) DO NOTHING
            """, (user_id, achievement_id))
            conn.commit()

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
    action: str
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
    print(f"✅ review_avatar вызван: request_id={req.request_id}, action={req.action}, admin={admin_user_id}")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SET statement_timeout = 0")

    # Получаем заявку
    cur.execute("SELECT user_id, storage_path, status, original_filename FROM avatar_requests WHERE id = %s", (req.request_id,))
    request = cur.fetchone()
    if not request:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if request['status'] != 'pending':
        raise HTTPException(status_code=400, detail="Заявка уже обработана")
    
    user_id = request['user_id']
    storage_path = request['storage_path']
    
    if req.action == 'approve':
        print(f"Одобрение заявки {req.request_id}, storage_path = {storage_path}")
        new_path = storage_path
        
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
          # Увеличиваем счётчик одобренных аватаров пользователя
        cur.execute("UPDATE players SET approved_avatars_count = approved_avatars_count + 1 WHERE id = %s RETURNING approved_avatars_count", (user_id,))
        new_count = cur.fetchone()['approved_avatars_count']

            # Выдаём достижения (проверяем пороги)
        grant_achievement_if_not_obtained(user_id, 'avatar_lover')
        if new_count >= 5:
                grant_achievement_if_not_obtained(user_id, 'avatar_lover_5')
        if new_count >= 10:
                grant_achievement_if_not_obtained(user_id, 'avatar_lover_10')

            # Отправляем уведомление об одобрении (используем original_filename из request)
        add_notification(user_id, 'system', 'Аватар одобрен',
                             f'Ваша заявка на файл "{request["original_filename"]}" одобрена. Аватар добавлен в библиотеку профиля.')
        
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


# Обновить данные игрока
def required_exp(level: int) -> int:
    """Возвращает опыт, необходимый для перехода с level на level+1."""
    if level == 1:
        return 20
    else:
        return 20 * (2 ** (level - 1))

@app.get("/player/{user_id}")
def get_player(user_id: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM players WHERE id = %s", (user_id,))
            player = cur.fetchone()
            if player:
                return player
            else:
                # 1. Пытаемся получить username из auth.users
                try:
                    # Используем Admin API – требует service_role ключ
                    user_data = supabase.auth.admin.get_user_by_id(user_id)
                    username = user_data.user.user_metadata.get('username', 'Player')
                except Exception as e:
                    print(f"Ошибка получения username из Auth: {e}")
                    # Если не получилось – генерируем временное имя (или можно оставить заглушку)
                    username = "Player_" + user_id[:8]

                # 2. Создаём запись в таблице players
                cur.execute("""
                    INSERT INTO players (id, username, level, exp, gold, created_at)
                    VALUES (%s, %s, 1, 0, 0, NOW())
                    RETURNING *
                """, (user_id, username))
                player = cur.fetchone()
                conn.commit()

                # 3. Добавляем стандартные аватары и достижение
                add_default_avatars_for_user(user_id)
                add_achievement_for_user(user_id, 'alpha_tester')

                add_notification(user_id, 'system', 'Добро пожаловать!', 
                    f'Привет, {username}! Рады видеть тебя в Fastened World. Найди друзей, осваивай мир и получай удовольствие от игры!')
                add_notification(user_id, 'system', 'Стартовые Аватары', 
                    'Вы получили 10 стартовых Аватаров! Применить их можно во вкладке "Профиль", нажав на значок шестерни.')
                add_notification(user_id, 'achievement', 'Благодарность', 
                    'Вы получили достижение "Благодарность" за участие в альфа-тесте.')
                
                return player
   

@app.post("/player/{user_id}")
def update_player(user_id: str, update: PlayerUpdate):
    with get_db() as conn:
        with conn.cursor() as cur:
            # Проверяем существование игрока
            cur.execute("SELECT * FROM players WHERE id = %s", (user_id,))
            player = cur.fetchone()
            if not player:
                raise HTTPException(status_code=404, detail="Player not found")

            # Текущие значения
            current_exp = player["exp"]
            current_gold = player["gold"]
            current_level = player["level"]

            # Новое золото
            new_gold = current_gold if update.gold is None else update.gold

            # Обработка опыта (с повышением уровня)
            new_level = current_level
            new_exp = current_exp
            if update.exp is not None:
                exp_to_add = update.exp
                temp_exp = current_exp + exp_to_add
                while temp_exp >= required_exp(new_level):
                    temp_exp -= required_exp(new_level)
                    new_level += 1
                new_exp = temp_exp

            # Обновление в БД
            cur.execute(
                "UPDATE players SET exp = %s, gold = %s, level = %s WHERE id = %s RETURNING *",
                (new_exp, new_gold, new_level, user_id)
            )
            updated = cur.fetchone()
            conn.commit()
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
        if width != 150 or height != 150:
            raise HTTPException(status_code=400, detail="Аватар должен быть строго 150×150 пикселей")
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
    
    
@app.get("/my-avatar-requests")
def get_my_requests(user_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, original_filename, status, reason, created_at
        FROM avatar_requests
        WHERE user_id = %s
        ORDER BY created_at DESC
    """, (user_id,))
    requests = cur.fetchall()
    cur.close()
    conn.close()
    return {"requests": requests}

@app.get("/achievements/all")
def get_all_achievements():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, description, icon, max_progress, exp_reward, gold_reward FROM achievements ORDER BY id")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"achievements": rows}

@app.get("/achievements/progress")
def get_user_achievement_progress(user_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT achievement_id, current_progress, is_unlocked FROM user_achievements WHERE user_id = %s", (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"progress": rows}

@app.get("/achievements/pinned")
def get_pinned_achievements(user_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT position, achievement_id FROM user_pinned_achievements WHERE user_id = %s ORDER BY position", (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"pinned": rows}

@app.post("/achievements/pin")
def pin_achievement(req: dict):
    user_id = req.get('user_id')
    position = req.get('position')
    achievement_id = req.get('achievement_id')
    conn = get_db()
    cur = conn.cursor()
    # Удаляем старую запись на этой позиции, если есть
    cur.execute("DELETE FROM user_pinned_achievements WHERE user_id = %s AND position = %s", (user_id, position))
    if achievement_id:
        cur.execute("INSERT INTO user_pinned_achievements (user_id, position, achievement_id) VALUES (%s, %s, %s)",
                    (user_id, position, achievement_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}

@app.post("/achievements/update_progress")
def update_achievement_progress(req: dict):
    user_id = req.get('user_id')
    achievement_id = req.get('achievement_id')
    increment = req.get('increment', 1)
    conn = get_db()
    cur = conn.cursor()
    # Получить текущий прогресс
    cur.execute("SELECT current_progress, max_progress FROM achievements a JOIN user_achievements ua ON a.id = ua.achievement_id WHERE ua.user_id = %s AND ua.achievement_id = %s", (user_id, achievement_id))
    row = cur.fetchone()
    if not row:
        # Создать запись
        cur.execute("INSERT INTO user_achievements (user_id, achievement_id, current_progress) VALUES (%s, %s, %s)", (user_id, achievement_id, 0))
        current = 0
        max_prog = (cur.execute("SELECT max_progress FROM achievements WHERE id = %s", (achievement_id,))).fetchone()['max_progress']
    else:
        current = row['current_progress']
        max_prog = row['max_progress']
    new_progress = min(current + increment, max_prog)
    cur.execute("UPDATE user_achievements SET current_progress = %s WHERE user_id = %s AND achievement_id = %s", (new_progress, user_id, achievement_id))
    if new_progress >= max_prog and not row['is_unlocked']:
        cur.execute("UPDATE user_achievements SET is_unlocked = true, unlocked_at = NOW() WHERE user_id = %s AND achievement_id = %s", (user_id, achievement_id))
        # Можно добавить выдачу награды (опыт, золото)
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}

        
@app.get("/notifications")
def get_notifications(user_id: str, type_filter: str = 'all', search: str = '', limit: int = 100):
    with get_db() as conn:
        with conn.cursor() as cur:
            query = """
                SELECT id, type, title, message, created_at, is_read
                FROM notifications
                WHERE user_id = %s AND expires_at > NOW()
            """
            params = [user_id]
            if type_filter != 'all':
                query += " AND type = %s"
                params.append(type_filter)
            if search:
                query += " AND (title ILIKE %s OR message ILIKE %s)"
                params.extend([f'%{search}%', f'%{search}%'])
            query += " ORDER BY created_at DESC LIMIT %s"
            params.append(limit)
            cur.execute(query, params)
            rows = cur.fetchall()
            return rows
        
def add_notification(user_id: str, notif_type: str, title: str, message: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
    INSERT INTO notifications (user_id, type, title, message, expires_at, is_read)
    VALUES (%s, %s, %s, %s, NOW() + INTERVAL '1 year', false)
""", (user_id, notif_type, title, message))
            conn.commit()
            
@app.get("/notifications/unread/count")
def get_unread_count(user_id: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND is_read = false AND expires_at > NOW()", (user_id,))
            count = cur.fetchone()['count']
            return {"unread_count": count}
        
@app.post("/notifications/mark_read")
def mark_notifications_read(user_id: str, notification_ids: List[str] = None):
    with get_db() as conn:
        with conn.cursor() as cur:
            if notification_ids:
                cur.execute("UPDATE notifications SET is_read = true WHERE user_id = %s AND id = ANY(%s)", (user_id, notification_ids))
            else:
                cur.execute("UPDATE notifications SET is_read = true WHERE user_id = %s AND expires_at > NOW()", (user_id,))
            conn.commit()
            return {"success": True}
        
#Достижение с аватарами 1,5,10   
def grant_achievement_if_not_obtained(user_id: str, achievement_id: str):
    """Выдаёт достижение игроку, если оно ещё не получено, и начисляет награду + уведомление."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            # Проверяем, есть ли уже и получено ли
            cur.execute("SELECT is_unlocked FROM user_achievements WHERE user_id = %s AND achievement_id = %s", (user_id, achievement_id))
            row = cur.fetchone()
            if row and row['is_unlocked']:
                return False  # уже есть

            # Получаем награды и название достижения
            cur.execute("SELECT name, exp_reward, gold_reward FROM achievements WHERE id = %s", (achievement_id,))
            reward = cur.fetchone()
            if not reward:
                return False

            # Вставляем или обновляем достижение
            cur.execute("""
                INSERT INTO user_achievements (user_id, achievement_id, current_progress, is_unlocked, unlocked_at)
                VALUES (%s, %s, 1, true, NOW())
                ON CONFLICT (user_id, achievement_id) DO UPDATE
                SET is_unlocked = true, unlocked_at = NOW()
            """, (user_id, achievement_id))

            # Начисляем опыт и золото
            cur.execute("UPDATE players SET exp = exp + %s, gold = gold + %s WHERE id = %s",
                        (reward['exp_reward'], reward['gold_reward'], user_id))

            # Отправляем уведомление о получении достижения
            add_notification(user_id, 'achievement', f'Достижение "{reward["name"]}" получено!',
                             f'Награда: +{reward["exp_reward"]} опыта, +{reward["gold_reward"]} золота.')

            conn.commit()
            return True
        

print("=== ALL ROUTES REGISTERED ===")


