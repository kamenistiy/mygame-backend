import random
import re
from pathlib import Path
from core.db import get_db
from services.player_service import required_exp
from services.notification_service import add_notification


# ================================================================
# БАДВОРДЫ
# ================================================================

BADWORDS_PATH = Path(__file__).resolve().parent.parent / "data" / "badwords.csv"
_badwords_re: re.Pattern | None = None


def _word_to_pattern(word: str) -> str:
    """Превращает слово из CSV в regex-паттерн.
    '*' → \\w* (ноль и более букв), '+' → \\w+ (одна и более)."""
    parts = []
    for ch in word:
        if ch == "*":
            parts.append(r"\w*")
        elif ch == "+":
            parts.append(r"\w+")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


def _build_badwords_re() -> re.Pattern:
    if not BADWORDS_PATH.exists():
        print(f"⚠️  [badwords] файл не найден: {BADWORDS_PATH}")
        return re.compile(r"(?!)")  # никогда не совпадает

    words: list[str] = []
    with BADWORDS_PATH.open("r", encoding="utf-8-sig") as f:
        for line in f:
            w = line.strip()
            if w:
                words.append(w)

    if not words:
        return re.compile(r"(?!)")

    # Длинные фразы первыми — чтобы альтернативы матчились точнее
    words.sort(key=len, reverse=True)

    alternatives = "|".join(_word_to_pattern(w) for w in words)
    pattern = rf"(?<!\w)(?:{alternatives})(?!\w)"
    try:
        compiled = re.compile(pattern, re.IGNORECASE | re.UNICODE)
    except re.error as e:
        print(f"⚠️  [badwords] ошибка компиляции regex: {e}")
        return re.compile(r"(?!)")
    print(f"✅ [badwords] загружено {len(words)} слов")
    return compiled


def _get_badwords_re() -> re.Pattern:
    global _badwords_re
    if _badwords_re is None:
        _badwords_re = _build_badwords_re()
    return _badwords_re


def contains_badwords(text: str) -> bool:
    if not text:
        return False
    return bool(_get_badwords_re().search(text))


# ================================================================
# ИССЛЕДОВАНИЕ — ролл и выборка текста
# ================================================================

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
    """Возвращает случайный активный текст + автора + количество лайков + liked_by_me.
    Тема (theme) в игровую выдачу НЕ попадает."""
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


# ================================================================
# ПРЕДЛОЖКА ТЕКСТОВ — сабмит, модерация, список
# ================================================================

MAX_PENDING = 5
SUBMIT_COOLDOWN_SEC = 60
MAX_THEME_LEN = 60
MAX_CONTENT_LEN = 500


def submit_text(user_id: str, theme: str, content: str) -> dict:
    """Валидирует и сохраняет текст как pending (is_active = FALSE).
    Возвращает {id, theme, created_at}. Бросает ValueError с понятным текстом."""
    theme = (theme or "").strip()
    content = (content or "").strip()

    if not theme:
        raise ValueError("Тема не может быть пустой")
    if len(theme) > MAX_THEME_LEN:
        raise ValueError(f"Тема не может быть длиннее {MAX_THEME_LEN} символов")
    if not content:
        raise ValueError("Текст не может быть пустым")
    if len(content) > MAX_CONTENT_LEN:
        raise ValueError(f"Текст не может быть длиннее {MAX_CONTENT_LEN} символов")

    if contains_badwords(theme):
        raise ValueError("В теме обнаружены недопустимые слова")
    if contains_badwords(content):
        raise ValueError("В тексте обнаружены недопустимые слова")

    with get_db() as conn:
        with conn.cursor() as cur:
            # 1. Лимит pending
            cur.execute("""
                SELECT COUNT(*) AS cnt FROM research_texts
                WHERE author_id = %s AND is_active = FALSE
            """, (user_id,))
            pending = int(cur.fetchone()['cnt'])
            if pending >= MAX_PENDING:
                raise ValueError(
                    f"У вас уже {MAX_PENDING} текстов на модерации. Дождитесь решения."
                )

            # 2. Кулдаун
            cur.execute("""
                SELECT last_text_submit_at,
                       EXTRACT(EPOCH FROM (NOW() - last_text_submit_at)) AS seconds_since
                FROM players WHERE id = %s
            """, (user_id,))
            row = cur.fetchone()
            if row and row['last_text_submit_at'] is not None:
                sec = float(row['seconds_since'] or 0)
                if sec < SUBMIT_COOLDOWN_SEC:
                    wait = int(SUBMIT_COOLDOWN_SEC - sec) + 1
                    raise ValueError(f"Подождите {wait} сек. перед следующей отправкой")

            # 3. INSERT
            cur.execute("""
                INSERT INTO research_texts (content, theme, author_id, is_active, created_at)
                VALUES (%s, %s, %s, FALSE, NOW())
                RETURNING id, theme, created_at
            """, (content, theme, user_id))
            new_row = cur.fetchone()

            # 4. Отметка времени сабмита
            cur.execute(
                "UPDATE players SET last_text_submit_at = NOW() WHERE id = %s",
                (user_id,)
            )
            conn.commit()

            return {
                "id": str(new_row['id']),
                "theme": new_row['theme'],
                "created_at": new_row['created_at'].isoformat() if new_row['created_at'] else None,
            }


