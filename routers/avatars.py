from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional
from uuid import UUID
import uuid

from core.db import get_db
from core.config import SUPABASE_URL, SUPABASE_SERVICE_KEY
from supabase import create_client


supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

router = APIRouter()

# отмена загрузки аватара
class CancelRequest(BaseModel):
    request_id: str
    user_id: str

# ========== МОДЕЛь ответа на заявку о смене аватара ==========
class AvatarReviewRequest(BaseModel):
    request_id: str
    action: str
    reason: Optional[str] = None 

@router.post("/avatar/upload")
async def upload_avatar(
    user_id: str = Form(...),
    request_id: str = Form(...),
    file: UploadFile = File(...)
):
    # 1. Проверка заявки
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT status FROM avatar_requests WHERE id = %s AND user_id = %s", (request_id, user_id))
    req = cur.fetchone()
    if not req or req['status'] != 'pending':
        cur.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Заявка не найдена или уже обработана")

    # 2. Проверки файла
    if file.size > 80 * 1024:
        raise HTTPException(status_code=400, detail="Файл превышает 80 KB")
    if file.content_type not in ['image/png', 'image/webp', 'image/gif']:
        raise HTTPException(status_code=400, detail="Разрешены только PNG, WebP, GIF")

    # 3. Читаем содержимое файла (ОДИН РАЗ!)
    contents = await file.read()
    if len(contents) > 80 * 1024:
        raise HTTPException(status_code=400, detail="Файл превышает 80 KB")
    original_filename = file.filename

        # 4. Проверка размеров через Pillow
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(contents))
        width, height = img.size
        if width != height:
            raise HTTPException(status_code=400, detail="Изображение должно быть квадратным (1:1)")
        if width != 150 or height != 150:
            raise HTTPException(status_code=400, detail="Аватар должен быть строго 150×150 пикселей")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Не удалось прочитать изображение: {e}")

    # 5. Генерация пути
    ext = file.filename.split('.')[-1].lower()
    file_name = f"{user_id}_{uuid.uuid4()}.{ext}"
    file_path = f"pending/{file_name}"

    # 6. Загрузка в Storage
    try:
        res = supabase.storage.from_("avatars").upload(file_path, contents)
        if hasattr(res, 'error') and res.error:
            raise Exception(str(res.error))
    except Exception as e:
        cur.close()
        conn.close()
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки в хранилище: {e}")

    # 7. Обновление заявки
    cur.execute("UPDATE avatar_requests SET storage_path = %s, original_filename = %s WHERE id = %s",
            (file_path, original_filename, request_id))
    conn.commit()
    cur.close()
    conn.close()

    return {"success": True}

