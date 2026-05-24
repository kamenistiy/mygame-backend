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
from services.inventory_service import use_item_logic
from services.notification_service import add_notification

from core.config import (
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY,
    DB_URL
)
print("=== IMPORTS OK ===")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://mygame-frontend.vercel.app",
        "http://localhost:3000",
        "*"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

print("=== FASTAPI CREATED ===")

app.include_router(inventory_router)
app.include_router(notifications_router)
app.include_router(achievements_router)
app.include_router(avatars_router)
app.include_router(players_router)

class UseItemRequest(BaseModel):
    item_id: str
    quantity: int = 1

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

# Проверка, что переменные заданы (опционально, но полезно для отладки)
if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not DB_URL:
    raise ValueError("Не заданы обязательные переменные окружения: SUPABASE_URL, SUPABASE_SERVICE_KEY, DB_URL")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

@app.get("/")
def root():
    return {"message": "Сервер игры работает!"}

@app.get("/ping")
def ping():
    return {"ping": "pong"}

@app.get("/test")
def test():
    return {"test": "ok"}

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
         
print("=== MAIN END ===")
print("=== ALL ROUTES REGISTERED ===")
@app.get("/ping")
def ping():
    return {"status": "ok"}


