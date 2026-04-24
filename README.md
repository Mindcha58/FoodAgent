# MealMind — Agentic AI System for Meal Planning and Daily Food Decision-Making

ระบบ Agentic AI สำหรับแนะนำเมนูอาหารและค้นหาร้านอาหารใกล้เคียงผ่าน LINE Bot โดยใช้ข้อมูลส่วนตัวของผู้ใช้เพื่อให้ได้เมนูที่ตรงใจมากที่สุด

---

## ภาพรวมโปรเจกต์

MealMind เป็น LINE Chatbot ที่ใช้ Agentic AI ในการ:
- **วางแผนเมนูอาหาร** ที่เหมาะกับสุขภาพ งบประมาณ และความชอบของผู้ใช้แต่ละคน
- **ค้นหาร้านอาหารใกล้เคียง** แบบ Real-time พร้อมที่อยู่และลิงก์
- **จดจำประวัติและ Feedback** เพื่อแนะนำเมนูที่ตรงใจมากขึ้นในครั้งถัดไป

---

## Agentic AI ที่ใช้

### 1. CrewAI — วางแผนเมนูอาหาร
จัดทีม Agent 2 ตัวทำงานต่อกันแบบ Sequential โดยมี Gemini เป็นสมอง

| Agent | หน้าที่ |
|---|---|
| นักวางแผนเมนู | คิดเมนูที่เหมาะกับข้อมูลผู้ใช้ |
| ผู้คุมงบประมาณ | ตรวจสอบราคา แคลอรี่ และความถูกต้องของชื่อเมนู |

### 2. LangGraph + Tavily — ค้นหาร้านอาหาร
สร้าง ReAct Agent ที่ปรับ query ได้เองถ้าค้นหาไม่พบ

```
Gemini คิด query → Tavily ค้นเว็บ → ได้ผลหรือยัง?
├── ได้แล้ว → สรุปผลเป็นภาษาไทย
└── ยังไม่พอ → ปรับ query ใหม่ → วนซ้ำ
```

### 3. Gemini 2.5 Flash — LLM หลัก
ขับเคลื่อนทั้ง CrewAI และ LangGraph ทำหน้าที่คิด ตัดสินใจ และสรุปผล

---

## โครงสร้างไฟล์

```
FoodAgent/
├── app.py                        # LINE Bot หลัก รับ-ส่งข้อความ Flask
├── crew_meal_planning.py         # CrewAI Agent คิดเมนูอาหาร
├── langchain_restaurant_agent.py # LangGraph Agent ค้นหาร้าน
├── database.py                   # SQLite จัดการข้อมูลผู้ใช้
├── memory_store.py               # เก็บประวัติเมนูและ Feedback
├── budget_tool.py                # เครื่องมือจัดการงบประมาณ
└── view_memory.py                # ดูประวัติ Memory ของผู้ใช้
```

---

## การติดตั้ง

### 1. Clone repository

```bash
git clone https://github.com/Mindcha58/FoodAgent.git
cd FoodAgent
```

