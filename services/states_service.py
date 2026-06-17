import json
from datetime import datetime, timedelta, timezone
from core.db import get_db
from services.player_service import recalc_derived_stats

# Словарь с данными состояний (иконки, типы)
STATE_INFO = {
    'exhaustion': {
    'name': 'Истощение',
    'type': 'debuff',
    'icon_class': 'state-exhaustion',
    'duration': 10,
    'modifiers': {
        'hp_delta': -50,
        'mana_delta': -50
    }
    },
    'weakness': {
        'name': 'Слабость',
        'type': 'debuff',
        'icon_class': 'state-weakness',
        'modifiers': {'body': -1, 'strength': -1, 'agility': -1, 'intellect': -1},
    },
    'inspiration': {
        'name': 'Воодушевление',
        'type': 'buff',
        'icon_class': 'state-inspiration',
        'modifiers': {'body': 1, 'strength': 1, 'agility': 1, 'intellect': 1},
    },
    'rage': {
        'name': 'Ярость',
        'type': 'buff',
        'icon_class': 'state-rage',
        'modifiers': {'pat': 10, 'mat': 10, 'pdf': -5, 'mdf': -5},
    }
}

def apply_state(user_id: str, state_key: str, duration_seconds: int = 10):
    with get_db() as conn:
        with conn.cursor() as cur:

            expires_at = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)

            info = STATE_INFO.get(state_key, {})
            modifiers = info.get('modifiers', {})

            parameters_json = json.dumps(modifiers)

            cur.execute("""
                DELETE FROM player_states
                WHERE user_id = %s AND state_key = %s
            """, (user_id, state_key))

            cur.execute("""
                INSERT INTO player_states
                (user_id, state_key, expires_at, parameters)
                VALUES (%s, %s, %s, %s)
            """, (
                user_id,
                state_key,
                expires_at,
                parameters_json
            ))

            apply_effect(user_id, state_key)

            conn.commit()

def remove_state(user_id: str, state_key: str):
    # Никаких изменений базовых статов!
    # Просто удаляем запись состояния
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM player_states WHERE user_id = %s AND state_key = %s",
                (user_id, state_key)
            )
            conn.commit()
    # Если это exhaustion – не нужно ничего откатывать (урон уже нанесён)

def check_expired_states(user_id: str):
    with get_db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT state_key
                FROM player_states
                WHERE user_id = %s AND expires_at < NOW()
            """, (user_id,))

            expired = cur.fetchall()

            for row in expired:
                state_key = row['state_key']

                # 🔴 ВОТ ОТКАТ
                on_expire_state(user_id, state_key)

                # удалить состояние
                cur.execute("""
                    DELETE FROM player_states
                    WHERE user_id = %s AND state_key = %s
                """, (user_id, state_key))

            conn.commit()

def get_active_states(user_id: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT state_key, parameters, expires_at
                FROM player_states
                WHERE user_id = %s AND expires_at > NOW()
            """, (user_id,))

            rows = cur.fetchall()

            states = []

            for row in rows:
                state_key = row.get("state_key")

                info = STATE_INFO.get(state_key)
                if not info:
                    continue

                params = row.get("parameters") or {}

                expires_at = row.get("expires_at")
                if expires_at:
                    expires_at = expires_at.isoformat()

                states.append({
                    "id": state_key,
                    "name": info.get("name", state_key),
                    "type": info.get("type", "debuff"),
                    "icon_class": info.get("icon_class", ""),
                    "parameters": params,
                    "expires_at": expires_at
                })

            return states

def clean_expired_states():
    """Удаляет все истекшие состояния из БД (можно вызывать по расписанию)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM player_states WHERE expires_at < NOW()")
            conn.commit()
            return cur.rowcount

def apply_effect(user_id: str, state_key: str):
    with get_db() as conn:
        with conn.cursor() as cur:

            if state_key == "exhaustion":
                cur.execute("""
                    UPDATE player_stats
                    SET current_hp = GREATEST(current_hp - 50, 0),
                        current_mana = GREATEST(current_mana - 50, 0)
                    WHERE user_id = %s
                """, (user_id,))

            conn.commit()

def on_expire_state(user_id: str, state_key: str):
    with get_db() as conn:
        with conn.cursor() as cur:

            if state_key == "exhaustion":
                cur.execute("""
                    UPDATE player_stats
                    SET current_hp = LEAST(current_hp + 50, max_hp),
                        current_mana = LEAST(current_mana + 50, max_mana)
                    WHERE user_id = %s
                """, (user_id,))

            conn.commit()