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

def is_valid_uuid(uuid_str: str) -> bool:
    try:
        UUID(uuid_str)
        return True
    except ValueError:
        return False
    
print("=== STARTING APP ===")
print("STEP 1")
from core.supabase_client import supabase
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
from core.supabase_client import supabase

@app.get("/")
def root():
    return {"message": "Сервер игры работает!"}

@app.get("/ping")
def ping():
    return {"ping": "pong"}

@app.get("/test")
def test():
    return {"test": "ok"}
         
print("=== MAIN END ===")
print("=== ALL ROUTES REGISTERED ===")
@app.get("/ping")
def ping():
    return {"status": "ok"}


