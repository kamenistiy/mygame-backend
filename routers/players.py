from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from core.db import get_db
from services.achievement_service import grant_achievement_if_not_obtained
from core.supabase_client import supabase
from services.notification_service import add_notification
from services.player_service import (
    regen_energy_if_needed,
    add_default_avatars_for_user,
    recalc_derived_stats,
    required_exp
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
    regen_energy_if_needed(user_id)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT current_hp, max_hp, current_mana, max_mana, current_energy, max_energy,
                       body, strength, agility, intellect, free_stat_points,
                       pdf, mdf, pat, mat, ddg, acc, sp,
                       crft, spd, gat, awr,
                       fame, rep, ins,
                       pvp, pve, unic, zone
                FROM player_stats
                WHERE user_id = %s
            """, (user_id,))
            stats = cur.fetchone()
            if not stats:
                raise HTTPException(status_code=404, detail="Stats not found")
            return stats
        
@router.post("/player/stats/update")
def update_player_stats(user_id: str, update: StatsUpdate):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE player_stats
                SET body = %s, strength = %s, agility = %s, intellect = %s, free_stat_points = %s
                WHERE user_id = %s
            """, (update.body, update.strength, update.agility, update.intellect, update.free_points, user_id))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Player stats not found")
            conn.commit()
    # Пересчитываем производные
    recalc_derived_stats(user_id)
    return {"success": True}