from datetime import datetime, timezone
from core.db import get_db
import json

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ  ==========
def required_exp(level: int) -> int:
    if level == 1:
        return 20
    return int(20 * (1.5 ** (level - 1)))



def regen_energy_if_needed(user_id: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT current_energy, max_energy, last_energy_regen
                FROM player_stats
                WHERE user_id = %s
            """, (user_id,))
            stats = cur.fetchone()

            if not stats:
                return

            now = datetime.now(timezone.utc)
            last = stats['last_energy_regen']

            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)

            diff_seconds = (now - last).total_seconds()
            minutes_passed = diff_seconds // 600

            if minutes_passed <= 0:
                return

            new_energy = min(
                stats['current_energy'] + int(minutes_passed),
                stats['max_energy']
            )

            if new_energy != stats['current_energy']:
                cur.execute("""
                    UPDATE player_stats
                    SET current_energy = %s,
                        last_energy_regen = NOW()
                    WHERE user_id = %s
                """, (new_energy, user_id))
                conn.commit()

def apply_regen(user_id: str):
    """Обновляет current_hp и current_mana игрока на основе времени, прошедшего с last_regen_time."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT current_hp, max_hp, current_mana, max_mana, last_regen_time
                FROM player_stats
                WHERE user_id = %s
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                return

            now = datetime.now(timezone.utc)
            last = row['last_regen_time']
            if not last:
                last = now

            delta_seconds = (now - last).total_seconds()
            # 10% от максимума в минуту -> 0.1 / 60 в секунду
            regen_per_second_hp = row['max_hp'] * 0.1 / 60
            regen_per_second_mana = row['max_mana'] * 0.1 / 60

            new_hp = row['current_hp'] + delta_seconds * regen_per_second_hp
            new_mana = row['current_mana'] + delta_seconds * regen_per_second_mana

            new_hp = min(new_hp, row['max_hp'])
            new_mana = min(new_mana, row['max_mana'])

            new_hp = int(new_hp)
            new_mana = int(new_mana)

            if new_hp == row['current_hp'] and new_mana == row['current_mana']:
                return

            cur.execute("""
                UPDATE player_stats
                SET current_hp = %s, current_mana = %s, last_regen_time = %s
                WHERE user_id = %s
            """, (new_hp, new_mana, now, user_id))
            conn.commit()

def get_state_modifiers(user_id: str) -> dict:
    """Сумма модификаторов от всех АКТИВНЫХ состояний.
    Возвращает {'body': 1, 'pat': 10, 'pdf': -5, ...}
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT parameters
                FROM player_states
                WHERE user_id = %s AND expires_at > NOW()
            """, (user_id,))
            rows = cur.fetchall()
            total = {}
            for row in rows:
                params = row['parameters']
                if isinstance(params, str):
                    params = json.loads(params)
                if not params:
                    continue
                for k, v in params.items():
                    total[k] = total.get(k, 0) + (v or 0)
            return total

