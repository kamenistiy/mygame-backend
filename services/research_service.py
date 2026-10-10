import random
from core.db import get_db


def roll_event(encounters: dict) -> str:
    """Возвращает ключ события по весам из JSONB."""
    if not encounters:
        return 'text'
    keys = list(encounters.keys())
    weights = [max(0, encounters[k]) for k in keys]
    total = sum(weights)
    if total <= 0:
        return 'text'
    return random.choices(keys, weights=weights, k=1)[0]


def pick_random_text(cur, user_id: str) -> dict | None:
    """Возвращает случайный активный текст + автора + количество лайков + liked_by_me."""
    cur.execute("""
        SELECT t.id, t.content,
               t.author_id,
               p.username AS author_name,
               COALESCE((SELECT COUNT(*) FROM research_text_likes l WHERE l.text_id = t.id), 0) AS likes,
               EXISTS(SELECT 1 FROM research_text_likes l WHERE l.text_id = t.id AND l.user_id = %s) AS user_liked
        FROM research_texts t
        LEFT JOIN players p ON p.id = t.author_id
        WHERE t.is_active = TRUE
        ORDER BY RANDOM()
        LIMIT 1
    """, (user_id,))
    row = cur.fetchone()
    if not row:
        return None
    return {
        "text_id": str(row['id']),
        "content": row['content'],
        "author_id": str(row['author_id']) if row['author_id'] else None,
        "author_name": row['author_name'] or 'Неизвестный',
        "likes": int(row['likes']),
        "user_liked": bool(row['user_liked'])
    }