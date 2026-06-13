from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from core.db import get_db
from services.achievement_service import update_achievement_progress_logic
from services.states_service import check_expired_states
from core.supabase_client import supabase
from services.notification_service import add_notification
from services.player_service import (
    regen_energy_if_needed,
    add_default_avatars_for_user,
    recalc_derived_stats,
    required_exp,
    apply_regen
)

router = APIRouter()

# --- Модели данных ---
class PlayerUpdate(BaseModel):
    exp: Optional[int] = None
    coins: Optional[int] = None
    level: Optional[int] = None

class StatsUpdate(BaseModel):
    body: int
    strength: int
    agility: int
    intellect: int
    free_points: int


@router.get("/player/{user_id}")
def get_player(user_id: str):
    if user_id == "null":
        raise HTTPException(status_code=400, detail="Invalid user_id")
    try:
        with get_db() as conn:
            with conn.cursor() as cur:

                cur.execute(
                    "SELECT * FROM players WHERE id = %s",
                    (user_id,)
                )

                player = cur.fetchone()

                if player:
                    return player

                # username
                try:
                    user_data = supabase.auth.admin.get_user_by_id(user_id)
                    username = user_data.user.user_metadata.get(
                        'username',
                        'Player'
                    )

                except Exception as e:
                    print(f"Ошибка получения username из Auth: {e}")
                    username = "Player_" + user_id[:8]

                # создание игрока
                cur.execute("""
                    INSERT INTO players
                    (id, username, level, exp, coins, created_at)
                    VALUES (%s, %s, 1, 0, 0, NOW())
                    RETURNING *
                """, (user_id, username))

                player = cur.fetchone()
                conn.commit()

                # стартовые штуки
                add_default_avatars_for_user(user_id)

                add_notification(
                    user_id,
                    'system',
                    'Стартовые Аватары',
                    'Вы получили 10 стартовых Аватаров! Применить их можно во вкладке "Профиль", нажав на значок шестерни.'
                )

                grant_achievement_if_not_obtained(
                    user_id,
                    'alpha_tester'
                )

                add_notification(
                    user_id,
                    'system',
                    'Добро пожаловать!',
                    f'Привет, {username}! Рады видеть тебя в Fastened World.'
                )

                recalc_derived_stats(user_id)
                cur.execute("SELECT approved_avatars_count FROM players WHERE id = %s", (user_id,))
                count_row = cur.fetchone()
                if count_row:
                    count = count_row['approved_avatars_count']
                    for _ in range(count):
                        update_achievement_progress_logic(user_id, 'avatar_lover', 1)
                        update_achievement_progress_logic(user_id, 'avatar_lover_5', 1)
                        update_achievement_progress_logic(user_id, 'avatar_lover_10', 1)
                # -------------------------------------

                return player

    except Exception as e:
        print("❌ GET_PLAYER ERROR:", e)
        raise HTTPException(status_code=500, detail=str(e))
   

@router.post("/player/{user_id}")
def update_player(user_id: str, update: PlayerUpdate):
    with get_db() as conn:
        with conn.cursor() as cur:
            # Проверяем существование игрока
            cur.execute("SELECT * FROM players WHERE id = %s", (user_id,))
            player = cur.fetchone()
            if not player:
                raise HTTPException(status_code=404, detail="Player not found")

            # Текущие значения
            current_exp = player["exp"]
            current_coins = player["coins"]
            current_level = player["level"]

            # Новые монеты
            new_coins = current_coins if update.coins is None else update.coins

            # Обработка опыта (с повышением уровня)
            new_level = current_level
            new_exp = current_exp
            if update.exp is not None:
                exp_to_add = update.exp
                temp_exp = current_exp + exp_to_add
                while temp_exp >= required_exp(new_level):
                    temp_exp -= required_exp(new_level)
                    new_level += 1
                new_exp = temp_exp

            # Обновление в БД
            cur.execute(
                "UPDATE players SET exp = %s, coins = %s, level = %s WHERE id = %s RETURNING *",
                (new_exp, new_coins, new_level, user_id)
            )
            updated = cur.fetchone()
            conn.commit()
            

            # Если уровень повысился – обновляем характеристики
            if new_level > current_level:
                with get_db() as conn_stats:
                    with conn_stats.cursor() as cur_stats:
                        level_diff = new_level - current_level
                        cur_stats.execute("""
                            UPDATE player_stats
                            SET max_hp = max_hp + %s,
                                current_hp = current_hp + %s,
                                max_mana = max_mana + %s,
                                current_mana = current_mana + %s,
                                free_stat_points = free_stat_points + %s
                            WHERE user_id = %s
                        """, (level_diff * 10, level_diff * 10, level_diff * 10, level_diff * 10, level_diff * 2, user_id))
                        conn_stats.commit()
                        recalc_derived_stats(user_id)
                        check_expired_states(user_id)
            return updated


