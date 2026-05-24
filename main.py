# main.py
print("=== MAIN START ===")

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional, List
import os
import uuid
from uuid import UUID
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
from PIL import Image
import io
from datetime import datetime, timezone

from fastapi import FastAPI
from routers.inventory import router as inventory_router
from routers.notifications import router as notifications_router
from routers.achievements import router as achievements_router
from routers.avatars import router as avatars_router
from routers.players import router as players_router

from core.config import (
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY,
    DB_URL
)
print("=== IMPORTS OK ===")

app = FastAPI()

print("=== FASTAPI CREATED ===")

app.include_router(inventory_router)
app.include_router(notifications_router)
app.include_router(achievements_router)
app.include_router(avatars_router)
app.include_router(players_router)

def is_valid_uuid(uuid_str: str) -> bool:
    try:
        UUID(uuid_str)
        return True
    except ValueError:
        return False
    
print("=== STARTING APP ===")
print("STEP 1")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
print("STEP 2")



# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

import time
import logging
from psycopg2 import OperationalError

# Обновить данные игрока (опыт)
def required_exp(level: int) -> int:
    """Возвращает опыт, необходимый для перехода с level на level+1."""
    if level == 1:
        return 20
    else:
        return 20 * (2 ** (level - 1))



def get_db():
    max_retries = 3

    for i in range(max_retries):
        try:
            conn = psycopg2.connect(
                DB_URL,
                cursor_factory=RealDictCursor,
                sslmode="require"
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
                    INSERT INTO user_avatars (user_id, storage_path, is_active, username)
                    VALUES (%s, %s, false, (SELECT username FROM players WHERE id = %s))
                    ON CONFLICT (user_id, storage_path) DO NOTHING
                """, (user_id, path, user_id))
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

# ========== Вспомогательная функция – проверка, является ли пользователь админом: ==========
def is_admin(user_id: str) -> bool:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT is_admin FROM players WHERE id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row and row['is_admin'] == True



# ========== УДАЛЕНИЕ ПРЕДМЕТА ИЗ ИНВЕНТАРЯ ==========
class RemoveItemRequest(BaseModel):
    item_id: str
    quantity: int = 1


@app.post("/item/use")
def use_item(user_id: str, req: UseItemRequest):
    # 1. Проверяем существование предмета и его тип
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM items WHERE id = %s", (req.item_id,))
    item = cur.fetchone()
    if not item:
        raise HTTPException(status_code=404, detail="Предмет не найден")
    if item['class'] != 'consumable':
        raise HTTPException(status_code=400, detail="Этот предмет нельзя использовать")

    # 2. Специальная логика для фолианта смены Образа (загрузки нового аватара)
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
            INSERT INTO avatar_requests (user_id, status, username)
            VALUES (%s, 'pending', (SELECT username FROM players WHERE id = %s))
            RETURNING id
        """, (user_id, user_id))
        new_request = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {"success": True, "request_id": new_request['id']}


 # 3. Специальная логика для фолианта смены Пути (перераспределение статов за лвл)
    if req.item_id == 'stats_certificate':
        # Создаём отдельное соединение для операций с БД
        with get_db() as conn2:
            with conn2.cursor() as cur2:
                # Получаем уровень игрока и текущие статы
                cur2.execute("""
                    SELECT p.level, ps.body, ps.strength, ps.agility, ps.intellect, ps.free_stat_points
                    FROM players p
                    JOIN player_stats ps ON p.id = ps.user_id
                    WHERE p.id = %s
                """, (user_id,))
                player_stats = cur2.fetchone()
                if not player_stats:
                    raise HTTPException(status_code=404, detail="Player not found")
                
                level = player_stats['level']
                # Очки за уровень: на 1 уровне 2, на каждом следующем +2
                correct_free_points = (level - 1) * 2 + 2
                
                # Сбрасываем характеристики
                cur2.execute("""
                    UPDATE player_stats
                    SET body = 0, strength = 0, agility = 0, intellect = 0,
                        free_stat_points = %s
                    WHERE user_id = %s
                """, (correct_free_points, user_id))
                conn2.commit()
        
        # Пересчитываем производные (max_hp, pat, mat и т.д.)
        recalc_derived_stats(user_id)
        
        # Удаляем один предмет из инвентаря
        removed = remove_item_from_inventory(user_id, req.item_id, req.quantity)
        if not removed:
            raise HTTPException(status_code=400, detail="Недостаточно предметов в инвентаре")
        
        add_notification(user_id, 'system', 'Очки характеристик сброшены', 
                         f'Вы использовали Фолиант смены Пути. Все вложенные очки сброшены, у вас {correct_free_points} свободных очков для распределения.')
        
        return {"success": True, "message": "Очки характеристик сброшены"}

    # Если предмет не фолиант (пока не реализовано)
    raise HTTPException(status_code=400, detail="Использование этого предмета ещё не реализовано")

@app.get("/")
def root():
    return {"message": "Сервер игры работает!"}

@app.get("/ping")
def ping():
    return {"ping": "pong"}

@app.get("/test")
def test():
    return {"test": "ok"}


