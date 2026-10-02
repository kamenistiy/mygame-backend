from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
from core.db import get_db
from services.inventory_service import remove_item_from_inventory

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
    """Если время пешего пути истекло — завершаем его и обновляем позицию."""
    now = datetime.now(timezone.utc)
    if now >= movement['end_time']:
        cur.execute(
            "UPDATE player_movements SET status = 'completed' WHERE id = %s",
            (movement['id'],)
        )
        cur.execute("""
            UPDATE player_positions
            SET current_region_id = %s, current_city_id = NULL, updated_at = NOW()
            WHERE user_id = %s
        """, (movement['to_region_id'], movement['user_id']))
        return True
    return False


# ========== Эндпоинты ==========

@router.get("/regions")
def get_regions():
    """Все регионы для карты мира."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, description, background_image, map_x, map_y
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
                SELECT id, name, description, background_image, map_x, map_y
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
    """Текущая позиция игрока. Используется фронтом при открытии карты."""
    with get_db() as conn:
        with conn.cursor() as cur:
            # 1. Если игрок в пути — сначала завершим просроченное перемещение
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

            # 2. Позиция
            cur.execute("""
                SELECT current_region_id, current_city_id
                FROM player_positions
                WHERE user_id = %s
            """, (user_id,))
            pos = cur.fetchone()
            if not pos:
                return {"region_id": None, "city_id": None}

            return {
                "region_id": pos['current_region_id'],
                "city_id": pos['current_city_id'],
                "in_movement": movement is not None
            }


@router.get("/connections/{region_id}")
def get_connections(region_id: str, user_id: str):
    """Доступные переходы из региона + данные для модалки выбора пути."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    rc.id,
                    rc.to_region_id,
                    rc.gold_cost,
                    rc.item_id,
                    rc.time_seconds,
                    r.name AS to_region_name,
                    r.background_image AS to_region_image
                FROM region_connections rc
                JOIN regions r ON r.id = rc.to_region_id
                WHERE rc.from_region_id = %s
            """, (region_id,))
            connections = cur.fetchall()

            # Проверяем наличие предмета у игрока для каждого перехода
            for c in connections:
                c['has_item'] = False
                if c['item_id']:
                    cur.execute("""
                        SELECT quantity FROM inventory
                        WHERE user_id = %s AND item_id = %s
                    """, (user_id, c['item_id']))
                    inv = cur.fetchone()
                    if inv and inv['quantity'] > 0:
                        c['has_item'] = True

            return {"connections": connections}


@router.post("/travel")
def start_travel(req: TravelRequest):
    """Начать перемещение между регионами."""
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
            cur.execute("""
                SELECT current_region_id FROM player_positions
                WHERE user_id = %s
            """, (req.user_id,))
            pos = cur.fetchone()
            if not pos or not pos['current_region_id']:
                raise HTTPException(400, "Позиция игрока не определена")

            from_region_id = pos['current_region_id']

            # 3. Есть ли путь?
            cur.execute("""
                SELECT * FROM region_connections
                WHERE from_region_id = %s AND to_region_id = %s
            """, (from_region_id, req.to_region_id))
            connection = cur.fetchone()
            if not connection:
                raise HTTPException(400, "Нет прямого пути в этот регион")

            # 4. Обработка способа
            if req.method == 'gold':
                cur.execute("SELECT coins FROM players WHERE id = %s", (req.user_id,))
                player = cur.fetchone()
                if player['coins'] < connection['gold_cost']:
                    raise HTTPException(400, "Недостаточно золота")
                cur.execute(
                    "UPDATE players SET coins = coins - %s WHERE id = %s",
                    (connection['gold_cost'], req.user_id)
                )
                cur.execute("""
                    UPDATE player_positions
                    SET current_region_id = %s, current_city_id = NULL, updated_at = NOW()
                    WHERE user_id = %s
                """, (req.to_region_id, req.user_id))
                conn.commit()
                return {"success": True, "type": "instant"}

            elif req.method == 'item':
                if not connection['item_id']:
                    raise HTTPException(400, "Для этого пути не требуется предмет")
                cur.execute("""
                    SELECT quantity FROM inventory
                    WHERE user_id = %s AND item_id = %s
                """, (req.user_id, connection['item_id']))
                inv = cur.fetchone()
                if not inv or inv['quantity'] < 1:
                    raise HTTPException(400, "У вас нет нужного предмета")
                remove_item_from_inventory(req.user_id, connection['item_id'], 1)
                cur.execute("""
                    UPDATE player_positions
                    SET current_region_id = %s, current_city_id = NULL, updated_at = NOW()
                    WHERE user_id = %s
                """, (req.to_region_id, req.user_id))
                conn.commit()
                return {"success": True, "type": "instant"}

            elif req.method == 'walk':
                end_time = datetime.now(timezone.utc) + timedelta(seconds=connection['time_seconds'])
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
                    "end_time": end_time.isoformat()
                }

            raise HTTPException(400, "Неизвестный способ перемещения")


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