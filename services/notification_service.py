from core.db import get_db

def add_notification(user_id: str, notif_type: str, title: str, message: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO notifications (user_id, type, title, message, expires_at, is_read)
                VALUES (%s, %s, %s, %s, NOW() + INTERVAL '1 year', false)
            """, (user_id, notif_type, title, message))
            conn.commit()