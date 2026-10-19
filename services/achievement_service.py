from services.notification_service import add_notification
from services.player_service import required_exp
from services.player_service import add_exp_and_coins
from core.db import get_db

  
def grant_achievement_if_not_obtained(user_id: str, achievement_id: str):
    print(f"🎯 START: {achievement_id} для {user_id}")

    with get_db() as conn:
        with conn.cursor() as cur:
            # Проверяем, существует ли запись и получена ли она
            cur.execute("""
                SELECT is_unlocked
                FROM user_achievements
                WHERE user_id = %s AND achievement_id = %s
            """, (user_id, achievement_id))
            existing = cur.fetchone()

            # Если запись есть И она уже разблокирована — ничего не делаем
            if existing and existing['is_unlocked']:
                print("ℹ️ Достижение уже получено ранее")
                cur.execute("""
                    SELECT 1 FROM notifications
                    WHERE user_id = %s AND type = 'achievement' AND title = (
                        SELECT name FROM achievements WHERE id = %s
                    )
                """, (user_id, achievement_id))
                
                if not cur.fetchone():
                    cur.execute("SELECT name FROM achievements WHERE id = %s", (achievement_id,))
                    ach = cur.fetchone()
                    if ach:
                        add_notification(
                            user_id, 'achievement', ach['name'],
                            f'Вы получили достижение "{ach["name"]}".'
                        )
                return False

            # Загружаем награды
            cur.execute("""
                SELECT name, exp_reward, coins_reward, COALESCE(gems_reward, 0) AS gems_reward
                FROM achievements WHERE id = %s
            """, (achievement_id,))
            reward = cur.fetchone()

            if not reward:
                print(f"❌ Достижение {achievement_id} не найдено")
                return False

            # Если запись есть, но is_unlocked = false — обновляем её
            if existing:
                cur.execute("""
                    UPDATE user_achievements
                    SET current_progress = 1, is_unlocked = true, unlocked_at = NOW()
                    WHERE user_id = %s AND achievement_id = %s
                """, (user_id, achievement_id))
            else:
                # Если записи вообще нет (например, выдаем ачивку напрямую) — создаем
                cur.execute("""
                    INSERT INTO user_achievements
                    (user_id, achievement_id, current_progress, is_unlocked, unlocked_at)
                    VALUES (%s, %s, 1, true, NOW())
                """, (user_id, achievement_id))

            conn.commit()
            print("ADDING EXP/COINS")

            # Выдаем опыт и монеты
            add_exp_and_coins(
                user_id,
                exp_to_add=reward['exp_reward'],
                coins_to_add=reward['coins_reward']
            )

            # Выдаем гемы
            if reward['gems_reward'] > 0:
                cur.execute("""
                    UPDATE players 
                    SET pathgems = COALESCE(pathgems, 0) + %s 
                    WHERE id = %s
                """, (reward['gems_reward'], user_id))
                conn.commit()

            print("ADDING NOTIFICATION")
            add_notification(
                user_id,
                'achievement',
                reward['name'],
                f'Вы получили достижение "{reward["name"]}".'
            )

            print("✅ DONE")
            return True

def update_achievement_progress_logic(user_id: str, achievement_id: str, increment: int = 1):
    """Увеличивает прогресс достижения. Если достижение выполнено – выдаёт награду."""
    should_grant = False  # Флаг, что нужно выдать награду после закрытия транзакции
    
    with get_db() as conn:
        with conn.cursor() as cur:
            # Получаем max_progress и текущую запись
            cur.execute("SELECT max_progress FROM achievements WHERE id = %s", (achievement_id,))
            max_prog_row = cur.fetchone()
            if not max_prog_row:
                return
            max_progress = max_prog_row['max_progress']

            cur.execute("""
                SELECT current_progress, is_unlocked
                FROM user_achievements
                WHERE user_id = %s AND achievement_id = %s
            """, (user_id, achievement_id))
            row = cur.fetchone()

            if not row:
                # Нет записи – создаём
                cur.execute("""
                    INSERT INTO user_achievements (user_id, achievement_id, current_progress, is_unlocked)
                    VALUES (%s, %s, 0, false)
                """, (user_id, achievement_id))
                current_progress = 0
                is_unlocked = False
            else:
                current_progress = row['current_progress']
                is_unlocked = row['is_unlocked']

            if is_unlocked:
                return  # уже получено – ничего не делаем

            new_progress = min(current_progress + increment, max_progress)
            cur.execute("""
                UPDATE user_achievements
                SET current_progress = %s
                WHERE user_id = %s AND achievement_id = %s
            """, (new_progress, user_id, achievement_id))
            
            # ⚠️ ВАЖНО: Сохраняем прогресс и снимаем блокировку БД ДО выдачи наград!
            conn.commit() 

            if new_progress >= max_progress:
                should_grant = True
    
    # Вызываем вне транзакции (когда соединение уже закрыто), чтобы не было дедлока
    if should_grant:
        grant_achievement_if_not_obtained(user_id, achievement_id)