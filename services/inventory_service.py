from core.db import get_db
from fastapi import HTTPException
from datetime import datetime

from services.notification_service import add_notification
from services.player_service import recalc_derived_stats

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

    if item['class'] != 'consumable':
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
            f'Вы использовали Фолиант смены Пути. У вас {correct_free_points} очков.'
        )

        return {"success": True, "message": "Stats reset"}

    raise HTTPException(status_code=400, detail="Не реализовано")