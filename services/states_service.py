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
    """Применить состояние к игроку (если уже активно – продлить время)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            # Проверяем, есть ли уже активное состояние
            cur.execute(
                "SELECT expires_at FROM player_states WHERE user_id = %s AND state_key = %s",
                (user_id, state_key)
            )
            existing = cur.fetchone()
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
            
            if existing:
                # Продлеваем существующее
                cur.execute(
                    "UPDATE player_states SET expires_at = %s WHERE user_id = %s AND state_key = %s",
                    (expires_at, user_id, state_key)
                )
            else:
                # Создаём новое
                cur.execute(
                    "INSERT INTO player_states (user_id, state_key, expires_at, parameters) VALUES (%s, %s, %s, %s)",
                    (user_id, state_key, expires_at, '{}')
                )
                # Применяем эффект (только при первом применении, чтобы не накапливать)
                _apply_effect(user_id, state_key)
            conn.commit()

def _apply_effect(user_id: str, state_key: str):
    """Непосредственно изменяет характеристики игрока."""
    info = STATE_INFO[state_key]
    if state_key == 'exhaustion':
        # Наносим урон
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE player_stats
                    SET current_hp = GREATEST(current_hp + %s, 0),
                        current_mana = GREATEST(current_mana + %s, 0)
                    WHERE user_id = %s
                """, (info['damage_hp'], info['damage_mana'], user_id))
                conn.commit()
    elif 'modifiers' in info:
        with get_db() as conn:
            with conn.cursor() as cur:
                mods = info['modifiers']
                # Обновляем базовые статы (body, strength и т.д.)
                for stat, delta in mods.items():
                    if stat in ('body', 'strength', 'agility', 'intellect'):
                        cur.execute(f"UPDATE player_stats SET {stat} = {stat} + %s WHERE user_id = %s", (delta, user_id))
                    # Для боевых статов (pat, mat, pdf, mdf) – они пересчитаются через recalc_derived_stats,
                    # но rage изменяет их напрямую, поэтому меняем их и потом пересчитываем.
                    elif stat in ('pat', 'mat', 'pdf', 'mdf'):
                        cur.execute(f"UPDATE player_stats SET {stat} = {stat} + %s WHERE user_id = %s", (delta, user_id))
                conn.commit()
        # Если менялись базовые статы – пересчитываем производные
        if any(s in mods for s in ('body','strength','agility','intellect')):
            recalc_derived_stats(user_id)

def remove_state(user_id: str, state_key: str):
    """Снять состояние (откатить эффект)."""
    info = STATE_INFO[state_key]
    if state_key == 'exhaustion':
        # Истощение не откатывает урон
        pass
    elif 'modifiers' in info:
        with get_db() as conn:
            with conn.cursor() as cur:
                mods = info['modifiers']
                for stat, delta in mods.items():
                    if stat in ('body','strength','agility','intellect'):
                        cur.execute(f"UPDATE player_stats SET {stat} = {stat} - %s WHERE user_id = %s", (delta, user_id))
                    elif stat in ('pat','mat','pdf','mdf'):
                        cur.execute(f"UPDATE player_stats SET {stat} = {stat} - %s WHERE user_id = %s", (delta, user_id))
                conn.commit()
        if any(s in mods for s in ('body','strength','agility','intellect')):
            recalc_derived_stats(user_id)
    # Удаляем запись из таблицы
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM player_states WHERE user_id = %s AND state_key = %s",
                (user_id, state_key)
            )
            conn.commit()

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
    """Вернуть список активных состояний с иконками и типами (для фронта)."""
    check_expired_states(user_id)  # сначала чистим истекшие
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT state_key, expires_at FROM player_states WHERE user_id = %s ORDER BY state_key",
                (user_id,)
            )
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
            # Сортировка: сначала buff, потом debuff
            states.sort(key=lambda s: (0 if s['type'] == 'buff' else 1))
            return states