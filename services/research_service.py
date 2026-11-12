import random
import re
from pathlib import Path
from datetime import datetime, timezone, time as dt_time, timedelta

from core.db import get_db
from services.player_service import required_exp
from services.notification_service import add_notification
from services.achievement_service import update_achievement_progress_logic


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

MAX_PENDING = 10
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
    """is_active = TRUE, счётчик автору +1, ачивки, нотификация."""
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

    # ===== Начисление прогресса ачивок за тексты =====
    # Вызываем ПОСЛЕ commit, т.к. функция открывает своё соединение
    update_achievement_progress_logic(str(author_id), 'notesadd_1', 1)
    update_achievement_progress_logic(str(author_id), 'notesadd_10', 1)
    update_achievement_progress_logic(str(author_id), 'notesadd_50', 1)
    update_achievement_progress_logic(str(author_id), 'notesadd_100', 1)
    update_achievement_progress_logic(str(author_id), 'notesadd_200', 1)
    update_achievement_progress_logic(str(author_id), 'notesadd_500', 1)

    # Нотификация — вне транзакции
    add_notification(
        str(author_id),
        'system',
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
        'system',
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
    base = XP_WEIGHTS.get(event_type, 0.0006)
    req = required_exp(level)
    xp = req * base * random.uniform(0.7, 1.3)
    
    # Микробуст для новичков (до 10 уровня даем минимум 2 опыта за шаг)
    if level <= 10:
        xp = max(2.0, xp * 5)
        
    return max(1, round(xp))

def roll_loot_item(cur, region_id: str) -> dict | None:
    """Выбирает случайный предмет из лут-зоны региона с учетом весов."""
    if not region_id:
        return None

    # 1. Получаем ID лут-зоны региона
    cur.execute("SELECT loot_zone_id FROM regions WHERE id = %s", (region_id,))
    row = cur.fetchone()
    if not row or not row['loot_zone_id']:
        return None
    
    zone_id = row['loot_zone_id']

    # 2. Загружаем пул предметов для этой зоны (добавили i.rarity)
    cur.execute("""
        SELECT lzi.item_id, lzi.weight, lzi.min_qty, lzi.max_qty,
               i.name, i.name_en, i.icon, i.rarity
        FROM loot_zone_items lzi
        JOIN items i ON i.id = lzi.item_id
        WHERE lzi.zone_id = %s
    """, (zone_id,))
    items = cur.fetchall()
    if not items:
        return None

    # 3. Роллим по весам
    weights = [max(0, item['weight']) for item in items]
    if sum(weights) <= 0:
        return None
        
    chosen = random.choices(items, weights=weights, k=1)[0]
    qty = random.randint(chosen['min_qty'], chosen['max_qty'])

    # 4. Возвращаем результат, включая редкость для фронтенда
    return {
        "id": chosen['item_id'],
        "name": chosen['name'],
        "name_en": chosen['name_en'],
        "icon": chosen['icon'],
        "quantity": qty,
        "rarity": chosen['rarity']
    }

def roll_gathering_node(cur, region_id: str) -> dict | None:
    if not region_id:
        return None

    # 1. Роллим редкость (75% обычный, 20% редкий, 5% забавный)
    roll = random.uniform(0, 100)
    if roll <= 75:
        target_rarity = 'Простой'
    elif roll <= 95:
        target_rarity = 'Редкий'
    else:
        target_rarity = 'Забавный'

    # 2. Выбираем случайную ноду этой редкости в регионе (+ данные предмета)
    cur.execute("""
        SELECT n.id, n.name, n.name_en, n.icon, n.rarity, n.item_id, n.min_hold_time, n.max_hold_time,
               i.name AS item_name, i.name_en AS item_name_en, i.icon AS item_icon
        FROM nodes n
        JOIN items i ON n.item_id = i.id
        WHERE n.region_id = %s AND n.rarity = %s
        ORDER BY RANDOM()
        LIMIT 1
    """, (region_id, target_rarity))
    
    node = cur.fetchone()
    if not node:
        return None

    # 3. Генерируем время удержания
    hold_time = random.randint(node['min_hold_time'], node['max_hold_time'])

    return {
        "node_id": node['id'],
        "name": node['name'],
        "name_en": node['name_en'],
        "icon": node['icon'],
        "rarity": node['rarity'],
        "item_id": node['item_id'],
        "item_name": node['item_name'],
        "item_name_en": node['item_name_en'],
        "item_icon": node['item_icon'],
        "hold_time": hold_time
    }

# ================================================================
# ЗЛОКЛЮЧЕНИЯ (MISADVENTURES)
# Тексты хранятся в БД: misadventure_texts, misadventure_subtype_weights.
# region_id = NULL → дефолтный пул, применяется ко всем регионам.
# Merge-логика: тексты региона ДОБАВЛЯЮТСЯ к дефолтным.
# ================================================================

TRAP_AVOID_CHANCE = 15.0  # %

_TRAP_DAMAGE_WEIGHTS = {
    "hp": 25,
    "mp": 20,
    "energy": 20,
    "fatigue": 20,
}


def _load_misadventure_texts(cur, region_id: str | None) -> dict:
    """Возвращает {subtype: [row, ...]} = дефолт + региональные (merge)."""
    pool: dict[str, list] = {}

    # 1. Дефолтные (region_id IS NULL)
    cur.execute("""
        SELECT subtype, text_ru, text_en, weight
        FROM misadventure_texts
        WHERE region_id IS NULL AND is_active = TRUE
    """)
    for r in cur.fetchall():
        pool.setdefault(r['subtype'], []).append(r)

    # 2. Региональные (добавляются к дефолтным)
    if region_id:
        cur.execute("""
            SELECT subtype, text_ru, text_en, weight
            FROM misadventure_texts
            WHERE region_id = %s AND is_active = TRUE
        """, (region_id,))
        for r in cur.fetchall():
            pool.setdefault(r['subtype'], []).append(r)

    return pool


def _load_subtype_weights(cur, region_id: str | None) -> dict:
    """Веса подтипов. Региональные перекрывают дефолтные."""
    weights: dict[str, int] = {}

    cur.execute("""
        SELECT subtype, weight FROM misadventure_subtype_weights
        WHERE region_id IS NULL
    """)
    for r in cur.fetchall():
        weights[r['subtype']] = int(r['weight'])

    if region_id:
        cur.execute("""
            SELECT subtype, weight FROM misadventure_subtype_weights
            WHERE region_id = %s
        """, (region_id,))
        for r in cur.fetchall():
            weights[r['subtype']] = int(r['weight'])

    return weights


def _pick_misadventure_text(cur, region_id: str | None) -> dict:
    """Случайный текст злоключения с учётом весов."""
    pool = _load_misadventure_texts(cur, region_id)
    if not pool:
        # Совсем пусто (теоретически не должно быть, но пусть будет)
        return {
            "subtype": "weather",
            "ru": "Что-то обрушивается на тебя из ниоткуда...",
            "en": "Something crashes down on you out of nowhere...",
        }

    weights_map = _load_subtype_weights(cur, region_id)
    subtypes = list(pool.keys())
    subtype_weights = [max(0, weights_map.get(s, 1)) for s in subtypes]
    if sum(subtype_weights) <= 0:
        subtype_weights = [1] * len(subtypes)

    subtype = random.choices(subtypes, weights=subtype_weights, k=1)[0]
    entries = pool[subtype]
    entry_weights = [max(0, int(e['weight'])) for e in entries]
    if sum(entry_weights) <= 0:
        entry_weights = [1] * len(entries)

    chosen = random.choices(entries, weights=entry_weights, k=1)[0]
    return {"subtype": subtype, "ru": chosen['text_ru'], "en": chosen['text_en']}


def sync_fatigue_state(cur, user_id: str) -> str | None:
    cur.execute("SELECT fatigue FROM player_stats WHERE user_id = %s", (user_id,))
    row = cur.fetchone()
    if not row:
        return None
    
    fatigue_val = int(row['fatigue'] or 0) 

    if fatigue_val >= 1000:
        target = 'fatigue_1000'
    elif fatigue_val >= 500:
        target = 'fatigue_500'
    elif fatigue_val >= 300:
        target = 'fatigue_300'
    else:
        target = 'energized'

    cur.execute("""
        DELETE FROM player_states
        WHERE user_id = %s
          AND state_key IN ('energized', 'fatigue_300', 'fatigue_500', 'fatigue_1000')
          AND state_key != %s
    """, (user_id, target))

    cur.execute("""
        SELECT 1 FROM player_states WHERE user_id = %s AND state_key = %s
    """, (user_id, target))
    if not cur.fetchone():
        now = datetime.now(timezone.utc)
        tomorrow = now + timedelta(days=1)
        expires_at = datetime.combine(tomorrow, dt_time.min, tzinfo=timezone.utc)
        cur.execute("""
            INSERT INTO player_states (user_id, state_key, expires_at)
            VALUES (%s, %s, %s)
        """, (user_id, target, expires_at))

    return target


def handle_trap_event(cur, user_id: str, region_id: str | None) -> dict:
    """Обрабатывает злоключение. НЕ коммитит.
    Предполагается, что базовый шаг исследования (steps_today = prev + 1)
    уже записан в БД до вызова этой функции."""
    text = _pick_misadventure_text(cur, region_id)

    # 1. Уклонение
    if random.uniform(0, 100) < TRAP_AVOID_CHANCE:
        return {
            "type": "trap",
            "subtype": text["subtype"],
            "avoided": True,
            "text_ru": text["ru"],
            "text_en": text["en"],
        }

    # 2. Ролл типа урона
    dmg_types = list(_TRAP_DAMAGE_WEIGHTS.keys())
    dmg_weights = [_TRAP_DAMAGE_WEIGHTS[t] for t in dmg_types]
    damage_type = random.choices(dmg_types, weights=dmg_weights, k=1)[0]

    # 3. Текущие статы
    cur.execute("""
        SELECT current_hp, max_hp, current_mana, max_mana,
               current_energy, max_energy, fatigue
        FROM player_stats WHERE user_id = %s
    """, (user_id,))
    stats = cur.fetchone()

    result = {
        "type": "trap",
        "subtype": text["subtype"],
        "avoided": False,
        "damage_type": damage_type,
        "text_ru": text["ru"],
        "text_en": text["en"],
    }

    if not stats:
        result["avoided"] = True
        return result

    fatigue_val = int(stats["fatigue"] or 0)

    # ---- HP / MP ----
    if damage_type in ("hp", "mp"):
        if damage_type == "hp":
            current = int(stats["current_hp"] or 0)
            maximum = int(stats["max_hp"] or 1)
            field = "current_hp"
        else:
            current = int(stats["current_mana"] or 0)
            maximum = int(stats["max_mana"] or 1)
            field = "current_mana"

        damage = random.randint(max(1, int(maximum * 0.1)),
                                max(1, int(maximum * 0.5)))

        if current - damage < 1:
            fatigue_gain = random.randint(1, 5)
            new_fatigue = fatigue_val + fatigue_gain
            cur.execute(
                "UPDATE player_stats SET fatigue = %s WHERE user_id = %s", 
                (new_fatigue, user_id),
            )
            sync_fatigue_state(cur, user_id)
            result.update({
                "damage_type": "fatigue",
                "fatigue_gained": fatigue_gain,
                "redirected_from": damage_type,
                "fatigue": new_fatigue,
            })
        else:
            new_val = current - damage
            cur.execute(
                f"UPDATE player_stats SET {field} = %s WHERE user_id = %s",
                (new_val, user_id),
            )
            result["damage"] = damage
            result["steps_today"] = steps_today
            result[field] = new_val

    # ---- Energy ----
    elif damage_type == "energy":
        current = int(stats["current_energy"] or 0)
        loss = random.randint(1, 3)
        new_val = max(0, current - loss)
        actual_loss = current - new_val
        cur.execute(
            "UPDATE player_stats SET current_energy = %s WHERE user_id = %s",
            (new_val, user_id),
        )
        result["damage"] = actual_loss
        result["current_energy"] = new_val
        result["steps_today"] = steps_today

    # ---- Fatigue (чистая) ----
    else:
        fatigue_gain = random.randint(1, 5)
        new_fatigue = fatigue_val + fatigue_gain
        cur.execute(
            "UPDATE player_stats SET fatigue = %s WHERE user_id = %s", # <--- UPDATE
            (new_fatigue, user_id),
        )
        sync_fatigue_state(cur, user_id)
        result["fatigue_gained"] = fatigue_gain
        result["fatigue"] = new_fatigue

    return result