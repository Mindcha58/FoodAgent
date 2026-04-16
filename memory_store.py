import time
import chromadb

client = chromadb.PersistentClient(path="./chroma_db")

meal_memory = client.get_or_create_collection(name="meal_history")
feedback_memory = client.get_or_create_collection(name="meal_feedback")


def save_meal_memory(user_id, meal_name, meal_detail, meal_category="ทั่วไป", status="recommended"):
    doc_id = f"{user_id}_{meal_name}_{status}_{int(time.time())}"

    meal_memory.upsert(
        ids=[doc_id],
        documents=[meal_detail],
        metadatas=[{
            "user_id": user_id,
            "meal_name": meal_name,
            "meal_category": meal_category,
            "status": status,
            "timestamp": int(time.time())
        }]
    )


def save_feedback_memory(user_id, meal_name, reason, meal_category="ทั่วไป", feedback_type="reject"):
    doc_id = f"{user_id}_{meal_name}_{feedback_type}_{int(time.time())}"

    feedback_memory.upsert(
        ids=[doc_id],
        documents=[f"{meal_name} | reason: {reason}"],
        metadatas=[{
            "user_id": user_id,
            "meal_name": meal_name,
            "meal_category": meal_category,
            "reason": reason,
            "feedback_type": feedback_type,
            "timestamp": int(time.time())
        }]
    )


def get_recent_meals(user_id, limit=5):
    results = meal_memory.get(where={"user_id": user_id})

    items = []
    if results and results.get("metadatas"):
        metas = results["metadatas"]
        metas = sorted(metas, key=lambda x: x.get("timestamp", 0))
        for meta in metas[-limit:]:
            items.append(meta["meal_name"])

    return items


def get_recent_rejections(user_id, limit=3):
    results = feedback_memory.get(
        where={
            "$and": [
                {"user_id": user_id},
                {"feedback_type": "reject"}
            ]
        }
    )

    items = []
    if results and results.get("metadatas"):
        metas = results["metadatas"]
        metas = sorted(metas, key=lambda x: x.get("timestamp", 0))
        for meta in metas[-limit:]:
            items.append({
                "meal_name": meta["meal_name"],
                "meal_category": meta.get("meal_category", "ทั่วไป"),
                "reason": meta.get("reason", "")
            })

    return items