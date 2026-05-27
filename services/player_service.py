from datetime import datetime, timezone
from core.db import get_db


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ  ==========
def required_exp(level: int) -> int:
    if level == 1:
        return 20
    return 20 * (2 ** (level - 1))



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

def recalc_derived_stats(user_id: str):
    """Пересчитывает производные характеристики на основе level, body, strength, agility, intellect."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.level, ps.body, ps.strength, ps.agility, ps.intellect
                FROM players p
                JOIN player_stats ps ON p.id = ps.user_id
                WHERE p.id = %s
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                return
            level = row['level']
            body = row['body']
            strength = row['strength']
            agility = row['agility']
            intellect = row['intellect']
            
            max_hp = 100 + (level - 1) * 10 + body * 10
            max_mana = 100 + (level - 1) * 10 + intellect * 10
            pat = strength * 5
            mat = intellect * 5
            sp = intellect * 5          # 0.5% за очко => 5 = 0.5%
            pdf = body * 5
            mdf = body * 5
            awr = body * 5              # 0.5% за очко
            spd = agility * 10 - body * 5    # 1% за ловкость, -0.5% за телосложение
            acc = agility * 5
            ddg = agility * 5
            gat = strength * 5          # 0.5% за очко
            
            cur.execute("""
                UPDATE player_stats
                SET max_hp = %s,
                    max_mana = %s,
                    pat = %s,
                    mat = %s,
                    sp = %s,
                    pdf = %s,
                    mdf = %s,
                    awr = %s,
                    spd = %s,
                    acc = %s,
                    ddg = %s,
                    gat = %s
                WHERE user_id = %s
            """, (max_hp, max_mana, pat, mat, sp, pdf, mdf, awr, spd, acc, ddg, gat, user_id))
            
            # Корректируем текущие HP/Mana, если они превышают новые максимумы
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
