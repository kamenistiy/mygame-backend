from services.notification_service import add_notification
from services.player_service import required_exp
from core.db import get_db

#Достижение с аватарами 1,5,10   
def grant_achievement_if_not_obtained(user_id: str, achievement_id: str):
    print(f"🎯 START: {achievement_id} для {user_id}")

    with get_db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT name, exp_reward, coins_reward
                FROM achievements
                WHERE id = %s
            """, (achievement_id,))

            reward = cur.fetchone()

            if not reward:
                print(f"❌ Достижение {achievement_id} не найдено")
                return False

            try:
                cur.execute("""
                    INSERT INTO user_achievements
                    (user_id, achievement_id, current_progress, is_unlocked, unlocked_at)
                    VALUES (%s, %s, 1, true, NOW())
                """, (user_id, achievement_id))

            except Exception as e:
                print(f"⚠️ Ошибка вставки: {e}")
                conn.commit()
                return False

            # Золото
            cur.execute("""
                UPDATE players
                SET coins = coins + %s
                WHERE id = %s
            """, (reward['coins_reward'], user_id))

            # Опыт / уровень
            cur.execute("""
                SELECT exp, level
                FROM players
                WHERE id = %s
            """, (user_id,))

            player = cur.fetchone()

            exp = player['exp'] + reward['exp_reward']
            level = player['level']

            new_level = level
            exp_rem = exp

            while exp_rem >= required_exp(new_level):
                exp_rem -= required_exp(new_level)
                new_level += 1

            cur.execute("""
                UPDATE players
                SET exp = %s, level = %s
                WHERE id = %s
            """, (exp_rem, new_level, user_id))

            conn.commit()

            # ===== ТЕКСТ УВЕДОМЛЕНИЯ =====

            notification_text = (
                f'Награда: +{reward["exp_reward"]} опыта, '
                f'+{reward["coins_reward"]} монет.'
            )

            # Специальный текст для alpha_tester
            if achievement_id == "alpha_tester":
                notification_text += (
                    '\n\nВы получили достижение "Благодарность" '
                    'за участие в альфа-тесте.'
                )

            add_notification(
                user_id,
                'achievement',
                f'Достижение "{reward["name"]}" получено!',
                notification_text
            )

            return True