def recalc_derived_stats(user_id: str):
    """Пересчёт ВСЕГО с учётом base + equip + mod."""
    with get_db() as conn:
        with conn.cursor() as cur:

            # 1. База
            cur.execute("""
                SELECT p.level,
                       ps.base_body, ps.base_strength, ps.base_agility, ps.base_intellect
                FROM players p
                JOIN player_stats ps ON p.id = ps.user_id
                WHERE p.id = %s
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                return

            level = row['level']
            base_body = row['base_body'] or 0
            base_strength = row['base_strength'] or 0
            base_agility = row['base_agility'] or 0
            base_intellect = row['base_intellect'] or 0

            # 2. Экипировка
            equip = get_equipment_stats(user_id)

            # 3. Состояния
            mods = get_state_modifiers(user_id)
            mod_body = mods.get('body', 0)
            mod_strength = mods.get('strength', 0)
            mod_agility = mods.get('agility', 0)
            mod_intellect = mods.get('intellect', 0)

            # 4. Итоговые АТРИБУТЫ
            body_total      = base_body      + equip['body']      + mod_body
            strength_total  = base_strength  + equip['strength']  + mod_strength
            agility_total   = base_agility   + equip['agility']   + mod_agility
            intellect_total = base_intellect + equip['intellect'] + mod_intellect

            # 5. Производные = формула от итоговых + прямые бонусы экип + прямые моды состояний
            max_hp   = 100 + (level - 1) * 10 + body_total * 10      + equip['max_hp']   + mods.get('max_hp', 0)
            max_mana = 100 + (level - 1) * 10 + intellect_total * 10 + equip['max_mana'] + mods.get('max_mana', 0)
            pat = strength_total * 5  + equip['pat']  + mods.get('pat', 0)
            mat = intellect_total * 5 + equip['mat']  + mods.get('mat', 0)
            sp  = intellect_total * 5 + equip['sp']   + mods.get('sp', 0)
            pdf = body_total * 5      + equip['pdf']  + mods.get('pdf', 0)
            mdf = body_total * 5      + equip['mdf']  + mods.get('mdf', 0)
            awr = body_total * 5      + equip['awr']  + mods.get('awr', 0)
            spd = agility_total * 10 - body_total * 5 + equip['spd'] + mods.get('spd', 0)
            acc = agility_total * 5   + equip['acc']  + mods.get('acc', 0)
            ddg = agility_total * 5   + equip['ddg']  + mods.get('ddg', 0)
            gat = strength_total * 5  + equip['gat']  + mods.get('gat', 0)

            # 6. Сохраняем
            cur.execute("""
                UPDATE player_stats
                SET equip_body = %s, equip_strength = %s, equip_agility = %s, equip_intellect = %s,
                    mod_body = %s, mod_strength = %s, mod_agility = %s, mod_intellect = %s,
                    body_total = %s, strength_total = %s, agility_total = %s, intellect_total = %s,
                    max_hp = %s, max_mana = %s,
                    pat = %s, mat = %s, sp = %s,
                    pdf = %s, mdf = %s, awr = %s,
                    spd = %s, acc = %s, ddg = %s, gat = %s
                WHERE user_id = %s
            """, (
                equip['body'], equip['strength'], equip['agility'], equip['intellect'],
                mod_body, mod_strength, mod_agility, mod_intellect,
                body_total, strength_total, agility_total, intellect_total,
                max_hp, max_mana,
                pat, mat, sp,
                pdf, mdf, awr,
                spd, acc, ddg, gat,
                user_id
            ))

            # 7. Обрезаем текущие HP/Mana
            cur.execute("""
                UPDATE player_stats
                SET current_hp = LEAST(current_hp, max_hp),
                    current_mana = LEAST(current_mana, max_mana)
                WHERE user_id = %s
            """, (user_id,))

            conn.commit()


def add_default_avatars_for_user(user_id: str):
    """Добавляет стандартные аватары пользователю"""
    with get_db() as conn:
        with conn.cursor() as cur:
            avatars = ['default_avatars/E.png', 'default_avatars/F.png', 'default_avatars/G.png',
                       'default_avatars/H.png', 'default_avatars/I.png', 'default_avatars/K.png',
                       'default_avatars/M.png', 'default_avatars/S.png', 'default_avatars/V.png',
                       'default_avatars/X.png']
            for path in avatars:
                cur.execute("""
                    INSERT INTO user_avatars (user_id, storage_path, is_active, username)
                    VALUES (%s, %s, false, (SELECT username FROM players WHERE id = %s))
                    ON CONFLICT (user_id, storage_path) DO NOTHING
                """, (user_id, path, user_id))
            conn.commit()

def add_achievement_for_user(user_id: str, achievement_id: str):
    """Добавляет достижение пользователю"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_achievements (user_id, achievement_id, current_progress, is_unlocked, unlocked_at)
                VALUES (%s, %s, 1, true, NOW())
                ON CONFLICT (user_id, achievement_id) DO NOTHING
            """, (user_id, achievement_id))
            conn.commit()

def add_exp_and_coins(user_id: str, exp_to_add: int = 0, coins_to_add: int = 0):
    with get_db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT level, exp, coins
                FROM players
                WHERE id = %s
            """, (user_id,))

            player = cur.fetchone()

            if not player:
                return False

            current_level = player['level']
            current_exp = player['exp']
            current_coins = player['coins']

            new_level = current_level
            total_exp = current_exp + exp_to_add

            while total_exp >= required_exp(new_level):
                total_exp -= required_exp(new_level)
                new_level += 1

            new_coins = current_coins + coins_to_add

            cur.execute("""
                UPDATE players
                SET
                    exp = %s,
                    level = %s,
                    coins = %s
                WHERE id = %s
            """, (
                total_exp,
                new_level,
                new_coins,
                user_id
            ))
            print(f"LEVEL UPDATE -> level={new_level}, exp={total_exp}, coins={new_coins}"
            )

            # level up rewards
            if new_level > current_level:
                level_diff = new_level - current_level

                cur.execute("""
                    UPDATE player_stats
                    SET
                        max_hp = max_hp + %s,
                        current_hp = current_hp + %s,
                        max_mana = max_mana + %s,
                        current_mana = current_mana + %s,
                        free_stat_points = free_stat_points + %s
                    WHERE user_id = %s
                """, (
                    level_diff * 10,
                    level_diff * 10,
                    level_diff * 10,
                    level_diff * 10,
                    level_diff * 2,
                    user_id
                ))

            conn.commit()

    recalc_derived_stats(user_id)

    if new_level > current_level:
        recalc_derived_stats(user_id)
    return True

def get_equipment_stats(user_id: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT i.strength, i.agility, i.intellect, i.body,
                       i.pdf, i.mdf, i.pat, i.mat, i.ddg, i.acc,
                       i.sp, i.gat, i.spd, i.awr, i.max_hp, i.max_mana
                FROM player_equipment pe
                JOIN items i ON pe.item_id = i.id
                WHERE pe.user_id = %s
            """, (user_id,))
            rows = cur.fetchall()
            keys = ['strength', 'agility', 'intellect', 'body',
                    'pdf', 'mdf', 'pat', 'mat', 'ddg', 'acc',
                    'sp', 'gat', 'spd', 'awr', 'max_hp', 'max_mana']
            total = {k: 0 for k in keys}
            for row in rows:
                for k in keys:
                    val = row.get(k) if isinstance(row, dict) else row[k]
                    total[k] += val or 0
            return total