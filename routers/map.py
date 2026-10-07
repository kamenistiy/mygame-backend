from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
from core.db import get_db
from services.inventory_service import remove_item_from_inventory
from collections import deque

router = APIRouter(prefix="/map", tags=["map"])


# ========== Модели ==========
class TravelRequest(BaseModel):
    user_id: str
    to_region_id: str
    method: str  # 'gold' | 'item' | 'walk'


class PositionUpdate(BaseModel):
    user_id: str
    settlement_id: str


# ========== Хелперы ==========
def _finalize_movement_if_due(cur, movement):
    """Если время пешего пути истекло — завершаем его и обновляем позицию.
       Запись НЕ удаляем, чтобы фронт мог узнать о прибытии (arrival_pending)."""
    now = datetime.now(timezone.utc)
    if now >= movement['end_time']:
        cur.execute("""
            UPDATE player_movements
            SET status = 'completed'
            WHERE id = %s
        """, (movement['id'],))
        cur.execute("""
            UPDATE player_positions
            SET current_region_id = %s, current_city_id = NULL, updated_at = NOW()
            WHERE user_id = %s
        """, (movement['to_region_id'], movement['user_id']))
        return True
    return False

def _find_path(cur, from_region_id, to_region_id):
    """BFS: кратчайший путь между регионами. Возвращает список ID (включая оба конца) или None."""
    if not from_region_id or not to_region_id:
        return None
    if from_region_id == to_region_id:
        return [from_region_id]

    cur.execute("SELECT from_region_id, to_region_id FROM region_connections")
    edges = cur.fetchall()

    adj = {}
    for e in edges:
        adj.setdefault(e['from_region_id'], []).append(e['to_region_id'])

    queue = deque([from_region_id])
    parent = {from_region_id: None}

    while queue:
        node = queue.popleft()
        for nxt in adj.get(node, []):
            if nxt in parent:
                continue
            parent[nxt] = node
            if nxt == to_region_id:
                path = [nxt]
                while path[-1] != from_region_id:
                    path.append(parent[path[-1]])
                path.reverse()
                return path
            queue.append(nxt)
    return None

# ========== Эндпоинты ==========

@router.get("/regions")
def get_regions():
    """Все регионы для карты мира."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, description, background_image, map_x, map_y, map_icon
                FROM regions
                ORDER BY name
            """)
            return {"regions": cur.fetchall()}


@router.get("/regions/{region_id}")
def get_region_details(region_id: str):
    """Регион + список городов для режима 'Регион'."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM regions WHERE id = %s", (region_id,))
            region = cur.fetchone()
            if not region:
                raise HTTPException(404, "Регион не найден")

            cur.execute("""
                SELECT id, name, description, background_image, map_x, map_y, map_icon
                FROM settlements
                WHERE region_id = %s
                ORDER BY name
            """, (region_id,))
            region['settlements'] = cur.fetchall()
            return region


@router.get("/settlements/{settlement_id}")
def get_settlement_details(settlement_id: str):
    """Город. Пока заглушка — вернёт базовые поля."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM settlements WHERE id = %s", (settlement_id,))
            settlement = cur.fetchone()
            if not settlement:
                raise HTTPException(404, "Город не найден")
            return settlement