### 2. สร้าง Virtual Environment

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # macOS/Linux
```

### 3. ติดตั้ง Dependencies

```bash
pip install flask
pip install line-bot-sdk>=3.0.0
pip install crewai
pip install langchain-google-genai
pip install langchain-tavily
pip install langgraph
pip install langchain-core
pip install python-dotenv
```

### 4. ตั้งค่า Environment Variables

สร้างไฟล์ `ChatFood.env` ในโฟลเดอร์โปรเจกต์:

```env
LINE_CHANNEL_ACCESS_TOKEN=your_line_channel_access_token
LINE_CHANNEL_SECRET=your_line_channel_secret
GEMINI_API_KEY=your_gemini_api_key
TAVILY_API_KEY=your_tavily_api_key
```

| Key | วิธีได้มา |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | [LINE Developers Console](https://developers.line.biz/) |
| `LINE_CHANNEL_SECRET` | [LINE Developers Console](https://developers.line.biz/) |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) |
| `TAVILY_API_KEY` | [Tavily](https://app.tavily.com) (ฟรี 1,000 req/เดือน) |

### 5. รันแอปพลิเคชัน

```bash
python app.py
```

### 6. ตั้งค่า Webhook ด้วย ngrok

```bash
ngrok http 5000
```

นำ URL ที่ได้ไปตั้งใน LINE Developers Console:
```
https://xxxx.ngrok.io/callback
```

---

## วิธีใช้งาน

### Onboarding (ครั้งแรก)
Bot จะเก็บข้อมูลส่วนตัวทีละขั้นตอน:
```
ชื่อเล่น → เพศ → อายุ → น้ำหนัก → ส่วนสูง
→ เป้าหมาย → อาหารที่ชอบ → อาหารที่ไม่ชอบ
→ อาหารที่แพ้ → ที่อยู่ → งบต่อมื้อ
```

### คำสั่งที่ใช้ได้

| พิมพ์ | ผลลัพธ์ |
|---|---|
| `วันนี้กินอะไรดี` | แนะนำเมนู 1 มื้อ |
| `อยากได้ 3 มื้อ` | วางแผนเมนูทั้งวัน |
| `เอาเมนูนี้` | ค้นหาร้านอาหารใกล้เคียง |
| `ไม่เอา / ซ้ำ / แพงไป` | ขอเมนูใหม่ที่ต่างออกไป |
| `เลือกร้าน 1` | เลือกร้านและสรุปออเดอร์ |
| `เปลี่ยนที่อยู่` | อัปเดตพื้นที่ค้นหาร้าน |
| `รีเซ็ต` | ล้างข้อมูลและเริ่มใหม่ |

---

## Architecture

```
User (LINE)
    ↓
Flask Webhook (app.py)
    ↓
State Machine (Onboarding / Ready)
    ↓
┌─────────────────────────────────┐
│  CrewAI + Gemini                │
│  Agent 1: วางแผนเมนู           │
│  Agent 2: ตรวจสอบงบและแคลอรี่  │
└─────────────────────────────────┘
    ↓ (เมื่อ user พิมพ์ "เอาเมนูนี้")
┌─────────────────────────────────┐
│  LangGraph + Gemini + Tavily    │
│  ReAct Agent ค้นหาร้านอาหาร   │
│  ปรับ query อัตโนมัติถ้าไม่เจอ │
└─────────────────────────────────┘
    ↓
ส่งผลลัพธ์กลับ LINE (push_message)
```

---

## Database Schema

```sql
-- เก็บข้อมูลผู้ใช้
users (user_id, name, gender, age, weight, height,
       goal, allergies, dislikes, favorite_foods,
       address, budget_per_meal)

-- เก็บ State การทำงาน
user_state (user_id, current_step)

-- เก็บงบประมาณคงเหลือ
user_budget (user_id, remaining_budget)

-- Catalog ร้านอาหาร (สำรอง)
restaurant_catalog (id, restaurant_name, menu_name,
                    price, location, order_link)
```

---

## Tech Stack

| ส่วน | เทคโนโลยี |
|---|---|
| LINE Bot | LINE Messaging API SDK v3 |
| Web Server | Flask |
| LLM | Google Gemini 2.5 Flash |
| Multi-Agent | CrewAI |
| Agent Loop | LangGraph |
| Web Search | Tavily Search API |
| Database | SQLite |
| Memory | ChromaDB (Vector Store) |

---

## Requirements

- Python 3.10+
- LINE Messaging API account
- Google AI Studio account (Gemini API Key)
- Tavily account (Search API Key)
- ngrok หรือ Public Server สำหรับ Webhook

---

## ผู้พัฒนา

- Krittin Chairab
- Natcha Chaitavasviboon

โปรเจกต์นี้เป็นส่วนหนึ่งของรายวิชา CPE311 Artificial Intelligence Laboratory
