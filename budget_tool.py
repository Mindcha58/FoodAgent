from database import get_connection, get_budget

def update_budget(user_id, spent_amount):
    current_budget = get_budget(user_id)

    if current_budget >= spent_amount:
        new_budget = current_budget - spent_amount

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE user_budget
        SET remaining_budget = ?
        WHERE user_id = ?
        """, (new_budget, user_id))
        conn.commit()
        conn.close()

        return True, new_budget

    return False, current_budget