@router.get("/position/{user_id}")
def get_player_position(user_id: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            # 1. Догнать просроченное перемещение
            cur.execute("""
                SELECT * FROM player_movements
                WHERE user_id = %s AND status = 'in_progress'
                LIMIT 1
            """, (user_id,))
            movement = cur.fetchone()
            if movement:
                if _finalize_movement_if_due(cur, movement):
                    conn.commit()
                    movement = None

            # 2. Проверить, есть ли непрочитанное прибытие
            cur.execute("""
                SELECT id, to_region_id FROM player_movements
                WHERE user_id = %s AND status = 'completed' AND notified = FALSE
                ORDER BY created_at DESC LIMIT 1
            """, (user_id,))
            arrival = cur.fetchone()

            arrival_region_name = None
            if arrival:
                cur.execute("SELECT name FROM regions WHERE id = %s", (arrival['to_region_id'],))
                r = cur.fetchone()
                if r:
                    arrival_region_name = r['name']
                cur.execute("UPDATE player_movements SET notified = TRUE WHERE id = %s", (arrival['id'],))
                conn.commit()

            # 3. Позиция + счётчики
            cur.execute("""
                SELECT
                    pp.current_region_id,
                    pp.current_city_id,
                    r.name AS region_name,
                    r.travel_background,
                    s.name AS city_name,
                    (
                        SELECT COUNT(*) FROM player_positions pp2
                        WHERE pp2.current_region_id = pp.current_region_id
                    ) AS players_count
                FROM player_positions pp
                LEFT JOIN regions r ON r.id = pp.current_region_id
                LEFT JOIN settlements s ON s.id = pp.current_city_id
                WHERE pp.user_id = %s
            """, (user_id,))
            pos = cur.fetchone()
            if not pos:
                return {"region_id": None, "city_id": None, "region_name": None,
                        "city_name": None, "travel_background": None, "players_count": 0}

            return {
                "region_id": pos['current_region_id'],
                "city_id": pos['current_city_id'],
                "region_name": pos['region_name'],
                "travel_background": pos['travel_background'], 
                "city_name": pos['city_name'],
                "players_count": pos['players_count'] or 0,
                "in_movement": movement is not None,
                "arrival_pending": arrival_region_name
            }


@router.post("/travel")
def start_travel(req: TravelRequest):
    ENERGY_PER_HOP = {'gold': 3, 'walk': 3, 'teleport': 5}
    if req.method not in ENERGY_PER_HOP:
        raise HTTPException(400, "Неизвестный способ перемещения")

    with get_db() as conn:
        with conn.cursor() as cur:
            # 1. Уже в пути?
            cur.execute("""
                SELECT id FROM player_movements
                WHERE user_id = %s AND status = 'in_progress'
            """, (req.user_id,))
            if cur.fetchone():
                raise HTTPException(400, "Вы уже в пути")

            # 2. Текущая позиция
            cur.execute("SELECT current_region_id FROM player_positions WHERE user_id = %s", (req.user_id,))
            pos = cur.fetchone()
            if not pos or not pos['current_region_id']:
                raise HTTPException(400, "Позиция игрока не определена")
            from_region_id = pos['current_region_id']

            # 3. Ищем путь
            path = _find_path(cur, from_region_id, req.to_region_id)
            if not path or len(path) < 2:
                raise HTTPException(400, "Путь не найден")

            hops = len(path) - 1

            # 4. Считаем суммарные затраты
            total_gold = 0
            total_time = 0
            for i in range(hops):
                cur.execute("""
                    SELECT gold_cost, time_seconds FROM region_connections
                    WHERE from_region_id = %s AND to_region_id = %s
                """, (path[i], path[i + 1]))
                edge = cur.fetchone()
                if not edge:
                    raise HTTPException(500, "Связь между регионами отсутствует")
                total_gold += edge['gold_cost'] or 0
                total_time += edge['time_seconds'] or 0

            total_energy = ENERGY_PER_HOP[req.method] * hops

            # 5. Проверяем энергию
            cur.execute("SELECT current_energy FROM player_stats WHERE user_id = %s", (req.user_id,))
            ps = cur.fetchone()
            if not ps or ps['current_energy'] < total_energy:
                raise HTTPException(400, f"Недостаточно энергии (нужно {total_energy})")

            # 6. Списываем энергию
            cur.execute(
                """UPDATE player_stats 
                SET current_energy = current_energy - %s,
                    last_energy_regen = CASE 
                        WHEN current_energy >= max_energy THEN NOW() 
                        ELSE last_energy_regen 
                    END
                WHERE user_id = %s""",
                (total_energy, req.user_id)
            )

            # 7. Обработка способа
            if req.method == 'gold':
                cur.execute("SELECT coins FROM players WHERE id = %s", (req.user_id,))
                player = cur.fetchone()
                if player['coins'] < total_gold:
                    raise HTTPException(400, f"Недостаточно монет (нужно {total_gold})")
                cur.execute(
                    "UPDATE players SET coins = coins - %s WHERE id = %s",
                    (total_gold, req.user_id)
                )
                cur.execute("""
                    UPDATE player_positions
                    SET current_region_id = %s, current_city_id = NULL, updated_at = NOW()
                    WHERE user_id = %s
                """, (req.to_region_id, req.user_id))
                conn.commit()
                return {"success": True, "type": "instant", "hops": hops}

            elif req.method == 'teleport':
                cur.execute("""
                    UPDATE player_positions
                    SET current_region_id = %s, current_city_id = NULL, updated_at = NOW()
                    WHERE user_id = %s
                """, (req.to_region_id, req.user_id))
                conn.commit()
                return {"success": True, "type": "instant", "hops": hops}

            elif req.method == 'walk':
                end_time = datetime.now(timezone.utc) + timedelta(seconds=total_time)
                cur.execute("""
                    INSERT INTO player_movements
                    (user_id, from_region_id, to_region_id, method, end_time, status)
                    VALUES (%s, %s, %s, 'walk', %s, 'in_progress')
                    RETURNING id
                """, (req.user_id, from_region_id, req.to_region_id, end_time))
                movement_id = cur.fetchone()['id']
                conn.commit()
                return {
                    "success": True,
                    "type": "walk",
                    "movement_id": str(movement_id),
                    "end_time": end_time.isoformat(),
                    "total_seconds": total_time,
                    "hops": hops
                }


@router.get("/movement/status")
def get_movement_status(user_id: str):
    """Текущее состояние пешего перемещения (для прогресс-бара)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM player_movements
                WHERE user_id = %s AND status = 'in_progress'
                ORDER BY created_at DESC LIMIT 1
            """, (user_id,))
            movement = cur.fetchone()

            if not movement:
                return {"in_progress": False}

            if _finalize_movement_if_due(cur, movement):
                conn.commit()
                return {"in_progress": False, "completed": True}

            now = datetime.now(timezone.utc)
            remaining = int((movement['end_time'] - now).total_seconds())
            total = int((movement['end_time'] - movement['created_at']).total_seconds())
            return {
                "in_progress": True,
                "remaining_seconds": remaining,
                "total_seconds": total,
                "from_region_id": movement['from_region_id'],
                "to_region_id": movement['to_region_id']
            }


