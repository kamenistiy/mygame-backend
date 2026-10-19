from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from core.db import get_db
from services import research_service
from services.achievement_service import update_achievement_progress_logic


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
def approve_text(text_id: str, admin_id: str) -> dict:
    """is_active = TRUE, счётчик автору +1, ачивки, нотификация."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, author_id, theme FROM research_texts
                WHERE id = %s AND is_active = FALSE
            """, (text_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("Текст не найден или уже обработан")

            author_id = row['author_id']
            theme = row['theme'] or '(без темы)'

            cur.execute(
                "UPDATE research_texts SET is_active = TRUE WHERE id = %s",
                (text_id,)
            )
            cur.execute("""
                UPDATE players SET approved_texts_count = approved_texts_count + 1
                WHERE id = %s
            """, (author_id,))
            conn.commit()

    # ===== Начисление прогресса ачивок за тексты =====
    # Вызываем ПОСЛЕ commit, т.к. функция открывает своё соединение
    update_achievement_progress_logic(str(author_id), 'notesadd_1', 1)
    update_achievement_progress_logic(str(author_id), 'notesadd_10', 1)
    update_achievement_progress_logic(str(author_id), 'notesadd_50', 1)
    update_achievement_progress_logic(str(author_id), 'notesadd_100', 1)
    update_achievement_progress_logic(str(author_id), 'notesadd_200', 1)
    update_achievement_progress_logic(str(author_id), 'notesadd_500', 1)

    # Нотификация — вне транзакции
    add_notification(
        str(author_id),
        'research_text_approved',
        '📜 Заметка одобрена!',
        f'Ваша заметка на тему «{theme}» одобрена и добавлена в игру. Благодарим за вклад!'
    )

    return {"id": str(text_id), "author_id": str(author_id), "theme": theme}



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