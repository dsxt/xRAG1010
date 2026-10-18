from pymongo import MongoClient
from datetime import datetime

client = MongoClient("mongodb://localhost:27017/")
db = client["kb001"]
collection = db["chat_message"]

document = {
    "session_id": "test_session_001",
    "role": "user",
    "text": "你好，有烫金机吗？",
    "rewritten_query": "",
    "item_names": [],
    "ts": datetime.now().timestamp()
}

result = collection.insert_one(document)
print("插入的 _id：", result.inserted_id)