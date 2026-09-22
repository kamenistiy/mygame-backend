from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from core.db import get_db
from services.achievement_service import update_achievement_progress_logic
from services.states_service import (check_expired_states,
    get_active_states)
from core.supabase_client import supabase
from services.notification_service import add_notification
from services.player_service import (
    regen_energy_if_needed,
    add_default_avatars_for_user,
    recalc_derived_stats,
    required_exp,
    apply_regen,
    get_equipment_stats
)
from services.inventory_service import remove_item_from_inventory
from services.achievement_service import grant_achievement_if_not_obtained, update_achievement_progress_logic
# ====== Константы для сборки URL аватара ======
SUPABASE_STORAGE_BASE = "https://onkpedemixygmtllrehp.supabase.co/storage/v1/object/public"
AVATARS_BUCKET = "avatars"

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

class EquipRequest(BaseModel):
    user_id: str
    item_id: str
    slot: str

class UnequipRequest(BaseModel):
    user_id: str
    slot: str

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
                            SET free_stat_points = free_stat_points + %s
                            WHERE user_id = %s
                        """, (level_diff * 2, user_id))
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
    # 1. Применяем регенерацию HP/MP (max_hp уже актуален в БД после recalc)
    apply_regen(user_id)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    base_body, base_strength, base_agility, base_intellect,
                    equip_body, equip_strength, equip_agility, equip_intellect,
                    mod_body, mod_strength, mod_agility, mod_intellect,
                    body_total, strength_total, agility_total, intellect_total,
                    current_hp, max_hp, current_mana, max_mana,
                    current_energy, max_energy, free_stat_points,
                    pdf, mdf, pat, mat, ddg, acc, sp, crft, spd, gat, awr,
                    fame, rep, ins, pvp, pve, unic, zone,
                    p.level
                FROM player_stats ps
                JOIN players p ON p.id = ps.user_id
                WHERE ps.user_id = %s
            """, (user_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Stats not found")

            return {
                # Базовые (за уровень)
                "base_body": row['base_body'],
                "base_strength": row['base_strength'],
                "base_agility": row['base_agility'],
                "base_intellect": row['base_intellect'],
                # Бонусы экипировки
                "equip_body": row['equip_body'],
                "equip_strength": row['equip_strength'],
                "equip_agility": row['equip_agility'],
                "equip_intellect": row['equip_intellect'],
                # Модификаторы состояний
                "mod_body": row['mod_body'],
                "mod_strength": row['mod_strength'],
                "mod_agility": row['mod_agility'],
                "mod_intellect": row['mod_intellect'],
                # Итоговые атрибуты
                "body": row['body_total'],
                "strength": row['strength_total'],
                "agility": row['agility_total'],
                "intellect": row['intellect_total'],
                # HP / MP / Energy
                "current_hp": row['current_hp'],
                "max_hp": row['max_hp'],
                "current_mana": row['current_mana'],
                "max_mana": row['max_mana'],
                "current_energy": row['current_energy'],
                "max_energy": row['max_energy'],
                "free_stat_points": row['free_stat_points'],
                # Производные
                "pdf": row['pdf'], "mdf": row['mdf'],
                "pat": row['pat'], "mat": row['mat'],
                "ddg": row['ddg'], "acc": row['acc'], "sp": row['sp'],
                "crft": row['crft'] or 0,
                "spd": row['spd'], "gat": row['gat'], "awr": row['awr'],
                # Заслуги / свершения
                "fame": row['fame'] or 0,
                "rep": row['rep'] or 0,
                "ins": row['ins'] or 0,
                "pvp": row['pvp'] or 0,
                "pve": row['pve'] or 0,
                "unic": row['unic'] or 0,
                "zone": row['zone'] or 0,
            }
        
@router.post("/player/stats/update")
def update_player_stats(user_id: str, update: StatsUpdate):
    with get_db() as conn:
        with conn.cursor() as cur:
            # Уровень — источник лимита
            cur.execute("SELECT level FROM players WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Player not found")
            max_points = row['level'] * 2

            # Модификаторы активных состояний
            cur.execute("""
                SELECT parameters FROM player_states
                WHERE user_id = %s AND expires_at > NOW()
            """, (user_id,))
            states = cur.fetchall()
            mod_body = sum((s['parameters'] or {}).get('body', 0) for s in states)
            mod_str  = sum((s['parameters'] or {}).get('strength', 0) for s in states)
            mod_agi  = sum((s['parameters'] or {}).get('agility', 0) for s in states)
            mod_int  = sum((s['parameters'] or {}).get('intellect', 0) for s in states)

            new_base_body      = update.body      - mod_body
            new_base_strength  = update.strength  - mod_str
            new_base_agility   = update.agility   - mod_agi
            new_base_intellect = update.intellect - mod_int

            if min(new_base_body, new_base_strength,
                   new_base_agility, new_base_intellect) < 0:
                raise HTTPException(400, "Характеристики не могут быть отрицательными")

            total_invested = (new_base_body + new_base_strength
                              + new_base_agility + new_base_intellect)
            if total_invested > max_points:
                raise HTTPException(400, f"Максимум вложенных очков: {max_points}")

            new_free = max_points - total_invested  # считает СЕРВЕР

            cur.execute("""
                UPDATE player_stats
                SET base_body = %s, base_strength = %s,
                    base_agility = %s, base_intellect = %s,
                    free_stat_points = %s
                WHERE user_id = %s
            """, (new_base_body, new_base_strength, new_base_agility,
                  new_base_intellect, new_free, user_id))
            conn.commit()

    recalc_derived_stats(user_id)
    return {"success": True, "free_stat_points": new_free}

@router.post("/equip")
def equip_item(req: EquipRequest):
    with get_db() as conn:
        with conn.cursor() as cur:
            # 1. Проверить наличие предмета в инвентаре
            cur.execute("SELECT quantity FROM inventory WHERE user_id = %s AND item_id = %s", (req.user_id, req.item_id))
            inv_row = cur.fetchone()
            if not inv_row or inv_row['quantity'] < 1:
                raise HTTPException(400, "Предмет не найден в инвентаре")

            # 2. Проверить, что предмет экипируемый (класс в списке)
            cur.execute("SELECT class FROM items WHERE id = %s", (req.item_id,))
            item_row = cur.fetchone()
            if not item_row:
                raise HTTPException(404, "Предмет не найден")
            allowed_classes = ['weapon', 'helmet', 'armor', 'leggings', 'bracers', 'accessories', 'book', 'pets', 'boots']
            if item_row['class'] not in allowed_classes:
                raise HTTPException(400, "Этот предмет нельзя экипировать")

            # 3. Проверить, занят ли слот
            cur.execute("SELECT item_id FROM player_equipment WHERE user_id = %s AND slot = %s", (req.user_id, req.slot))
            old_item = cur.fetchone()

            # 4. Если слот занят – вернуть старый предмет в инвентарь
            if old_item:
                old_item_id = old_item['item_id']
                cur.execute("""
                    INSERT INTO inventory (user_id, item_id, quantity)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (user_id, item_id) DO UPDATE SET quantity = inventory.quantity + 1
                """, (req.user_id, old_item_id))
                # Удалить старую запись экипировки
                cur.execute("DELETE FROM player_equipment WHERE user_id = %s AND slot = %s", (req.user_id, req.slot))

            # 5. Удалить экипируемый предмет из инвентаря (одну штуку)
            remove_item_from_inventory(req.user_id, req.item_id, 1)

            # 6. Вставить новый предмет в экипировку
            cur.execute("INSERT INTO player_equipment (user_id, slot, item_id) VALUES (%s, %s, %s)", (req.user_id, req.slot, req.item_id))

            conn.commit()
            recalc_derived_stats(req.user_id)
            return {"success": True}

@router.get("/equipment/{user_id}")
def get_equipment(user_id: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT i.*, pe.slot
                FROM player_equipment pe
                JOIN items i ON pe.item_id = i.id
                WHERE pe.user_id = %s
            """, (user_id,))
            rows = cur.fetchall()
            return {"equipment": rows}

@router.post("/unequip")
def unequip_item(req: UnequipRequest):
    with get_db() as conn:
        with conn.cursor() as cur:
            # 1. Проверяем, что слот занят
            cur.execute("SELECT item_id FROM player_equipment WHERE user_id = %s AND slot = %s", (req.user_id, req.slot))
            row = cur.fetchone()
            if not row:
                raise HTTPException(400, "В этом слоте нет предмета")

            item_id = row['item_id']

            # 2. Удаляем запись из экипировки
            cur.execute("DELETE FROM player_equipment WHERE user_id = %s AND slot = %s", (req.user_id, req.slot))

            # 3. Добавляем предмет обратно в инвентарь
            cur.execute("""
                INSERT INTO inventory (user_id, item_id, quantity)
                VALUES (%s, %s, 1)
                ON CONFLICT (user_id, item_id) DO UPDATE SET quantity = inventory.quantity + 1
            """, (req.user_id, item_id))

            conn.commit()
            recalc_derived_stats(req.user_id)
            return {"success": True}

# ====== Хелпер для URL аватара ======
def _build_avatar_url(storage_path):
    """Возвращает публичный URL аватара так же, как /user-avatar/{user_id}."""
    if not storage_path:
        return None
    # Если в БД вдруг уже лежит полный URL — возвращаем как есть
    if isinstance(storage_path, str) and storage_path.startswith("http"):
        return storage_path
    try:
        return supabase.storage.from_("avatars").get_public_url(storage_path)
    except Exception as e:
        print(f"⚠️ Не удалось построить URL аватара ({storage_path}): {e}")
        return None


@router.get("/rating/all")
def get_rating():
    """Отдаёт сводные данные по всем игрокам для вкладки Рейтинг."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    p.id,
                    p.username,
                    p.level,
                    COALESCE(ps.body_total, 0)      AS body_total,
                    COALESCE(ps.strength_total, 0)  AS strength_total,
                    COALESCE(ps.agility_total, 0)   AS agility_total,
                    COALESCE(ps.intellect_total, 0) AS intellect_total,
                    COALESCE(ps.max_hp, 0)   AS max_hp,
                    COALESCE(ps.max_mana, 0) AS max_mana,
                    COALESCE(ps.pat, 0)      AS pat,
                    COALESCE(ps.mat, 0)      AS mat,
                    COALESCE(ps.pdf, 0)      AS pdf,
                    COALESCE(ps.mdf, 0)      AS mdf,
                    COALESCE(ps.ddg, 0)      AS ddg,
                    COALESCE(ps.acc, 0)      AS acc,
                    COALESCE(ps.sp, 0)       AS sp,
                    COALESCE(ps.crft, 0)     AS crft,
                    COALESCE(ps.spd, 0)      AS spd,
                    COALESCE(ps.gat, 0)      AS gat,
                    COALESCE(ps.awr, 0)      AS awr,
                    COALESCE(ps.fame, 0)     AS fame,
                    COALESCE(ps.rep, 0)      AS rep,
                    COALESCE(ps.ins, 0)      AS ins,
                    COALESCE(ps.pvp, 0)      AS pvp,
                    COALESCE(ps.pve, 0)      AS pve,
                    COALESCE(ps.unic, 0)     AS unic,
                    COALESCE(ps.zone, 0)     AS zone,
                    COALESCE((
                        SELECT COUNT(*) FROM user_achievements ua
                        WHERE ua.user_id = p.id AND ua.is_unlocked = true
                    ), 0) AS ach_count,
                    -- Активный аватар
                    (
                        SELECT ua.storage_path FROM user_avatars ua
                        WHERE ua.user_id = p.id AND ua.is_active = true
                        LIMIT 1
                    ) AS avatar_path,
                    -- Заглушки под поражения (добавишь колонки — заменишь на реальные поля)
                    0 AS defeats_pve,
                    0 AS defeats_pvp
                FROM players p
                LEFT JOIN player_stats ps ON ps.user_id = p.id
                WHERE p.username IS NOT NULL
            """)
            rows = cur.fetchall()

            for row in rows:
                # Приведение None → 0
                for k, v in list(row.items()):
                    if v is None and k != 'avatar_path':
                        row[k] = 0
                # Собираем полный URL аватара
                row['avatar_url'] = _build_avatar_url(row.pop('avatar_path', None)) or 'images/avatar.webp'

            return {"players": rows}