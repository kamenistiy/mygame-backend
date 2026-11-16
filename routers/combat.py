from fastapi import APIRouter, HTTPException
from services.combat_service import process_combat_round
from core.db import get_db

router = APIRouter(tags=["combat"])

@router.post("/combat/attack")
def combat_attack(user_id: str):
    try:
        return process_combat_round(user_id, "attack")
    except ValueError as e:
        raise HTTPException(400, str(e))

@router.post("/combat/flee")
def combat_flee(user_id: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT active_enemy_id FROM player_stats WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            if not row or not row['active_enemy_id']:
                raise HTTPException(400, "Вы не в бою")
                
            cur.execute("UPDATE player_stats SET active_enemy_id = NULL, enemy_current_hp = NULL WHERE user_id = %s", (user_id,))
            conn.commit()
            return {"success": True}