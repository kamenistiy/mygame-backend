from fastapi import APIRouter
from core.db import get_db

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
    conn = get_db()
    cur = conn.cursor()
    # Получить текущий прогресс
    cur.execute("SELECT current_progress, max_progress FROM achievements a JOIN user_achievements ua ON a.id = ua.achievement_id WHERE ua.user_id = %s AND ua.achievement_id = %s", (user_id, achievement_id))
    row = cur.fetchone()
    is_unlocked = False
    if not row:
    # Создать запись
        cur.execute(
        "INSERT INTO user_achievements (user_id, achievement_id, current_progress) VALUES (%s, %s, %s)",
        (user_id, achievement_id, 0)
    )

        current = 0

        cur.execute(
        "SELECT max_progress FROM achievements WHERE id = %s",
        (achievement_id,)
    )
        max_prog_row = cur.fetchone()
        max_prog = max_prog_row['max_progress']

    else:
        is_unlocked = row['is_unlocked']
        current = row['current_progress']
        max_prog = row['max_progress']
    new_progress = min(current + increment, max_prog)
    cur.execute("UPDATE user_achievements SET current_progress = %s WHERE user_id = %s AND achievement_id = %s", (new_progress, user_id, achievement_id))
    if new_progress >= max_prog and not is_unlocked:
        cur.execute("UPDATE user_achievements SET is_unlocked = true, unlocked_at = NOW() WHERE user_id = %s AND achievement_id = %s", (user_id, achievement_id))
        # Можно добавить выдачу награды (опыт, монеты)
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}