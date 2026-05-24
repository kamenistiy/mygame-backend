from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.db import get_db

router = APIRouter()

# ========== МОДЕЛИ ДЛЯ ИНВЕНТАРЯ ==========
class AddItemRequest(BaseModel):
    item_id: str
    quantity: int = 1

class UseItemRequest(BaseModel):
    item_id: str
    quantity: int = 1

class RemoveItemRequest(BaseModel):
    item_id: str
    quantity: int = 1

class MoveToJunkRequest(BaseModel):
    item_id: str
    quantity: int = 1
    
@router.post("/inventory/use")
def use_item(user_id: str, req: UseItemRequest):
    conn = get_db()
    cur = conn.cursor()

    result = use_item_logic(user_id, req, conn, cur)

    conn.commit()
    cur.close()
    conn.close()

    return result

@router.get("/inventory/{user_id}")
def get_inventory(user_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT i.id, i.name, i.description, i.class, i.icon, inv.quantity,
            i.rarity, i.level, i.strength, i.agility, i.intellect, i.body,
            i.price
        FROM inventory inv
        JOIN items i ON inv.item_id = i.id
        WHERE inv.user_id = %s
    """, (user_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    return items

@router.post("/inventory/add")
def add_item(user_id: str, req: AddItemRequest):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO inventory (user_id, item_id, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, item_id)
            DO UPDATE SET quantity = inventory.quantity + EXCLUDED.quantity
            RETURNING *
        """, (user_id, req.item_id, req.quantity))
        result = cur.fetchone()
        conn.commit()
        return {"success": True, "item": result}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@router.post("/inventory/remove")
def remove_item(user_id: str, req: RemoveItemRequest):
    removed = remove_item_from_inventory(user_id, req.item_id, req.quantity)
    if not removed:
        raise HTTPException(status_code=400, detail="Недостаточно предметов или предмет не найден")
    return {"success": True}

@router.post("/inventory/move_to_junk")
def move_to_junk(user_id: str, req: MoveToJunkRequest):
    conn = get_db()
    cur = conn.cursor()
    try:
        # 1. Уменьшаем количество в обычном инвентаре
        cur.execute("SELECT quantity FROM inventory WHERE user_id = %s AND item_id = %s", (user_id, req.item_id))
        row = cur.fetchone()
        if not row or row['quantity'] < req.quantity:
            raise HTTPException(status_code=400, detail="Недостаточно предметов")
        new_qty = row['quantity'] - req.quantity
        if new_qty == 0:
            cur.execute("DELETE FROM inventory WHERE user_id = %s AND item_id = %s", (user_id, req.item_id))
        else:
            cur.execute("UPDATE inventory SET quantity = %s WHERE user_id = %s AND item_id = %s", (new_qty, user_id, req.item_id))
        
        # 2. Добавляем в junk_inventory
        cur.execute("""
            INSERT INTO junk_inventory (user_id, item_id, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, item_id)
            DO UPDATE SET quantity = junk_inventory.quantity + EXCLUDED.quantity
        """, (user_id, req.item_id, req.quantity))
        
        conn.commit()
        return {"success": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

def remove_item_from_inventory(user_id: str, item_id: str, quantity: int = 1) -> bool:
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT quantity FROM inventory WHERE user_id = %s AND item_id = %s", (user_id, item_id))
        row = cur.fetchone()
        if not row or row['quantity'] < quantity:
            return False
        new_qty = row['quantity'] - quantity
        if new_qty == 0:
            cur.execute("DELETE FROM inventory WHERE user_id = %s AND item_id = %s", (user_id, item_id))
        else:
            cur.execute("UPDATE inventory SET quantity = %s WHERE user_id = %s AND item_id = %s", (new_qty, user_id, item_id))
        conn.commit()
        return True
    except Exception as e:
        print("Error removing item:", e)
        return False
    finally:
        cur.close()
        conn.close()

@router.get("/inventory/junk/{user_id}")
def get_junk_inventory(user_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT i.id, i.name, i.description, i.icon, j.quantity, i.rarity, i.level, i.price
        FROM junk_inventory j
        JOIN items i ON j.item_id = i.id
        WHERE j.user_id = %s
    """, (user_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    return items


