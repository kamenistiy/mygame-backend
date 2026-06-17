import json
from datetime import datetime, timedelta, timezone
from core.db import get_db
from services.player_service import recalc_derived_stats

# Словарь с данными состояний (иконки, типы)
STATE_INFO = {
    'exhaustion': {
        'name': 'Истощение',
        'type': 'debuff',
        'icon_class': 'state-exhaustion',  # будет использовано в CSS
        'apply_effect': 'damage',          # наносит урон при применении
        'damage_hp': -50,
        'damage_mana': -50,
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
    print("\n==============================")
    print("APPLY_STATE START")
    print("USER:", user_id)
    print("STATE:", state_key)
    print("==============================")

    with get_db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT expires_at
                FROM player_states
                WHERE user_id = %s
                  AND state_key = %s
                """,
                (user_id, state_key)
            )

            existing = cur.fetchone()

            print("EXISTING STATE:", existing)

            expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=duration_seconds
            )

            info = STATE_INFO.get(state_key, {})
            modifiers = info.get('modifiers', {})
            parameters_json = json.dumps(modifiers)

            if existing:
                print("STATE ALREADY EXISTS -> UPDATE TIMER")

                cur.execute(
                    """
                    UPDATE player_states
                    SET expires_at = %s
                    WHERE user_id = %s
                      AND state_key = %s
                    """,
                    (expires_at, user_id, state_key)
                )

                print("UPDATED ROWS:", cur.rowcount)

            else:
                print("NEW STATE -> INSERT")

                cur.execute(
                    """
                    INSERT INTO player_states
                    (
                        user_id,
                        state_key,
                        expires_at,
                        parameters
                    )
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        state_key,
                        expires_at,
                        parameters_json
                    )
                )

                print("STATE INSERTED")
                print("CALLING _apply_effect()")

                _apply_effect(user_id, state_key)

            conn.commit()

            print("APPLY_STATE COMMIT OK")
            print("==============================\n")

def _apply_effect(user_id: str, state_key: str):
    print("\n>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")
    print("_apply_effect CALLED")
    print("USER:", user_id)
    print("STATE:", state_key)
    print("<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<")

    info = STATE_INFO[state_key]

    if state_key == 'exhaustion':

        print("EXHAUSTION EFFECT START")
        print("HP DELTA:", info['damage_hp'])
        print("MANA DELTA:", info['damage_mana'])

        with get_db() as conn:
            with conn.cursor() as cur:

                cur.execute("""
                    SELECT current_hp,
                           current_mana
                    FROM player_stats
                    WHERE user_id = %s
                """, (user_id,))

                before = cur.fetchone()

                print("BEFORE UPDATE:", before)

                cur.execute("""
                    UPDATE player_stats
                    SET current_hp = GREATEST(current_hp + %s, 0),
                        current_mana = GREATEST(current_mana + %s, 0)
                    WHERE user_id = %s
                """,
                (
                    info['damage_hp'],
                    info['damage_mana'],
                    user_id
                ))

                print("ROWS UPDATED:", cur.rowcount)

                conn.commit()

                cur.execute("""
                    SELECT current_hp,
                           current_mana
                    FROM player_stats
                    WHERE user_id = %s
                """, (user_id,))

                after = cur.fetchone()

                print("AFTER UPDATE:", after)
                print("EXHAUSTION EFFECT END")

    else:
        print("NO ONE-TIME EFFECT FOR:", state_key)
    # Для всех остальных состояний – ничего не делаем, модификаторы уже в parameters

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
    """Проверить истекшие состояния и снять их."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state_key FROM player_states WHERE user_id = %s AND expires_at < NOW()",
                (user_id,)
            )
            expired = cur.fetchall()
            for row in expired:
                remove_state(user_id, row['state_key'])

def get_active_states(user_id: str):
    """Вернуть список активных состояний (expires_at > NOW()) без удаления записей."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT state_key, expires_at
                FROM player_states
                WHERE user_id = %s AND expires_at > NOW()
                ORDER BY state_key
            """, (user_id,))
            rows = cur.fetchall()
            states = []
            for row in rows:
                state_key = row['state_key']
                info = STATE_INFO[state_key]
                states.append({
                    'id': state_key,
                    'name': info['name'],
                    'type': info['type'],
                    'icon_class': info['icon_class'],
                    'expires_at': row['expires_at'].isoformat()
                })
            states.sort(key=lambda s: (0 if s['type'] == 'buff' else 1))
            return states

def clean_expired_states():
    """Удаляет все истекшие состояния из БД (можно вызывать по расписанию)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM player_states WHERE expires_at < NOW()")
            conn.commit()
            return cur.rowcount