def list_pending(limit: int = 200) -> list[dict]:
    """Список pending-текстов для админки (старые сверху)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.id, t.content, t.theme, t.created_at,
                       t.author_id, p.username AS author_name
                FROM research_texts t
                LEFT JOIN players p ON p.id = t.author_id
                WHERE t.is_active = FALSE
                ORDER BY t.created_at ASC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            return [{
                "id": str(r['id']),
                "content": r['content'],
                "theme": r['theme'] or '',
                "created_at": r['created_at'].isoformat() if r['created_at'] else None,
                "author_id": str(r['author_id']) if r['author_id'] else None,
                "author_name": r['author_name'] or 'Неизвестный',
            } for r in rows]


def approve_text(text_id: str, admin_id: str) -> dict:
    """is_active = TRUE, счётчик автору +1, нотификация."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, author_id, theme FROM research_texts
                WHERE id = %s AND is_active = FALSE
            """, (text_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Текст не найден или уже обработан")

            author_id = row['author_id']
            theme = row['theme'] or '(без темы)'

            cur.execute(
                "UPDATE research_texts SET is_active = TRUE WHERE id = %s",
                (text_id,)
            )
            cur.execute("""
                UPDATE players SET approved_texts_count = approved_texts_count + 1
                WHERE id = %s
            """, (author_id,))
            conn.commit()

    # Нотификация — вне транзакции
    add_notification(
        str(author_id),
        'research_text_approved',
        '📜 Заметка одобрена!',
        f'Ваша заметка на тему «{theme}» одобрена и добавлена в игру. Благодарим за вклад!'
    )

    return {"id": str(text_id), "author_id": str(author_id), "theme": theme}


def reject_text(text_id: str, admin_id: str, comment: str) -> dict:
    """DELETE (нигде не сохраняем), нотификация с причиной."""
    comment = (comment or "").strip()
    if not comment:
        raise ValueError("Комментарий обязателен")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, author_id, theme FROM research_texts
                WHERE id = %s AND is_active = FALSE
            """, (text_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Текст не найден или уже обработан")

            author_id = row['author_id']
            theme = row['theme'] or '(без темы)'

            cur.execute("DELETE FROM research_texts WHERE id = %s", (text_id,))
            conn.commit()

    add_notification(
        str(author_id),
        'research_text_rejected',
        '📜 Заметка отклонена',
        f'Ваша заметка на тему «{theme}» не прошла проверку. Причина: {comment}'
    )

    return {"id": str(text_id), "author_id": str(author_id)}

# Веса опыта (в процентах от required_exp)
XP_WEIGHTS = {
    'text': 0.0006,
    'resource': 0.0008,
    'item': 0.0010,
    'trap': 0.0012,
    'secret': 0.0025,
    'combat': 0.0004, # только за встречу
    'victory': 0.0016 # дополнительно за победу
}

def calc_research_xp(level: int, event_type: str) -> int:
    """Считает опыт за событие с разбросом ±30%"""
    base = XP_WEIGHTS.get(event_type, 0.0006)
    req = required_exp(level)
    xp = req * base * random.uniform(0.7, 1.3)
    return max(1, round(xp))