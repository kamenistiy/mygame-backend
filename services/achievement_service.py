from services.notification_service import add_notification
from services.player_service import required_exp
from core.db import get_db

#Достижение с аватарами 1,5,10   
def grant_achievement_if_not_obtained(user_id: str, achievement_id: str):
    print(f"🎯 START: {achievement_id} для {user_id}")

    with get_db() as conn:
        with conn.cursor() as cur:

            # Проверяем — уже есть достижение?
            cur.execute("""
                SELECT 1
                FROM user_achievements
                WHERE user_id = %s AND achievement_id = %s
            """, (user_id, achievement_id))

            existing = cur.fetchone()

            if existing:
                print("  ℹ️ Достижение уже получено")
                return False

            # Получаем награды
            cur.execute("""
                SELECT name, exp_reward, coins_reward
                FROM achievements
                WHERE id = %s
            """, (achievement_id,))

            reward = cur.fetchone()

            if not reward:
                print(f"  ❌ Достижение {achievement_id} не найдено")
                return False

            # Выдаём достижение
            cur.execute("""
                INSERT INTO user_achievements
                (user_id, achievement_id, current_progress, is_unlocked, unlocked_at)
                VALUES (%s, %s, 1, true, NOW())
            """, (user_id, achievement_id))

            # Награды
            cur.execute("""
                UPDATE players
                SET coins = coins + %s
                WHERE id = %s
            """, (reward['coins_reward'], user_id))

            conn.commit()

            add_notification(
                user_id,
                'achievement',
                'Благодарность',
                'Вы получили достижение "Благодарность" за участие в альфа-тесте.'
            )

            print("  ✅ Достижение выдано")
            return True