@router.post("/profile/update")
def update_profile(req: dict):
    user_id = req.get('user_id')
    motto = req.get('motto', '')
    bio = req.get('bio', '')
    avatar_id = req.get('avatar_id')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE players SET motto = %s, bio = %s WHERE id = %s", (motto, bio, user_id))
    if avatar_id:
        # Сбросить активный флаг у всех аватаров пользователя
        cur.execute("UPDATE user_avatars SET is_active = false WHERE user_id = %s", (user_id,))
        cur.execute("UPDATE user_avatars SET is_active = true WHERE id = %s AND user_id = %s", (avatar_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"success": True}

# Админка: список всех игроков
@router.get("/admin/players")
def list_players():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, serial_number, username, level, exp, coins, created_at FROM players")
    players = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM players")
    total = cur.fetchone()['count']
    cur.close()
    conn.close()
    return {"total": total, "players": players}

@router.get("/player/stats/{user_id}")
def get_player_stats(user_id: str):
    # 1. Сначала регенерируем ХП/МП
    apply_regen(user_id)
    
    # 2. Затем читаем обновлённые данные
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(""" ... """)
    # 3 ... проверка регенерации энергии ...
    with get_db() as conn:
        with conn.cursor() as cur:
            # Базовые значения
            cur.execute("""
                SELECT base_body, base_strength, base_agility, base_intellect,
                       current_hp, max_hp, current_mana, max_mana, current_energy, max_energy,
                       free_stat_points, p.level
                FROM player_stats ps
                JOIN players p ON p.id = ps.user_id
                WHERE ps.user_id = %s
            """, (user_id,))
            base = cur.fetchone()
            if not base:
                raise HTTPException(404, "Stats not found")

            # Активные состояния с модификаторами
            cur.execute("""
                SELECT parameters FROM player_states
                WHERE user_id = %s AND expires_at > NOW()
            """, (user_id,))
            states = cur.fetchall()

            # Суммируем модификаторы
            mod_body = sum((s['parameters'] or {}).get('body', 0) for s in states)
            mod_str = sum((s['parameters'] or {}).get('strength', 0) for s in states)
            mod_agi = sum((s['parameters'] or {}).get('agility', 0) for s in states)
            mod_int = sum((s['parameters'] or {}).get('intellect', 0) for s in states)
            mod_pat = sum((s['parameters'] or {}).get('pat', 0) for s in states)
            mod_mat = sum((s['parameters'] or {}).get('mat', 0) for s in states)
            mod_pdf = sum((s['parameters'] or {}).get('pdf', 0) for s in states)
            mod_mdf = sum((s['parameters'] or {}).get('mdf', 0) for s in states)

            # Итоговые базовые статы
            total_body = base['base_body'] + mod_body
            total_str = base['base_strength'] + mod_str
            total_agi = base['base_agility'] + mod_agi
            total_int = base['base_intellect'] + mod_int

            # Производные характеристики (формулы из player_service.py)
            level = base['level']
            max_hp = 100 + (level - 1) * 10 + total_body * 10
            max_mana = 100 + (level - 1) * 10 + total_int * 10
            pat = total_str * 5 + mod_pat
            mat = total_int * 5 + mod_mat
            sp = total_int * 5          # 0.5% за очко
            pdf = total_body * 5 + mod_pdf
            mdf = total_body * 5 + mod_mdf
            awr = total_body * 5
            spd = total_agi * 10 - total_body * 5
            acc = total_agi * 5
            ddg = total_agi * 5
            gat = total_str * 5

            return {
                "current_hp": base['current_hp'],
                "max_hp": max_hp,
                "current_mana": base['current_mana'],
                "max_mana": max_mana,
                "current_energy": base['current_energy'],
                "max_energy": base['max_energy'],
                "body": total_body,
                "strength": total_str,
                "agility": total_agi,
                "intellect": total_int,
                "free_stat_points": base['free_stat_points'],
                "pdf": pdf, "mdf": mdf, "pat": pat, "mat": mat,
                "ddg": ddg, "acc": acc, "sp": sp,
                "crft": 0,     # пока заглушка
                "spd": spd, "gat": gat, "awr": awr,
                "fame": 0, "rep": 0, "ins": 0,
                "pvp": 0, "pve": 0, "unic": 0, "zone": 0
            }
        
@router.post("/player/stats/update")
def update_player_stats(user_id: str, update: StatsUpdate):
    with get_db() as conn:
        with conn.cursor() as cur:
            # 1. Получить текущие базовые статы
            cur.execute("""
                SELECT base_body, base_strength, base_agility, base_intellect
                FROM player_stats WHERE user_id = %s
            """, (user_id,))
            current_base = cur.fetchone()
            if not current_base:
                raise HTTPException(404, "Player stats not found")

            # 2. Получить суммарные модификаторы от активных состояний
            cur.execute("""
                SELECT parameters FROM player_states
                WHERE user_id = %s AND expires_at > NOW()
            """, (user_id,))
            states = cur.fetchall()
            mod_body = sum((s['parameters'] or {}).get('body', 0) for s in states)
            mod_str = sum((s['parameters'] or {}).get('strength', 0) for s in states)
            mod_agi = sum((s['parameters'] or {}).get('agility', 0) for s in states)
            mod_int = sum((s['parameters'] or {}).get('intellect', 0) for s in states)

            # 3. Вычислить новые базовые значения (переданные итоговые - модификаторы)
            new_base_body = update.body - mod_body
            new_base_strength = update.strength - mod_str
            new_base_agility = update.agility - mod_agi
            new_base_intellect = update.intellect - mod_int

            # 4. Обновить базовые колонки и free_stat_points
            cur.execute("""
                UPDATE player_stats
                SET base_body = %s,
                    base_strength = %s,
                    base_agility = %s,
                    base_intellect = %s,
                    free_stat_points = %s
                WHERE user_id = %s
            """, (new_base_body, new_base_strength, new_base_agility, new_base_intellect,
                  update.free_points, user_id))

            if cur.rowcount == 0:
                raise HTTPException(404, "Player stats not found")
            conn.commit()

    # После обновления базовых статов пересчитываем производные
    recalc_derived_stats(user_id)
    return {"success": True}