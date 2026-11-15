import random
from core.db import get_db
from services.player_service import required_exp
from services.inventory_service import add_item_to_inventory

def calculate_attack(attacker, defender):
    """Возвращает (hit: bool, damage: int)"""
    # 1. Проверка на попадание
    acc = max(1, attacker.get('acc', 0))
    ddg = max(0, defender.get('ddg', 0))
    hit_chance = (acc / (acc + ddg)) * 100
    
    if random.uniform(0, 100) > hit_chance:
        return False, 0
        
    # 2. Расчет урона (Физ или Маг)
    if attacker.get('attack_type') == 'magic':
        dmg = attacker.get('mat', 0) - defender.get('mdf', 0)
    else:
        dmg = attacker.get('pat', 0) - defender.get('pdf', 0)
        
    return True, max(1, int(dmg))

def get_combat_state(cur, user_id):
    """Достает данные игрока и текущего моба."""
    cur.execute("""
        SELECT ps.current_hp, ps.current_mana, ps.pat, ps.mat, ps.pdf, ps.mdf, ps.ddg, ps.acc,
               ps.active_enemy_id, ps.enemy_current_hp, p.level
        FROM player_stats ps
        JOIN players p ON p.id = ps.user_id
        WHERE ps.user_id = %s
    """, (user_id,))
    p_stats = cur.fetchone()
    
    if not p_stats or not p_stats['active_enemy_id']:
        return None, None
        
    cur.execute("SELECT * FROM enemies WHERE id = %s", (p_stats['active_enemy_id'],))
    enemy = cur.fetchone()
    
    return p_stats, enemy

def process_combat_round(user_id, action_type):
    """Обрабатывает один ход (Игрок -> Моб)"""
    with get_db() as conn:
        with conn.cursor() as cur:
            p_stats, enemy = get_combat_state(cur, user_id)
            
            if not p_stats or not enemy:
                raise ValueError("Бой не найден или уже завершен")

            logs = []
            p_hp = p_stats['current_hp']
            e_hp = p_stats['enemy_current_hp']
            
            # --- ХОД ИГРОКА ---
            if action_type == 'attack':
                player_attacker = {
                    'attack_type': 'physical',
                    'pat': p_stats['pat'], 'mat': p_stats['mat'],
                    'acc': p_stats['acc']
                }
                hit, dmg = calculate_attack(player_attacker, enemy)
                if hit:
                    e_hp -= dmg
                    logs.append({"text": "player_hit", "dmg": dmg, "is_player": True})
                else:
                    logs.append({"text": "player_miss", "is_player": True})
            
            # Проверяем, жив ли моб
            if e_hp <= 0:
                cur.execute("UPDATE player_stats SET enemy_current_hp = 0 WHERE user_id = %s", (user_id,))
                conn.commit()
                return finish_combat(cur, conn, user_id, enemy, p_stats, logs, victory=True)
            
            # --- ХОД МОБА ---
            # Моб всегда атакует
            enemy_attacker = dict(enemy)
            player_defender = {
                'pdf': p_stats['pdf'], 'mdf': p_stats['mdf'], 'ddg': p_stats['ddg']
            }
            hit, dmg = calculate_attack(enemy_attacker, player_defender)
            if hit:
                p_hp -= dmg
                logs.append({"text": "enemy_hit", "dmg": dmg, "is_player": False})
            else:
                logs.append({"text": "enemy_miss", "is_player": False})
                
            # Проверяем, жив ли игрок
            if p_hp <= 0:
                cur.execute("UPDATE player_stats SET current_hp = 1, current_mana = 0 WHERE user_id = %s", (user_id,))
                conn.commit()
                return finish_combat(cur, conn, user_id, enemy, p_stats, logs, victory=False)
                
            # Сохраняем промежуточный результат
            cur.execute("""
                UPDATE player_stats 
                SET current_hp = %s, enemy_current_hp = %s 
                WHERE user_id = %s
            """, (p_hp, e_hp, user_id))
            conn.commit()
            
            return {
                "victory": None,
                "logs": logs,
                "player_hp": p_hp,
                "enemy_hp": e_hp,
                "enemy_max_hp": enemy['max_hp']
            }

def finish_combat(cur, conn, user_id, enemy, p_stats, logs, victory):
    """Завершает бой, выдает награды или штрафы."""
    cur.execute("UPDATE player_stats SET active_enemy_id = NULL, enemy_current_hp = NULL WHERE user_id = %s", (user_id,))
    
    rewards = []
    level_up_data = None
    
    if victory:
        # Даем опыт
        xp = enemy['exp_reward']
        cur.execute("SELECT level, exp FROM players WHERE id = %s", (user_id,))
        p = cur.fetchone()
        new_exp = p['exp'] + xp
        new_level = p['level']
        
        while new_exp >= required_exp(new_level):
            new_exp -= required_exp(new_level)
            new_level += 1
            
        if new_level > p['level']:
            # Логика левелапа (упрощенно, как в research)
            cur.execute("UPDATE player_stats SET free_stat_points = free_stat_points + %s WHERE user_id = %s", 
                        ((new_level - p['level']) * 2, user_id))
            level_up_data = {"from_level": p['level'], "to_level": new_level}
            
        cur.execute("UPDATE players SET exp = %s, level = %s WHERE id = %s", (new_exp, new_level, user_id))
        logs.append({"text": "victory", "xp": xp})
        
        # Ролл лута
        cur.execute("SELECT item_id, drop_chance, min_qty, max_qty FROM enemy_loot WHERE enemy_id = %s", (enemy['id'],))
        loots = cur.fetchall()
        
        for loot in loots:
            if random.randint(1, 100) <= loot['drop_chance']:
                qty = random.randint(loot['min_qty'], loot['max_qty'])
                add_item_to_inventory(user_id, loot['item_id'], qty)
                # Достаем инфу о предмете для UI
                cur.execute("SELECT name, name_en, icon FROM items WHERE id = %s", (loot['item_id'],))
                item_info = cur.fetchone()
                if item_info:
                    rewards.append({
                        "id": loot['item_id'],
                        "name": item_info['name'],
                        "name_en": item_info['name_en'],
                        "icon": item_info['icon'],
                        "quantity": qty
                    })
    else:
        logs.append({"text": "defeat"})
        
    conn.commit()
    
    return {
        "victory": victory,
        "logs": logs,
        "player_hp": p_stats['current_hp'], # Возвращаем то, что было (1)
        "enemy_hp": 0,
        "rewards": rewards,
        "level_up": level_up_data
    }

def roll_enemy_for_region(cur, region_id):
    cur.execute("SELECT * FROM enemies WHERE region_id = %s ORDER BY RANDOM() LIMIT 1", (region_id,))
    return cur.fetchone()