#Отображение Аватаров пользователей в их профиле
@router.get("/user-avatar/{user_id}")
def get_user_avatar(user_id: str):
    conn = get_db()
    cur = conn.cursor()
    # Ищем активный аватар (is_active = true)
    cur.execute("""
        SELECT storage_path FROM user_avatars
        WHERE user_id = %s AND is_active = true
        LIMIT 1
    """, (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row:
        # Формируем публичную ссылку на файл в Storage
        public_url = supabase.storage.from_("avatars").get_public_url(row['storage_path'])
        return {"avatar_url": public_url}
    else:
        return {"avatar_url": None}
    
    
@router.get("/my-avatar-requests")
def get_my_requests(user_id: str):
    try:
        UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, original_filename, status, reason, created_at
        FROM avatar_requests
        WHERE user_id = %s
        ORDER BY created_at DESC
    """, (user_id,))
    requests = cur.fetchall()
    cur.close()
    conn.close()
    return {"requests": requests}

@router.post("/avatar-request/cancel")
def cancel_avatar_request(req: CancelRequest):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT status FROM avatar_requests WHERE id = %s AND user_id = %s", (req.request_id, req.user_id))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Заявка не найдена")
        if row['status'] != 'pending':
            raise HTTPException(status_code=400, detail="Заявка уже обработана")

        cur.execute("DELETE FROM avatar_requests WHERE id = %s", (req.request_id,))
        cur.execute("""
            INSERT INTO inventory (user_id, item_id, quantity)
            VALUES (%s, 'avatar_certificate', 1)
            ON CONFLICT (user_id, item_id)
            DO UPDATE SET quantity = inventory.quantity + 1
        """, (req.user_id,))
        conn.commit()
        return {"success": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@router.get("/admin/avatar-requests")
def get_avatar_requests(user_id: str):
    if not is_admin(user_id):
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT ar.id, ar.user_id, p.username, ar.original_filename, ar.status, ar.created_at, ar.storage_path, ar.reason
        FROM avatar_requests ar
        JOIN players p ON ar.user_id = p.id
        ORDER BY ar.created_at DESC
    """)
    requests = cur.fetchall()
    cur.close()
    conn.close()
    return {"requests": requests}

#   Эндпоинт – обработка заявки (одобрить/отклонить):
@router.post("/admin/avatar-review")
def review_avatar(req: AvatarReviewRequest, admin_user_id: str):
    # if not is_admin(admin_user_id):
    #     raise HTTPException(status_code=403, detail="Доступ запрещён")
    print(f"✅ review_avatar вызван: request_id={req.request_id}, action={req.action}, admin={admin_user_id}")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SET statement_timeout = 0")

    # Получаем заявку
    cur.execute("SELECT user_id, storage_path, status, original_filename FROM avatar_requests WHERE id = %s", (req.request_id,))
    request = cur.fetchone()
    if not request:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    if request['status'] != 'pending':
        raise HTTPException(status_code=400, detail="Заявка уже обработана")
    
    user_id = request['user_id']
    storage_path = request['storage_path']
    
    if req.action == 'approve':
        print("=== APPROVE START ===")
        new_path = storage_path
        
        # Перемещение файла из pending в approved (если файл существует)
        if storage_path and storage_path.startswith('pending/'):
            try:
                supabase.storage.from_("avatars").download(storage_path)
                new_path = storage_path.replace('pending/', 'approved/')
                file_data = supabase.storage.from_("avatars").download(storage_path)
                supabase.storage.from_("avatars").upload(new_path, file_data)
                supabase.storage.from_("avatars").remove([storage_path])
                storage_path = new_path
                print(f"Файл перемещён в {new_path}")
            except Exception as e:
                print(f"Не удалось переместить файл {storage_path}: {e}")

        # 1. Добавляем аватар в библиотеку
        cur.execute("""
    INSERT INTO user_avatars (user_id, storage_path, is_active, username)
    VALUES (%s, %s, %s, (SELECT username FROM players WHERE id = %s))
    RETURNING id
""", (user_id, new_path, False, user_id))
        avatar_id = cur.fetchone()['id']
        cur.execute("SELECT id FROM user_avatars WHERE user_id = %s AND is_active = true", (user_id,))
        active = cur.fetchone()
        if not active:
            cur.execute("UPDATE user_avatars SET is_active = true WHERE id = %s", (avatar_id,))
        
        # 2. Обновляем статус заявки
        cur.execute("UPDATE avatar_requests SET status = 'approved', reviewed_at = NOW() WHERE id = %s", (req.request_id,))
        
        # 3. Увеличиваем счётчик одобренных аватаров
        cur.execute("UPDATE players SET approved_avatars_count = approved_avatars_count + 1 WHERE id = %s RETURNING approved_avatars_count", (user_id,))
        new_count = cur.fetchone()['approved_avatars_count']
        
        # 4. Фиксируем изменения
        conn.commit()
        print("=== APPROVE DONE (commit) ===")

        # Выдаём достижения и уведомления
        grant_achievement_if_not_obtained(user_id, 'avatar_lover')
        if new_count >= 5:
            grant_achievement_if_not_obtained(user_id, 'avatar_lover_5')
        if new_count >= 10:
            grant_achievement_if_not_obtained(user_id, 'avatar_lover_10')

        orig_filename = request.get('original_filename', 'неизвестный файл')
        add_notification(user_id, 'system', 'Аватар одобрен',
                         f'Ваша заявка на файл "{orig_filename}" одобрена. Аватар добавлен в библиотеку профиля.')

        return {"success": True}

    elif req.action == 'reject':
        print("=== REJECT START ===")

        # Удаляем файл из Storage, если он есть
        if storage_path:
            try:
                supabase.storage.from_("avatars").remove([storage_path])
                print(f"Файл {storage_path} удалён")
            except Exception as e:
                print(f"Не удалось удалить файл (пропускаем): {e}")
        else:
            print("Файл отсутствует, удаление пропущено")

        # Обновляем статус заявки
        cur.execute("""
            UPDATE avatar_requests
            SET status = 'rejected', reason = %s, reviewed_at = NOW()
            WHERE id = %s
        """, (req.reason, req.request_id))
        print(f"Обновлено строк: {cur.rowcount}")

        if cur.rowcount == 0:
            print("Заявка не найдена или уже обработана")
            conn.rollback()
            raise HTTPException(status_code=400, detail="Заявка не найдена или уже обработана")

        # Возвращаем фолиант
        cur.execute("""
            INSERT INTO inventory (user_id, item_id, quantity)
            VALUES (%s, 'avatar_certificate', 1)
            ON CONFLICT (user_id, item_id)
            DO UPDATE SET quantity = inventory.quantity + 1
        """, (user_id,))
        print("Фолиант возвращён")

        conn.commit()
        print("=== REJECT DONE (commit) ===")
        # Отправляем уведомление игроку об отказе
        orig_filename = request.get('original_filename', 'неизвестный файл')
        reason_text = f" Причина: {req.reason}" if req.reason else ""
        add_notification(user_id, 'system', 'Аватар отклонён',
                         f'Ваша заявка на файл "{orig_filename}" отклонена.{reason_text} Фолиант возвращён в инвентарь.')
        return {"success": True}

    else:
        raise HTTPException(status_code=400, detail="Неверное действие")