from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.states_service import apply_state, get_active_states, check_expired_states

router = APIRouter(prefix="/states", tags=["states"])

class ApplyStateRequest(BaseModel):
    user_id: str
    state_key: str
    duration_seconds: int = 10

@router.post("/apply")
def apply_state_endpoint(req: ApplyStateRequest):
    allowed_states = ['exhaustion', 'weakness', 'inspiration', 'rage']
    if req.state_key not in allowed_states:
        raise HTTPException(status_code=400, detail="Unknown state key")
    apply_state(req.user_id, req.state_key, req.duration_seconds)
    return {"success": True}

@router.get("/active/{user_id}")
def get_active_states_endpoint(user_id: str):
    return get_active_states(user_id)

@router.post("/check_expired/{user_id}")
def check_expired_endpoint(user_id: str):
    check_expired_states(user_id)
    return {"success": True}