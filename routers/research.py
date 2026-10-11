from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from core.db import get_db
from services import research_service


router = APIRouter(tags=["research"])


class SubmitTextPayload(BaseModel):
    theme: str = Field(..., max_length=60)
    content: str = Field(..., max_length=500)


class RejectPayload(BaseModel):
    comment: str


def _require_user(x_user_id: str | None) -> str:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Не авторизован")
    return x_user_id


def _require_admin(user_id: str) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT is_admin FROM players WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if not row or not row["is_admin"]:
                raise HTTPException(status_code=403, detail="Требуются права администратора")


# ---------- игрок ----------

@router.post("/research/texts/submit")
def submit_text(
    payload: SubmitTextPayload,
    x_user_id: str = Header(..., alias="x-user-id"),
):
    uid = _require_user(x_user_id)
    try:
        result = research_service.submit_text(uid, payload.theme, payload.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "text": result}


# ---------- админ ----------

@router.get("/admin/research/texts")
def list_pending_texts(
    status: str = "pending",
    x_user_id: str = Header(..., alias="x-user-id"),
):
    uid = _require_user(x_user_id)
    _require_admin(uid)
    if status != "pending":
        raise HTTPException(status_code=400, detail="Поддерживается только status=pending")
    return {"texts": research_service.list_pending()}


@router.post("/admin/research/texts/{text_id}/approve")
def approve_text(
    text_id: str,
    x_user_id: str = Header(..., alias="x-user-id"),
):
    uid = _require_user(x_user_id)
    _require_admin(uid)
    try:
        result = research_service.approve_text(text_id, uid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@router.post("/admin/research/texts/{text_id}/reject")
def reject_text(
    text_id: str,
    payload: RejectPayload,
    x_user_id: str = Header(..., alias="x-user-id"),
):
    uid = _require_user(x_user_id)
    _require_admin(uid)
    try:
        result = research_service.reject_text(text_id, uid, payload.comment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}