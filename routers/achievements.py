from fastapi import APIRouter
from core.db import get_db

from services.achievement_service import update_achievement_progress_logic

router = APIRouter()


@router.get("/achievements/all")
def get_all_achievements():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, description, icon, max_progress, exp_reward, coins_reward FROM achievements ORDER BY id")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"achievements": rows}

@router.get("/achievements/progress")
def get_user_achievement_progress(user_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT achievement_id, current_progress, is_unlocked FROM user_achievements WHERE user_id = %s", (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"progress": rows}

@router.get("/achievements/pinned")
def get_pinned_achievements(user_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT position, achievement_id FROM user_pinned_achievements WHERE user_id = %s ORDER BY position", (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {"pinned": rows}

@router.post("/achievements/pin")
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

@router.post("/achievements/update_progress")
def update_achievement_progress(req: dict):
    user_id = req.get('user_id')
    achievement_id = req.get('achievement_id')
    increment = req.get('increment', 1)
    update_achievement_progress_logic(user_id, achievement_id, increment)
    return {"success": True}