def add_notification(user_id: str, notif_type: str, title: str, message: str):
    print(f"📢 add_notification: {title} для {user_id}")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO notifications (user_id, type, title, message, expires_at, is_read)
                VALUES (%s, %s, %s, %s, NOW() + INTERVAL '1 year', false)
                RETURNING is_read
            """, (user_id, notif_type, title, message))
            result = cur.fetchone()
            print(f"✅ Вставлено is_read = {result['is_read']}")
            conn.commit()


#Достижение с аватарами 1,5,10   
def grant_achievement_if_not_obtained(user_id: str, achievement_id: str):
    print(f"🎯 START: {achievement_id} для {user_id}")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
            cur.execute("SELECT name, exp_reward, coins_reward FROM achievements WHERE id = %s", (achievement_id,))
            reward = cur.fetchone()
            if not reward:
                print(f"  ❌ Достижение {achievement_id} не найдено")
                return False
            try:
                cur.execute("""
                    INSERT INTO user_achievements (user_id, achievement_id, current_progress, is_unlocked, unlocked_at)
                    VALUES (%s, %s, 1, true, NOW())
                """, (user_id, achievement_id))
                print("  ✅ Вставка успешна")
            except Exception as e:
                print(f"  ⚠️ Ошибка вставки: {e}")
                conn.commit()
                return False

            # Обновляем золото
            cur.execute("UPDATE players SET coins = coins + %s WHERE id = %s", (reward['coins_reward'], user_id))
            
            # Обновляем опыт и уровень 
            cur.execute("SELECT exp, level FROM players WHERE id = %s", (user_id,))
            player = cur.fetchone()
            exp = player['exp'] + reward['exp_reward']
            level = player['level']
            new_level = level
            exp_rem = exp
            while exp_rem >= required_exp(new_level):
                exp_rem -= required_exp(new_level)
                new_level += 1
            cur.execute("UPDATE players SET exp = %s, level = %s WHERE id = %s", (exp_rem, new_level, user_id))
            
            conn.commit()
            print(f"  ✅ Награды выданы: +{reward['exp_reward']} опыта, +{reward['coins_reward']} монет, уровень {level} -> {new_level}")
            
            # Уведомление
            add_notification(user_id, 'achievement', f'Достижение "{reward["name"]}" получено!',
                             f'Награда: +{reward["exp_reward"]} опыта, +{reward["coins_reward"]} монет.')
            return True
        
def regen_energy_if_needed(user_id: str):
    """Проверяет, сколько прошло времени с last_energy_regen, и добавляет +1 энергии за каждые 10 минут."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_energy, max_energy, last_energy_regen FROM player_stats WHERE user_id = %s", (user_id,))
            stats = cur.fetchone()
            if not stats:
                return
            now = datetime.now(timezone.utc)
            last = stats['last_energy_regen']
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            diff_seconds = (now - last).total_seconds()
            minutes_passed = diff_seconds // 600  # 10 минут = 600 секунд
            if minutes_passed <= 0:
                return
            new_energy = min(stats['current_energy'] + int(minutes_passed), stats['max_energy'])
            if new_energy != stats['current_energy']:
                cur.execute("""
                    UPDATE player_stats
                    SET current_energy = %s, last_energy_regen = NOW()
                    WHERE user_id = %s
                """, (new_energy, user_id))
                conn.commit()
        
def recalc_derived_stats(user_id: str):
    """Пересчитывает производные характеристики на основе level, body, strength, agility, intellect."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.level, ps.body, ps.strength, ps.agility, ps.intellect
                FROM players p
                JOIN player_stats ps ON p.id = ps.user_id
                WHERE p.id = %s
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                return
            level = row['level']
            body = row['body']
            strength = row['strength']
            agility = row['agility']
            intellect = row['intellect']
            
            max_hp = 100 + (level - 1) * 10 + body * 10
            max_mana = 100 + (level - 1) * 10 + intellect * 10
            pat = strength * 5
            mat = intellect * 5
            sp = intellect * 5          # 0.5% за очко => 5 = 0.5%
            pdf = body * 5
            mdf = body * 5
            awr = body * 5              # 0.5% за очко
            spd = agility * 10 - body * 5    # 1% за ловкость, -0.5% за телосложение
            acc = agility * 5
            ddg = agility * 5
            gat = strength * 5          # 0.5% за очко
            
            cur.execute("""
                UPDATE player_stats
                SET max_hp = %s,
                    max_mana = %s,
                    pat = %s,
                    mat = %s,
                    sp = %s,
                    pdf = %s,
                    mdf = %s,
                    awr = %s,
                    spd = %s,
                    acc = %s,
                    ddg = %s,
                    gat = %s
                WHERE user_id = %s
            """, (max_hp, max_mana, pat, mat, sp, pdf, mdf, awr, spd, acc, ddg, gat, user_id))
            
            # Корректируем текущие HP/Mana, если они превышают новые максимумы
            cur.execute("""
                UPDATE player_stats
                SET current_hp = LEAST(current_hp, max_hp),
                    current_mana = LEAST(current_mana, max_mana)
                WHERE user_id = %s
            """, (user_id,))
            conn.commit()

        
print("=== MAIN END ===")
print("=== ALL ROUTES REGISTERED ===")
@app.get("/ping")
def ping():
    return {"status": "ok"}


