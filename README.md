# xRAG1010
RAG

## 启动：
uv run uvicorn web.api.import_service:app --host 127.0.0.1 --port 8000 --reload
uv run uvicorn web.api.query_service:app --host 127.0.0.1 --port 8001 --reload
导入页：http://127.0.0.1:8000/import.html
检索页：http://127.0.0.1:8001/chat.html