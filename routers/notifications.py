from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List

from core.db import get_db

router = APIRouter()


@router.get("/notifications")
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
        
@router.get("/notifications/unread/count")
def get_unread_count(user_id: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM notifications WHERE user_id = %s AND is_read = false AND expires_at > NOW()", (user_id,))
            count = cur.fetchone()['count']
            return {"unread_count": count}
        
@router.post("/notifications/mark_read")
def mark_notifications_read(user_id: str, notification_ids: List[str] = None):
    print(f"🔔 mark_notifications_read вызван для user_id={user_id}, ids={notification_ids}")
    with get_db() as conn:
        with conn.cursor() as cur:
            if notification_ids:
                cur.execute("UPDATE notifications SET is_read = true WHERE user_id = %s AND id = ANY(%s)", (user_id, notification_ids))
            else:
                cur.execute("UPDATE notifications SET is_read = true WHERE user_id = %s AND expires_at > NOW()", (user_id,))
            conn.commit()
            print(f"✅ Помечено прочитанными для {user_id}")
            return {"success": True}

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