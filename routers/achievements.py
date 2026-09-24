from fastapi import APIRouter
from core.db import get_db
from pydantic import BaseModel

from services.achievement_service import update_achievement_progress_logic

router = APIRouter()

class GrantUniqueRequest(BaseModel):
    achievement_id: str
    user_id: str
    note: str | None = None


@router.get("/achievements/all")
def get_all_achievements():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, description, icon, is_unique, max_progress, exp_reward, coins_reward FROM achievements ORDER BY id")
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

@router.get("/achievements/unique/owners")
def get_unique_owners():
    """Список уникальных достижений с их владельцами (для рейтинга)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ua.achievement_id,
                    a.name,
                    a.icon,
                    a.description,
                    ua.user_id,
                    p.username,
                    ua.granted_at
                FROM unique_achievements_owner ua
                JOIN achievements a ON a.id = ua.achievement_id
                JOIN players p ON p.id = ua.user_id
            """)
            rows = cur.fetchall() or []
            return {"owners": rows}


@router.get("/achievements/unique/by-user/{user_id}")
def get_unique_by_user(user_id: str):
    """Уникальные достижения конкретного игрока (для статистики)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.id, a.name, a.icon, a.description, ua.granted_at
                FROM unique_achievements_owner ua
                JOIN achievements a ON a.id = ua.achievement_id
                WHERE ua.user_id = %s
                ORDER BY ua.granted_at DESC
            """, (user_id,))
            return {"achievements": cur.fetchall() or []}


@router.post("/admin/achievements/unique/grant")
def grant_unique_achievement(req: GrantUniqueRequest):
    """Выдать уникальное достижение игроку. Только если оно уникально и ещё не выдано."""
    with get_db() as conn:
        with conn.cursor() as cur:
            # 1. Проверяем, что достижение существует и помечено уникальным
            cur.execute("SELECT is_unique FROM achievements WHERE id = %s", (req.achievement_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Достижение не найдено")
            if not row['is_unique']:
                raise HTTPException(400, "Это достижение не является уникальным")

            # 2. Проверяем, что не выдано
            cur.execute("SELECT user_id FROM unique_achievements_owner WHERE achievement_id = %s",
                        (req.achievement_id,))
            if cur.fetchone():
                raise HTTPException(409, "Это уникальное достижение уже кому-то выдано")

            # 3. Выдаём
            cur.execute("""
                INSERT INTO unique_achievements_owner (achievement_id, user_id, note)
                VALUES (%s, %s, %s)
            """, (req.achievement_id, req.user_id, req.note))
            conn.commit()
            return {"success": True}


@router.post("/admin/achievements/unique/revoke")
def revoke_unique_achievement(req: dict):
    """Отозвать уникальное достижение (если выдали не тому)."""
    achievement_id = req.get('achievement_id')
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM unique_achievements_owner WHERE achievement_id = %s",
                        (achievement_id,))
            conn.commit()
            return {"success": True}