@router.post("/position")
def update_position(req: PositionUpdate):
    """Перемещение внутри региона (свободное)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.region_id, p.current_region_id
                FROM settlements s
                JOIN player_positions p ON p.user_id = %s
                WHERE s.id = %s
            """, (req.user_id, req.settlement_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Город или позиция не найдены")
            if row['region_id'] != row['current_region_id']:
                raise HTTPException(400, "Этот город в другом регионе")

            cur.execute("""
                UPDATE player_positions
                SET current_city_id = %s, updated_at = NOW()
                WHERE user_id = %s
            """, (req.settlement_id, req.user_id))
            conn.commit()
            return {"success": True}

@router.post("/movement/cancel")
def cancel_movement(user_id: str):
    """Отменить пеший путь. Игрок остаётся в исходном регионе."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM player_movements
                WHERE user_id = %s AND status = 'in_progress'
                LIMIT 1
            """, (user_id,))
            m = cur.fetchone()
            if not m:
                raise HTTPException(400, "Нет активного перемещения")

            cur.execute("""
                UPDATE player_movements SET status = 'cancelled'
                WHERE id = %s
            """, (m['id'],))
            conn.commit()
            return {"success": True}

@router.get("/travel-info/{user_id}/{to_region_id}")
def get_travel_info(user_id: str, to_region_id: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_region_id FROM player_positions WHERE user_id = %s", (user_id,))
            pos = cur.fetchone()
            if not pos or not pos['current_region_id']:
                raise HTTPException(404, "Позиция игрока не определена")
            from_region_id = pos['current_region_id']

            if from_region_id == to_region_id:
                raise HTTPException(400, "Вы уже в этом регионе")

            path = _find_path(cur, from_region_id, to_region_id)
            if not path:
                raise HTTPException(404, "Путь не найден")

            hops = len(path) - 1

            total_gold = 0
            total_time = 0
            for i in range(hops):
                cur.execute("""
                    SELECT gold_cost, time_seconds FROM region_connections
                    WHERE from_region_id = %s AND to_region_id = %s
                """, (path[i], path[i + 1]))
                edge = cur.fetchone()
                if not edge:
                    raise HTTPException(500, "Связь между регионами отсутствует")
                total_gold += edge['gold_cost'] or 0
                total_time += edge['time_seconds'] or 0

            cur.execute("SELECT id, name FROM regions WHERE id::text = ANY(%s)", (path,))
            names = {r['id']: r['name'] for r in cur.fetchall()}
            path_names = [names.get(rid, '?') for rid in path]

            return {
                "path": path,
                "path_names": path_names,
                "hops": hops,
                "to_name": names.get(to_region_id, '?'),
                "gold": {"cost": total_gold, "energy": 3 * hops},
                "walk": {"time_seconds": total_time, "energy": 3 * hops},
                "teleport": {"energy": 5 * hops}
            }