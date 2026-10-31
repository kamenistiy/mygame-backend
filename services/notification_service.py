import json
from core.db import get_db

def add_notification(user_id: str, notif_type: str, title: str = None, message: str = None, key: str = None, params: dict = None):
    """
    Если передан key, мы сохраняем в БД JSON с ключом и параметрами.
    Фронтенд сам переведет это на нужный язык.
    """
    final_title = title
    final_msg = message

    if key:
        final_title = key  # В title храним просто ключ (для поиска)
        final_msg = json.dumps({"key": key, "params": params or {}})

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO notifications (user_id, type, title, message, expires_at, is_read)
                VALUES (%s, %s, %s, %s, NOW() + INTERVAL '1 year', false)
            """, (user_id, notif_type, final_title, final_msg))
            conn.commit()