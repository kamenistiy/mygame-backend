from fastapi import HTTPException, Header
from core.db import get_db
from typing import Optional


def get_current_user(x_user_id: str = Header(...)):
    if not x_user_id:
        raise HTTPException(401, "No user")
    return x_user_id
# ========== Проверка, является ли пользователь админом: ==========
def get_admin_user(x_user_id: str = Header(...)):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT is_admin FROM players WHERE id = %s", (x_user_id,))
    row = cur.fetchone()

    cur.close()
    conn.close()

    if not row or not row["is_admin"]:
        raise HTTPException(status_code=403, detail="Not admin")

    return x_user_id