from core.db import get_db
from fastapi import HTTPException
from datetime import datetime

from services.notification_service import add_notification
from services.player_service import recalc_derived_stats

# Словарь: что выдаёт каждый сундук (ID должны существовать в таблице items)
CHEST_ITEMS = {
    "archer_box": [
        {"id": "novice_bow", "qty": 1},
        {"id": "novice_helm", "qty": 1},
        {"id": "novice_armor1", "qty": 1},
        {"id": "novice_boots1", "qty": 1},
    ],
    "warrior_box": [
        {"id": "novice_sword", "qty": 1},
        {"id": "novice_helm", "qty": 1},
        {"id": "novice_armor1", "qty": 1},
        {"id": "novice_boots1", "qty": 1},
    ],
    "musician_box": [
        {"id": "novice_muse", "qty": 1},
        {"id": "novice_hat", "qty": 1},
        {"id": "novice_armor3", "qty": 1},
        {"id": "novice_boots3", "qty": 1},
    ],
    "mage_box": [
        {"id": "novice_staff", "qty": 1},
        {"id": "novice_hood", "qty": 1},
        {"id": "novice_armor2", "qty": 1},
        {"id": "novice_boots2", "qty": 1},
    ],
}


def remove_item_from_inventory(user_id: str, item_id: str, quantity: int = 1) -> bool:
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT quantity FROM inventory WHERE user_id = %s AND item_id = %s",
            (user_id, item_id)
        )
        row = cur.fetchone()

        if not row or row['quantity'] < quantity:
            return False

        new_qty = row['quantity'] - quantity

        if new_qty == 0:
            cur.execute(
                "DELETE FROM inventory WHERE user_id = %s AND item_id = %s",
                (user_id, item_id)
            )
        else:
            cur.execute(
                "UPDATE inventory SET quantity = %s WHERE user_id = %s AND item_id = %s",
                (new_qty, user_id, item_id)
            )

        conn.commit()
        return True

    finally:
        cur.close()
        conn.close()


def use_item_logic(user_id: str, req, conn, cur):
    """
    Вся логика use_item сюда
    """

    cur.execute("SELECT * FROM items WHERE id = %s", (req.item_id,))
    item = cur.fetchone()

    if not item:
        raise HTTPException(status_code=404, detail="Предмет не найден")

    # Разрешаем использовать consumable и chests
    if item['class'] not in ['consumable', 'chests']:
        raise HTTPException(status_code=400, detail="Этот предмет нельзя использовать")

    # === AVATAR CERTIFICATE ===
    if req.item_id == 'avatar_certificate':
        cur.execute("""
            SELECT id FROM avatar_requests
            WHERE user_id = %s AND status = 'pending'
        """, (user_id,))
        pending = cur.fetchone()

        if pending:
            raise HTTPException(status_code=400, detail="У вас уже есть аватар на модерации")

        removed = remove_item_from_inventory(user_id, req.item_id, req.quantity)
        if not removed:
            raise HTTPException(status_code=400, detail="Недостаточно предметов")

        cur.execute("""
            INSERT INTO avatar_requests (user_id, status, username)
            VALUES (%s, 'pending', (SELECT username FROM players WHERE id = %s))
            RETURNING id
        """, (user_id, user_id))

        new_request = cur.fetchone()
        conn.commit()

        return {"success": True, "request_id": new_request["id"]}

    # === STATS CERTIFICATE ===
    if req.item_id == 'stats_certificate':
        cur.execute("""
            SELECT p.level, ps.base_body, ps.base_strength, ps.base_agility, ps.base_intellect, ps.free_stat_points
            FROM players p
            JOIN player_stats ps ON p.id = ps.user_id
            WHERE p.id = %s
        """, (user_id,))

        player_stats = cur.fetchone()
        if not player_stats:
            raise HTTPException(status_code=404, detail="Player not found")

        level = player_stats['level']
        correct_free_points = (level - 1) * 2 + 2

        cur.execute("""
            UPDATE player_stats 
            SET base_body = 0, base_strength = 0, base_agility = 0, base_intellect = 0, 
                free_stat_points = free_stat_points + %s
            WHERE user_id = %s
        """, (correct_free_points, user_id))

        conn.commit()

        recalc_derived_stats(user_id)
        remove_item_from_inventory(user_id, req.item_id, req.quantity)

        add_notification(
            user_id,
            'system',
            'Очки характеристик сброшены',
            f'Вы использовали Фолиант смены Пути. У вас {correct_free_points} ед. свободных очков.'
        )

        return {"success": True, "message": "Stats reset"}

    # === ОБЩАЯ ЛОГИКА ДЛЯ ВСЕХ СУНДУКОВ ===
    if req.item_id in CHEST_ITEMS:
        # Проверяем наличие сундука
        cur.execute(
            "SELECT quantity FROM inventory WHERE user_id = %s AND item_id = %s",
            (user_id, req.item_id)
        )
        row = cur.fetchone()
        if not row or row['quantity'] < 1:
            raise HTTPException(status_code=400, detail="Недостаточно предметов")

        # Удаляем один сундук
        remove_item_from_inventory(user_id, req.item_id, 1)

        # Получаем список предметов для этого сундука
        items_to_give = CHEST_ITEMS[req.item_id]

        # Добавляем каждый предмет в инвентарь
        for item_data in items_to_give:
            cur.execute("""
                INSERT INTO inventory (user_id, item_id, quantity)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, item_id)
                DO UPDATE SET quantity = inventory.quantity + %s
            """, (user_id, item_data["id"], item_data["qty"], item_data["qty"]))

        conn.commit()

        # Получаем информацию о добавленных предметах (названия и иконки)
        ids = [item["id"] for item in items_to_give]
        placeholders = ','.join(['%s'] * len(ids))
        cur.execute(f"SELECT id, name, icon FROM items WHERE id IN ({placeholders})", ids)
        rows = cur.fetchall()

        items_info = []
        for r in rows:
            qty = next((item["qty"] for item in items_to_give if item["id"] == r["id"]), 1)
            items_info.append({"id": r["id"], "name": r["name"], "icon": r["icon"], "quantity": qty})

        # Название сундука для сообщения
        chest_name = item['name']

        return {
            "success": True,
            "message": f"Вы использовали {chest_name}",
            "items": items_info
        }

    # Если ни одно условие не сработало
    raise HTTPException(status_code=400, detail="Не реализовано")