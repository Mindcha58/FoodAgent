import os

os.environ["LANGCHAIN_TRACING_V2"] = "false"  # ปิดการส่ง Log ไป LangSmith

import logging
import random
import re
import threading
import time

from dotenv import load_dotenv
from flask import Flask, abort, request

# LINE SDK v3
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from crew_meal_planning import build_food_crew
from database import (
    create_user,
    get_user,
    get_user_state,
    init_db,
    init_restaurant_catalog,
    migrate_add_address,
    migrate_add_gender,
    reset_user,
    seed_restaurant_catalog,
    set_user_state,
    update_user_field,
)
from langchain_restaurant_agent import (
    find_restaurants_for_meal,
    prepare_order_for_restaurant,
)
from memory_store import (
    get_recent_meals,
    get_recent_rejections,
    save_feedback_memory,
    save_meal_memory,
)

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Config & Init
# ──────────────────────────────────────────────
load_dotenv("ChatFood.env")

assert os.getenv("LINE_CHANNEL_ACCESS_TOKEN"), "Missing LINE_CHANNEL_ACCESS_TOKEN"
assert os.getenv("LINE_CHANNEL_SECRET"), "Missing LINE_CHANNEL_SECRET"
assert os.getenv("GEMINI_API_KEY"), "Missing GEMINI_API_KEY — สร้างได้ที่ aistudio.google.com/apikey"
assert os.getenv("TAVILY_API_KEY"), "Missing TAVILY_API_KEY — สมัครฟรีได้ที่ app.tavily.com"

app = Flask(__name__)

init_db()
migrate_add_gender()
migrate_add_address()
init_restaurant_catalog()
seed_restaurant_catalog()

# LINE SDK v3 configuration
configuration = Configuration(access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET"))

# ──────────────────────────────────────────────
# Thread-safe global state
# ──────────────────────────────────────────────
_lock = threading.Lock()
latest_suggestion: dict = {}
latest_restaurants: dict = {}
pending_orders: dict = {}


def get_line_api() -> MessagingApi:
    """สร้าง MessagingApi instance ใหม่ต่อ request (thread-safe)"""
    return MessagingApi(ApiClient(configuration))


def reply(reply_token: str, text: str) -> None:
    get_line_api().reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=text)],
        )
    )


def push(user_id: str, text: str) -> None:
    get_line_api().push_message(
        PushMessageRequest(
            to=user_id,
            messages=[TextMessage(text=text)],
        )
    )


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def call_with_retry(func, retries=3):
    for i in range(retries):
        try:
            return func()
        except Exception as e:
            err_str = str(e).lower()
            if "503" in str(e) or "unavailable" in err_str or "resource_exhausted" in err_str:
                delay = (2 ** i) + random.uniform(0, 0.5)
                logger.warning("Retry %d after %.1fs — %s", i + 1, delay, e)
                time.sleep(delay)
            else:
                raise
    raise Exception("AI unavailable after retries")


def extract_meal_name(text: str):
    match = re.search(r"สรุปสุดท้าย:\s*(.*?)\s*\|", text)
    return match.group(1).strip() if match else None


def extract_price(text: str):
    match = re.search(r"ราคา:\s*([0-9]+(?:\.[0-9]+)?)\s*บาท", text)
    return float(match.group(1)) if match else None


def extract_calories(text: str):
    match = re.search(r"แคลอรี่:\s*([0-9]+(?:\.[0-9]+)?)\s*kcal", text)
    return float(match.group(1)) if match else None


def extract_meal_category(meal_name: str) -> str:
    meal_name = meal_name.lower()
    category_map = {
        "ต้มยำ": ["ต้มยำ", "ต้มแซ่บ", "แกงส้ม"],
        "ผัดกะเพรา": ["กะเพรา"],
        "ข้าวมันไก่": ["ข้าวมันไก่"],
        "ก๋วยเตี๋ยว": ["ก๋วยเตี๋ยว", "เส้น", "เย็นตาโฟ", "ราดหน้า", "ผัดซีอิ๊ว"],
        "ของทอด": ["ทอด", "ไก่ทอด", "หมูทอด"],
        "อาหารคลีน": ["คลีน", "อกไก่", "สลัด", "ไข่ต้ม"],
    }
    for category, keywords in category_map.items():
        for keyword in keywords:
            if keyword in meal_name:
                return category
    return "ทั่วไป"


