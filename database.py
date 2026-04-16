import sqlite3

DB_NAME = "users.db"

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    migrate_add_gender()
    migrate_add_address()
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        name TEXT,
        gender TEXT,
        age INTEGER,
        weight REAL,
        height REAL,
        goal TEXT,
        allergies TEXT,
        dislikes TEXT,
        favorite_foods TEXT,
        address TEXT,
        budget_per_meal REAL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_state (
        user_id TEXT PRIMARY KEY,
        current_step TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_budget (
        user_id TEXT PRIMARY KEY,
        remaining_budget REAL DEFAULT 100
    )
    """)

    conn.commit()
    conn.close()

def create_user(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    INSERT OR IGNORE INTO users (user_id)
    VALUES (?)
    """, (user_id,))

    cursor.execute("""
    INSERT OR IGNORE INTO user_state (user_id, current_step)
    VALUES (?, ?)
    """, (user_id, "ask_name"))

    cursor.execute("""
    INSERT OR IGNORE INTO user_budget (user_id, remaining_budget)
    VALUES (?, ?)
    """, (user_id, 100))

    conn.commit()
    conn.close()

def get_user(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()

    conn.close()
    return dict(row) if row else None

def get_user_state(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT current_step FROM user_state WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()

    conn.close()
    return row["current_step"] if row else None

def set_user_state(user_id, step):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    INSERT INTO user_state (user_id, current_step)
    VALUES (?, ?)
    ON CONFLICT(user_id) DO UPDATE SET current_step = excluded.current_step
    """, (user_id, step))

    conn.commit()
    conn.close()

def update_user_field(user_id, field_name, value):
    allowed_fields = {
        "name", "gender" , "age", "weight", "height",
        "goal", "allergies", "dislikes",
        "favorite_foods", "address", "budget_per_meal"
    }

    if field_name not in allowed_fields:
        raise ValueError(f"Invalid field name: {field_name}")

    conn = get_connection()
    cursor = conn.cursor()

    query = f"UPDATE users SET {field_name} = ? WHERE user_id = ?"
    cursor.execute(query, (value, user_id))

    conn.commit()
    conn.close()

def get_budget(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT remaining_budget FROM user_budget WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()

    conn.close()
    return row["remaining_budget"] if row else 100

def migrate_add_gender():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN gender TEXT")
    except:  # noqa: E722
        pass
    conn.commit()
    conn.close()

def reset_user(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM user_state WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM user_budget WHERE user_id = ?", (user_id,))

    conn.commit()
    conn.close()

def migrate_add_address():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN address TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()

def init_restaurant_catalog():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS restaurant_catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        restaurant_name TEXT,
        menu_name TEXT,
        price REAL,
        location TEXT,
        order_link TEXT
    )
    """)

    conn.commit()
    conn.close()

def seed_restaurant_catalog():
    conn = get_connection()
    cursor = conn.cursor()

    # ข้อมูลตัวอย่างร้านอาหาร
    restaurants = [
        ('ร้านข้าวมันไก่เฮียเฮง', 'ข้าวมันไก่ต้ม', 50.0, 'กรุงเทพฯ', 'https://grab.com/shop1'),
        ('ส้มตำป้าต้อย', 'ส้มตำไทย', 45.0, 'นนทบุรี', 'https://lineman.com/shop2'),
        ('ราเมงนะ', 'ทงคัตสึราเมง', 120.0, 'ห้างสรรพสินค้าชั้นนำ', 'https://foodpanda.com/shop3')
    ]

    # ล้างข้อมูลเก่าออกก่อน (ถ้ามี) แล้วใส่ใหม่ เพื่อไม่ให้ข้อมูลซ้ำซ้อน
    cursor.execute("DELETE FROM restaurant_catalog")
    
    cursor.executemany("""
    INSERT INTO restaurant_catalog (restaurant_name, menu_name, price, location, order_link)
    VALUES (?, ?, ?, ?, ?)
    """, restaurants)

    conn.commit()
    conn.close()
    print("Seed restaurant catalog successfully!")