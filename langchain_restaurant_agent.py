import os
from dotenv import load_dotenv

from langchain_tavily import TavilySearch
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent

load_dotenv("ChatFood.env")

# ──────────────────────────────────────────────
# Keys — ใช้ GEMINI_API_KEY เท่านั้น
# ถ้ามี GOOGLE_API_KEY ใน .env ให้ลบออก
# เพราะ LangChain จะใช้ GOOGLE_API_KEY ทับ GEMINI_API_KEY
# ──────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

if not GEMINI_API_KEY:
    raise EnvironmentError("Missing GEMINI_API_KEY")
if not TAVILY_API_KEY:
    raise EnvironmentError("Missing TAVILY_API_KEY — สมัครฟรีได้ที่ app.tavily.com")

# ✅ ป้องกัน LangChain ใช้ GOOGLE_API_KEY ทับ GEMINI_API_KEY
os.environ.pop("GOOGLE_API_KEY", None)
os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
os.environ["TAVILY_API_KEY"] = TAVILY_API_KEY

# ──────────────────────────────────────────────
# ตัดชื่อเมนูให้สั้นลง
# ──────────────────────────────────────────────
MEAL_KEYWORDS = [
    "กะเพรา", "ต้มยำ", "ข้าวมันไก่", "ก๋วยเตี๋ยว", "ผัดไทย",
    "ข้าวผัด", "ราดหน้า", "ผัดซีอิ๊ว", "ต้มข่า", "แกงเขียวหวาน",
    "แกงส้ม", "หมูกระทะ", "ชาบู", "สุกี้", "ข้าวหมูแดง",
    "ข้าวหน้าเป็ด", "บะหมี่", "เย็นตาโฟ", "โจ๊ก", "ข้าวต้ม",
    "ไก่ทอด", "หมูทอด", "ปลาทอด", "ส้มตำ", "ลาบ", "น้ำตก",
    "สลัด", "อกไก่", "สเต็ก", "พิซซ่า", "ราเมง", "ซูชิ",
]

def simplify_meal_name(meal_name: str) -> str:
    for kw in MEAL_KEYWORDS:
        if kw in meal_name:
            return kw
    return meal_name.split()[0] if meal_name.split() else meal_name


# ──────────────────────────────────────────────
# System Prompt
# ──────────────────────────────────────────────
SYSTEM_PROMPT = """คุณเป็น AI ผู้เชี่ยวชาญค้นหาร้านอาหารในประเทศไทย

หน้าที่:
1. ค้นหาร้านอาหารที่มีเมนูที่ต้องการในบริเวณที่ผู้ใช้อยู่
2. ถ้าค้นครั้งแรกไม่พบ ให้ปรับ query ใหม่แล้วค้นอีกครั้ง เช่น เปลี่ยนภาษา หรือค้นกว้างขึ้น
3. สรุปผลเป็นภาษาไทย ชัดเจน อ่านง่าย

กฎ:
- แสดงร้านไม่เกิน 3 ร้าน
- แต่ละร้านต้องมี ชื่อร้าน ที่ตั้ง รายละเอียดสั้นๆ และลิงก์
- ลิงก์ให้ใช้ URL จากผลการค้นหาโดยตรง ถ้าไม่มีให้สร้าง Google Maps URL แทน
- ตอบเป็นภาษาไทยเสมอ

รูปแบบ:
1. [ชื่อร้าน]
   📍 [บริเวณ/ที่อยู่]
   📝 [รายละเอียด]
   🔗 [URL ของร้าน หรือ Google Maps]
"""