def extract_three_meals(text: str) -> list:
    """ดึงชื่อเมนู 3 มื้อจากผลลัพธ์ของ CrewAI
    รองรับ format: มื้อเช้า: [ชื่อเมนู] | ราคา: ...
    คืนค่าเป็น list ของชื่อเมนู เช่น ["โจ๊กไก่", "กะเพราหมู", "ต้มยำกุ้ง"]
    """
    meals = []
    patterns = [
        r"มื้อเช้า:\s*(.*?)\s*\|",
        r"มื้อกลางวัน:\s*(.*?)\s*\|",
        r"มื้อเย็น:\s*(.*?)\s*\|",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            meals.append(match.group(1).strip())
    return meals


def normalize_food_list(text: str, max_items=5) -> list:
    items = [x.strip() for x in text.split(",") if x.strip()]
    return items[:max_items]


def is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


# ──────────────────────────────────────────────
# Flask routes
# ──────────────────────────────────────────────
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


# ──────────────────────────────────────────────
# Background tasks
# ──────────────────────────────────────────────
def process_ai_and_push(user_id: str, user_msg: str) -> None:
    lower_msg = user_msg.lower()
    is_three_meals = any(
        k in lower_msg for k in ["3 มื้อ", "3 เมนู", "ทั้งวัน", "1 วัน", "สามมื้อ"]
    )

    try:
        profile = get_user(user_id)
        recent_meals = get_recent_meals(user_id, limit=5)
        recent_rejections = get_recent_rejections(user_id, limit=3)

        rejected_names = [x["meal_name"] for x in recent_rejections]
        rejected_categories = list({x["meal_category"] for x in recent_rejections})

        if is_three_meals:
            meal_instruction = """
ผู้ใช้ต้องการเมนูอาหาร 3 มื้อภายใน 1 วัน
กรุณาแนะนำเป็น 3 มื้อ ได้แก่:
- มื้อเช้า
- มื้อกลางวัน
- มื้อเย็น

เงื่อนไข:
- เมนูทั้ง 3 มื้อต้องไม่ซ้ำกัน
- ต้องเหมาะกับงบประมาณของผู้ใช้
- ถ้าผู้ใช้มีเป้าหมายลดน้ำหนัก ให้ช่วยคุมแคลอรี่โดยประมาณของแต่ละมื้อ

รูปแบบผลลัพธ์:
มื้อเช้า: [ชื่อเมนู] | ราคา: [X] บาท | แคลอรี่: [Y] kcal
มื้อกลางวัน: [ชื่อเมนู] | ราคา: [X] บาท | แคลอรี่: [Y] kcal
มื้อเย็น: [ชื่อเมนู] | ราคา: [X] บาท | แคลอรี่: [Y] kcal
งบรวม: [Z] บาท
เหตุผล: [สั้นๆ]
"""
        else:
            meal_instruction = """
ผู้ใช้ต้องการเมนูอาหาร 1 มื้อ

รูปแบบผลลัพธ์:
สรุปสุดท้าย: [ชื่อเมนู] | ราคา: [X] บาท | แคลอรี่: [Y] kcal | เหตุผล: [สั้นๆ]
"""

        user_context = f"""
ข้อความล่าสุดของผู้ใช้: {user_msg}

{meal_instruction}

เพศ: {profile.get('gender')}
อายุ: {profile.get('age')}
น้ำหนัก: {profile.get('weight')}
ส่วนสูง: {profile.get('height')}
งบประมาณต่อมื้อ: {profile.get('budget_per_meal')} บาท
อาหารที่ชอบ: {profile.get('favorite_foods')}
อาหารที่ไม่ชอบ: {profile.get('dislikes')}
อาหารที่แพ้: {profile.get('allergies')}
ที่อยู่หรือบริเวณที่ต้องการหาร้านใกล้เคียง: {profile.get('address')}
เป้าหมาย: {profile.get('goal')}
ชื่อผู้ใช้: {profile.get('name')}
เมนูที่เคยกิน/เคยแนะนำล่าสุด: {', '.join(recent_meals) if recent_meals else 'ยังไม่มี'}
เมนูที่ผู้ใช้เพิ่งปฏิเสธ: {', '.join(rejected_names) if rejected_names else 'ยังไม่มี'}
หมวดหมู่ที่ควรหลีกเลี่ยงจากการปฏิเสธล่าสุด: {', '.join(rejected_categories) if rejected_categories else 'ยังไม่มี'}

ข้อกำหนดสำคัญ:
- ห้ามแนะนำเมนูซ้ำกับรายการล่าสุดถ้ายังมีตัวเลือกอื่น
- ถ้าผู้ใช้เพิ่งปฏิเสธเมนู ให้หลีกเลี่ยงเมนูประเภทเดียวกันในรอบถัดไป
"""

        crew = build_food_crew(user_context)
        result = call_with_retry(lambda: crew.kickoff())
        result_text = str(result)

        meal_name = None
        if is_three_meals:
            # เก็บเมนู 3 มื้อไว้ใน latest_suggestion
            three_meals = extract_three_meals(result_text)
            if three_meals:
                with _lock:
                    latest_suggestion[user_id] = {
                        "is_three_meals": True,
                        "meal_names": three_meals,
                        "result_text": result_text,
                    }
        else:
            meal_name = extract_meal_name(result_text)
            price = extract_price(result_text)

            if meal_name:
                meal_category = extract_meal_category(meal_name)
                with _lock:
                    latest_suggestion[user_id] = {
                        "is_three_meals": False,
                        "meal_name": meal_name,
                        "price": price,
                        "meal_category": meal_category,
                        "result_text": result_text,
                    }
                try:
                    save_meal_memory(
                        user_id=user_id,
                        meal_name=meal_name,
                        meal_detail=result_text,
                        meal_category=meal_category,
                        status="recommended",
                    )
                except Exception as memory_error:
                    logger.warning("Memory save failed: %s", memory_error)

        # การหาร้านจะเกิดขึ้นเมื่อผู้ใช้พิมพ์ "เอาเมนูนี้" เท่านั้น
        if is_three_meals:
            footer_text = (
                "\n\nหากต้องการเลือกเมนูนี้ พิมพ์: เอาเมนูนี้\n"
                "หากต้องการให้จัดใหม่ พิมพ์: เอาใหม่"
            )
        else:
            footer_text = (
                "\n\nหากต้องการเลือกเมนูนี้ พิมพ์: เอาเมนูนี้\n"
                "หากไม่ต้องการ พิมพ์: ไม่เอา / ซ้ำ / แพงไป / เอาใหม่"
            )

        push(user_id, result_text + footer_text)

    except Exception as e:
        logger.error("process_ai_and_push error: %s", e)
        push(user_id, f"ขออภัยค่ะ ระบบขัดข้องชั่วคราว\nรายละเอียด: {str(e)}")


def process_three_meals_restaurant_search(user_id: str, meal_names: list, user_address: str) -> None:
    """หาร้านอาหารสำหรับ 3 มื้อ แยกทีละมื้อ"""
    meal_labels = ["มื้อเช้า", "มื้อกลางวัน", "มื้อเย็น"]
    all_results = []
    
    # 1. กำหนดค่าเริ่มต้นให้ final_message ก่อนเริ่ม Loop
    final_message = "สรุปร้านอาหารสำหรับทั้ง 3 มื้อของคุณค่ะ:\n" + "─" * 20 + "\n"

    for i, meal_name in enumerate(meal_names):
        label = meal_labels[i] if i < len(meal_labels) else f"มื้อที่ {i+1}"
        logger.info("หาร้าน %s: %s ใกล้ %s", label, meal_name, user_address)
        try:
            # ค้นหาร้าน
            result = find_restaurants_for_meal(meal_name, user_address)
            all_results.append(f"🍽️ {label} — {meal_name}\n{result}")
        except Exception as e:
            logger.warning("หาร้าน %s ไม่สำเร็จ: %s", label, e)
            all_results.append(f"🍽️ {label} — {meal_name}\n(ขออภัยค่ะ หาร้านไม่พบในขณะนี้)")

    # 2. รวมผลลัพธ์หลังจากจบ Loop
    if all_results:
        final_message += "\n\n".join(all_results)
    else:
        final_message = "ขออภัยค่ะ ไม่สามารถค้นหาร้านอาหารได้ในขณะนี้"

    # 3. ส่งข้อความ (ตรวจสอบให้แน่ใจว่า push อยู่ระดับเดียวกับ for ไม่ใช่ข้างใน)
    push(user_id, final_message)


def process_restaurant_search_and_push(user_id: str, meal_name: str, user_address: str) -> None:
    try:
        logger.info("MEAL NAME = %s | USER ADDRESS = %s", meal_name, user_address)

        # LangGraph Agent ค้นหาร้านและสรุปผลให้เลย ไม่ต้องแปลงเพิ่ม
        restaurant_message = find_restaurants_for_meal(meal_name, user_address)

        logger.info("RESTAURANT RESULT = %s", restaurant_message)

        if not restaurant_message or not restaurant_message.strip():
            restaurant_message = (
                f"ขออภัยค่ะ ยังไม่พบร้าน{meal_name}ในบริเวณ{user_address}\n"
                f"ลองค้นด้วยตัวเองได้ที่:\n"
                f"• Google Maps: https://maps.google.com/?q=ร้าน{meal_name}+{user_address}"
            )

        # parse ชื่อร้านจากผลลัพธ์ของ LangGraph
        parsed_restaurants = []
        for line in restaurant_message.split("\n"):
            line = line.strip()
            # จับบรรทัดที่ขึ้นต้นด้วยตัวเลข เช่น "1. ร้านอะไรก็ได้"
            match = re.match(r"^\d+\.\s+(.+)$", line)
            if match:
                name = match.group(1).strip()
                if name:
                    parsed_restaurants.append(name)

        with _lock:
            latest_restaurants[user_id] = {
                "meal_name": meal_name,
                "list": parsed_restaurants,
            }

        footer = "\n\nหากต้องการเลือกร้าน พิมพ์: เลือกร้าน 1" if parsed_restaurants else ""

        push(
            user_id,
            (
                f"เมนูที่เลือก: {meal_name}\n\n"
                f"ร้านใกล้เคียงที่แนะนำ:\n{restaurant_message}"
                f"{footer}"
            ),
        )

    except Exception as e:
        logger.error("process_restaurant_search_and_push error: %s", e)
        push(user_id, f"ขออภัยค่ะ ระบบหาร้านอาหารขัดข้องชั่วคราว\nรายละเอียด: {str(e)}")


# ──────────────────────────────────────────────
# Message handler
# ──────────────────────────────────────────────
@handler.add(MessageEvent, message=TextMessageContent)  # ✅ v3
def handle_message(event: MessageEvent) -> None:
    try:
        user_id = event.source.user_id
        user_msg = event.message.text.strip()
        lower_msg = user_msg.lower()

        logger.info("NEW EVENT | user_id=%s | msg=%s", user_id, user_msg)

    except Exception as e:
        logger.error("handle_message parse error: %s", e)
        raise

    # ── รีเซ็ต ──────────────────────────────────
    if lower_msg in ["รีเซ็ต", "เริ่มใหม่", "reset"]:
        reset_user(user_id)
        create_user(user_id)
        set_user_state(user_id, "ask_name")
        reply(event.reply_token, "รีเซ็ตข้อมูลเรียบร้อยแล้วค่ะ 😊\nเริ่มใหม่ ชื่อเล่นของคุณคืออะไรคะ")
        return

    profile = get_user(user_id)

    # ── สมัครใหม่ ────────────────────────────────
    if profile is None:
        create_user(user_id)
        reply(
            event.reply_token,
            "สวัสดีค่ะ 😊 ยินดีต้อนรับสู่ MealMind\nก่อนเริ่มใช้งาน ขอเก็บข้อมูลเบื้องต้นนิดนึงนะคะ\nชื่อเล่นของคุณคืออะไรคะ",
        )
        return

    state = get_user_state(user_id)

    # ── Onboarding states ────────────────────────
    if state == "ask_name":
        update_user_field(user_id, "name", user_msg)
        set_user_state(user_id, "ask_gender")
        reply(event.reply_token, "เพศอะไรคะ (ชาย / หญิง)")
        return

    if state == "ask_gender":
        if user_msg not in ["ชาย", "หญิง"]:
            reply(event.reply_token, "กรุณาพิมพ์เพศเป็น ชาย หรือ หญิง นะคะ")
        else:
            update_user_field(user_id, "gender", user_msg)
            set_user_state(user_id, "ask_age")
            reply(event.reply_token, "อายุเท่าไหร่คะ")
        return

    if state == "ask_age":
        if not user_msg.isdigit():
            reply(event.reply_token, "กรุณากรอกอายุเป็นตัวเลขนะคะ")
        else:
            update_user_field(user_id, "age", int(user_msg))
            set_user_state(user_id, "ask_weight")
            reply(event.reply_token, "น้ำหนักกี่กิโลกรัมคะ")
        return

    if state == "ask_weight":
        if not is_number(user_msg):
            reply(event.reply_token, "กรุณากรอกน้ำหนักเป็นตัวเลขนะคะ เช่น 55 หรือ 55.5")
        else:
            update_user_field(user_id, "weight", float(user_msg))
            set_user_state(user_id, "ask_height")
            reply(event.reply_token, "ส่วนสูงกี่เซนติเมตรคะ")
        return

    if state == "ask_height":
        if not is_number(user_msg):
            reply(event.reply_token, "กรุณากรอกส่วนสูงเป็นตัวเลขนะคะ เช่น 160")
        else:
            update_user_field(user_id, "height", float(user_msg))
            set_user_state(user_id, "ask_goal")
            reply(event.reply_token, "เป้าหมายของคุณคืออะไรคะ เช่น ลดน้ำหนัก เพิ่มกล้าม หรือคุมงบ")
        return

    if state == "ask_goal":
        update_user_field(user_id, "goal", user_msg)
        set_user_state(user_id, "ask_favorite_foods")
        reply(
            event.reply_token,
            "คุณชอบอาหารอะไรบ้างคะ สูงสุด 5 อย่าง\nกรุณาคั่นด้วย comma เช่น ซูชิ, ราเมง, กะเพรา, ข้าวมันไก่, สลัด",
        )
        return

    if state == "ask_favorite_foods":
        foods = normalize_food_list(user_msg, max_items=5)
        if not foods:
            reply(
                event.reply_token,
                "คุณชอบอาหารอะไรบ้างคะ สูงสุด 5 อย่าง\nกรุณาคั่นด้วย comma เช่น ซูชิ, ราเมง, กะเพรา, ข้าวมันไก่, สลัด",
            )
        else:
            update_user_field(user_id, "favorite_foods", ", ".join(foods))
            set_user_state(user_id, "ask_dislikes")
            reply(
                event.reply_token,
                "มีอาหารที่ไม่ชอบอะไรบ้างคะ สูงสุด 5 อย่าง\nกรุณาคั่นด้วย comma เช่น ผักชี, มะเขือ, ของทอด, เผ็ดมาก, ปลาดิบ\nถ้าไม่มี พิมพ์ว่า ไม่มี",
            )
        return

    if state == "ask_dislikes":
        if user_msg == "ไม่มี":
            update_user_field(user_id, "dislikes", "ไม่มี")
        else:
            foods = normalize_food_list(user_msg, max_items=5)
            if not foods:
                reply(
                    event.reply_token,
                    "กรุณากรอกอาหารที่ไม่ชอบสูงสุด 5 อย่าง คั่นด้วย comma\nหรือถ้าไม่มี พิมพ์ว่า ไม่มี",
                )
                return
            update_user_field(user_id, "dislikes", ", ".join(foods))
        set_user_state(user_id, "ask_allergies")
        reply(event.reply_token, "มีอาหารที่แพ้ไหมคะ")
        return

    if state == "ask_allergies":
        update_user_field(user_id, "allergies", user_msg)
        set_user_state(user_id, "ask_address")
        reply(
            event.reply_token,
            "กรุณาระบุที่อยู่หรือบริเวณที่ต้องการให้หาร้านใกล้เคียงค่ะ เช่น รังสิต, ลาดกระบัง, บางนา",
        )
        return

    if state == "ask_address":
        update_user_field(user_id, "address", user_msg)
        set_user_state(user_id, "ask_budget_per_meal")
        reply(event.reply_token, "งบต่อมื้อประมาณกี่บาทคะ")
        return

    if state == "ask_budget_per_meal":
        if not is_number(user_msg):
            reply(event.reply_token, "กรุณากรอกงบต่อมื้อเป็นตัวเลขนะคะ เช่น 50 หรือ 100")
        else:
            update_user_field(user_id, "budget_per_meal", float(user_msg))
            set_user_state(user_id, "ready")
            reply(
                event.reply_token,
                "บันทึกข้อมูลเรียบร้อยแล้วค่ะ 🎉\n"
                "ต่อไปคุณสามารถพิมพ์ความต้องการได้เลย เช่น\n"
                "- งบ 100 บาท กินอะไรดี\n"
                "- วันนี้ไม่อยากกินเผ็ด\n"
                "- อยากได้เมนูอิ่มนาน",
            )
        return

    # ── Ready state ──────────────────────────────
    if state == "ready":

        # ── ทักทายพร้อมชื่อ ─────────────────────────
        if lower_msg in ["สวัสดี", "หวัดดี", "hi", "hello", "สวัสดีค่ะ", "สวัสดีครับ"]:
            name = profile.get("name", "")
            reply(
                event.reply_token,
                f"สวัสดีค่ะ คุณ{name} 😊\n"
                "วันนี้อยากกินอะไรคะ? พิมพ์บอกได้เลยนะคะ\n"
                "เช่น กินอะไรดี, งบ 80 บาท, ไม่อยากกินเผ็ด"
            )
            return

        if lower_msg in ["เอาเมนูนี้", "ตกลง", "โอเค", "เลือกเมนูนี้"]:
            with _lock:
                last = latest_suggestion.get(user_id)

            if not last:
                reply(event.reply_token, "ยังไม่มีเมนูล่าสุดให้ยืนยันค่ะ ลองขอเมนูใหม่ก่อนนะคะ")
                return

            user_address = profile.get("address", "")

            if last.get("is_three_meals"):
                # ✅ กรณี 3 มื้อ — หาร้านครบทุกมื้อ
                meal_names = last.get("meal_names", [])
                reply(event.reply_token, f"กำลังหาร้านสำหรับ {len(meal_names)} มื้อให้อยู่นะคะ 🍽️")
                threading.Thread(
                    target=process_three_meals_restaurant_search,
                    args=(user_id, meal_names, user_address),
                    daemon=True,
                ).start()
            else:
                # กรณี 1 มื้อ — หาร้านเดียว
                meal_name = last.get("meal_name")
                reply(event.reply_token, "กำลังหาร้านใกล้คุณที่มีเมนูนี้ให้อยู่นะคะ 🍽️")
                threading.Thread(
                    target=process_restaurant_search_and_push,
                    args=(user_id, meal_name, user_address),
                    daemon=True,
                ).start()
            return

        if lower_msg in ["ไม่เอา", "เอาใหม่", "ซ้ำ", "แพงไป", "ไม่ตรงโจทย์"]:
            with _lock:
                last = latest_suggestion.get(user_id)

            if last:
                try:
                    save_feedback_memory(
                        user_id=user_id,
                        meal_name=last["meal_name"],
                        reason=user_msg,
                        meal_category=last.get("meal_category", "ทั่วไป"),
                        feedback_type="reject",
                    )
                except Exception as memory_error:
                    logger.warning("Reject memory save failed: %s", memory_error)

            reply(
                event.reply_token,
                "โอเคค่ะ รับ feedback แล้ว กำลังหาเมนูใหม่ที่ไม่ซ้ำแนวเดิมให้นะคะ 🍽️",
            )
            threading.Thread(
                target=process_ai_and_push,
                args=(user_id, "ผู้ใช้ปฏิเสธเมนูล่าสุด กรุณาเสนอเมนูใหม่ที่คนละประเภทและไม่ซ้ำแนวเดิม"),
                daemon=True,
            ).start()
            return

        if lower_msg in ["เปลี่ยนที่อยู่", "แก้ไขที่อยู่"]:
            set_user_state(user_id, "ask_address")
            reply(event.reply_token, "ได้เลยค่ะ กรุณาระบุที่อยู่หรือบริเวณใหม่ที่ต้องการให้หาร้านใกล้เคียง")
            return

        if lower_msg.startswith("เลือกร้าน"):
            with _lock:
                data = latest_restaurants.get(user_id)

            if not data:
                reply(event.reply_token, "ยังไม่มีร้านให้เลือกค่ะ กรุณาเลือกเมนูก่อนนะคะ")
                return

            try:
                index = int(lower_msg.replace("เลือกร้าน", "").strip()) - 1
            except ValueError:
                reply(event.reply_token, "กรุณาพิมพ์ เช่น เลือกร้าน 1")
                return

            restaurant_list = data["list"]
            if index < 0 or index >= len(restaurant_list):
                reply(event.reply_token, "ไม่พบร้านที่เลือกค่ะ")
                return

            restaurant_name = restaurant_list[index]
            meal_name = data["meal_name"]
            order_summary = prepare_order_for_restaurant(restaurant_name, meal_name)

            with _lock:
                pending_orders[user_id] = {
                    "restaurant_name": restaurant_name,
                    "meal_name": meal_name,
                }

            reply(event.reply_token, order_summary + "\n\nพิมพ์: ยืนยันสั่งซื้อ")
            return

        if lower_msg in ["ยืนยันสั่งซื้อ", "confirm order"]:
            with _lock:
                pending = pending_orders.get(user_id)

            if not pending:
                reply(event.reply_token, "ยังไม่มีคำสั่งซื้อที่รอการยืนยันค่ะ")
            else:
                reply(
                    event.reply_token,
                    f"สั่งอาหารแบบจำลองเรียบร้อยแล้วค่ะ ✅\n"
                    f"ร้าน: {pending['restaurant_name']}\n"
                    f"เมนู: {pending['meal_name']}",
                )
            return

        # ── AI suggestion ────────────────────────
        name = profile.get("name", "")
        greeting = f"สวัสดีค่ะ คุณ{name} 😊\n" if name and user_msg in ["สวัสดี", "หวัดดี", "hi", "hello", "สวัสดีค่ะ", "สวัสดีครับ"] else ""
        reply(event.reply_token, f"{greeting}กำลังคิดเมนูให้อยู่นะคะ 🍽️")
        threading.Thread(
            target=process_ai_and_push,
            args=(user_id, user_msg),
            daemon=True,
        ).start()
        return

    # ── Unknown state fallback ───────────────────
    set_user_state(user_id, "ask_name")
    reply(event.reply_token, "เริ่มต้นใหม่ค่ะ ชื่อเล่นของคุณคืออะไรคะ")


if __name__ == "__main__":
    app.run(port=5000)