# ──────────────────────────────────────────────
# ฟังก์ชันหลัก
# ──────────────────────────────────────────────
def find_restaurants_for_meal(meal_name: str, address: str) -> str:
    if not address or address.strip() in ["", "ไม่ระบุ", "None", "none"]:
        address = "กรุงเทพ"

    short_name = simplify_meal_name(meal_name)
    print(f"[Restaurant Agent] ค้นหา: {short_name} ใกล้ {address}")

    try:
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            google_api_key=GEMINI_API_KEY,
            temperature=0.2,
        )

        tavily_tool = TavilySearch(max_results=5)

        # ✅ ใช้ prompt แทน state_modifier (รองรับทุกเวอร์ชัน LangGraph)
        agent = create_react_agent(
            model=llm,
            tools=[tavily_tool],
            prompt=SYSTEM_PROMPT,
        )

        user_request = (
            f"หาร้านอาหารที่มีเมนู '{short_name}' "
            f"ในบริเวณ '{address}' ประเทศไทย "
            f"ต้องการชื่อร้าน ที่อยู่ รายละเอียด และลิงก์เว็บไซต์หรือ Google Maps ของร้านด้วย"
        )

        result = agent.invoke({
            "messages": [HumanMessage(content=user_request)]
        })

        messages = result.get("messages", [])
        if not messages:
            return _fallback_message(short_name, address)

        # ✅ รองรับกรณี Gemini คืน content เป็น list หรือ string
        last_content = messages[-1].content
        if isinstance(last_content, list):
            # content เป็น list of parts → รวมทุก text part เข้าด้วยกัน
            output = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in last_content
            ).strip()
        else:
            output = str(last_content).strip()

        if not output:
            return _fallback_message(short_name, address)

        return output

    except TypeError as e:
        # ถ้า LangGraph เวอร์ชันเก่ามาก ให้ fallback ใช้ Tavily ตรงๆ
        print(f"[Restaurant Agent] LangGraph error: {e} → fallback to direct Tavily")
        return _search_with_tavily_direct(short_name, address)

    except Exception as e:
        print(f"[Restaurant Agent] Error: {e}")
        return _fallback_message(short_name, address)


def _search_with_tavily_direct(meal_name: str, address: str) -> str:
    """Fallback: ค้น Tavily ตรงๆ โดยไม่ผ่าน Agent ถ้า LangGraph มีปัญหา"""
    try:
        tool = TavilySearch(max_results=5)

        queries = [
            f"ร้าน{meal_name} {address} อร่อย แนะนำ",
            f"{meal_name} restaurant {address} Thailand",
            f"ร้านอาหาร {address} {meal_name}",
        ]

        for query in queries:
            print(f"[Tavily Direct] query: {query}")
            results = tool.invoke(query)

            if results and isinstance(results, list) and len(results) > 0:
                output = []
                for i, r in enumerate(results[:3], start=1):
                    title = r.get("title", "ไม่ระบุชื่อ")
                    content = r.get("content", "")[:150]
                    url = r.get("url", "-")
                    output.append(
                        f"{i}. {title}\n"
                        f"   📝 {content}\n"
                        f"   🔗 {url}"
                    )
                if output:
                    return "\n\n".join(output)

        return _fallback_message(meal_name, address)

    except Exception as e:
        print(f"[Tavily Direct] Error: {e}")
        return _fallback_message(meal_name, address)


def _fallback_message(meal_name: str, address: str) -> str:
    return (
        f"ขออภัยค่ะ ยังไม่พบร้าน{meal_name}ในบริเวณ{address}\n"
        f"ลองค้นด้วยตัวเองได้ที่:\n"
        f"• Wongnai: https://www.wongnai.com/search?q={meal_name}\n"
        f"• Google Maps: https://maps.google.com/?q=ร้าน{meal_name}+{address}"
    )


# ──────────────────────────────────────────────
# ฟังก์ชัน Order
# ──────────────────────────────────────────────
def prepare_order_summary(query: str) -> str:
    try:
        restaurant_name = ""
        meal_name = ""

        for part in query.split("|"):
            part = part.strip()
            if part.startswith("ร้าน:"):
                restaurant_name = part.replace("ร้าน:", "").strip()
            elif part.startswith("เมนู:"):
                meal_name = part.replace("เมนู:", "").strip()

        if not restaurant_name or not meal_name:
            return "ข้อมูลไม่ครบสำหรับสรุปออเดอร์"

        return (
            f"เตรียมคำสั่งซื้อเรียบร้อยแล้ว ✅\n"
            f"ร้าน: {restaurant_name}\n"
            f"เมนู: {meal_name}\n"
            f"สถานะ: รอผู้ใช้ยืนยันสั่งซื้อ"
        )

    except Exception as e:
        return f"เกิดข้อผิดพลาดในการเตรียมออเดอร์: {str(e)}"


def prepare_order_for_restaurant(restaurant_name: str, meal_name: str) -> str:
    return prepare_order_summary(f"ร้าน: {restaurant_name} | เมนู: {meal_name}")