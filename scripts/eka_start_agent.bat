@echo off
REM EKA Agent — Full Deployment Startup Script
REM Starts both retrieval server (:8100) and agent server (:8000)
REM
REM Prerequisites:
REM   - D:\training-data\faiss_index\faiss_index.bin (1.15M vectors)
REM   - D:\training-data\faiss_index\doc_store.jsonl
REM   - D:\training-data\adapters\adapter_fullbatch_2026-08-20\final (LoRA adapter)

echo === EKA Agent Deployment Startup ===
echo.

REM Step 1: Start retrieval server (FAISS index + doc_store offset index)
echo [1/2] Starting retrieval server on port 8100...
start "EKA-Retrieval" /min cmd /c ""C:\Users\abcom\AppData\Local\Programs\Python\Python312\python.exe" "C:\Users\abcom\eka_retrieval_server.py" 2>&1 > "C:\Users\abcom\eka_retrieval.log""

REM Wait for retrieval server to load (FAISS index ~60s + offset index ~75s first time)
echo     Waiting for retrieval server to load (150s)...
timeout /t 150 /nobreak >nul

REM Step 2: Start agent server (Qwen2.5-0.5B + LoRA adapter, float16 CPU)
echo [2/2] Starting agent server on port 8000...
start "EKA-Agent" /min cmd /c ""C:\Users\abcom\AppData\Local\Programs\Python\Python312\python.exe" "C:\Users\abcom\eka_agent_deploy.py" 2>&1 > "C:\Users\abcom\eka_agent.log""

REM Wait for agent model to load (~15s)
echo     Waiting for agent model to load (20s)...
timeout /t 20 /nobreak >nul

echo.
echo === EKA Agent Deployment Ready ===
echo.
echo   Retrieval server:  http://localhost:8100/health
echo   Agent server:      http://localhost:8000/health
echo   Query endpoint:    http://localhost:8000/query
echo   Streaming query:   http://localhost:8000/query/stream
echo   Raw (no RAG):      http://localhost:8000/raw
echo.
echo   Knowledge base:    1,149,950 chunks (FAISS IndexFlatIP, 384-dim)
echo   Model:             Qwen2.5-0.5B-Instruct + LoRA adapter
echo   Retrieval:          all-MiniLM-L6-v2 (cosine similarity)
echo.
echo Example query:
echo   curl -X POST http://localhost:8000/query -H "Content-Type: application/json" -d "{\"query\": \"What data was found?\"}"
echo.
echo NOTE: CPU inference is ~200ms/token. Default max_tokens=128, top_k=3.
echo       For faster inference, use a GPU or reduce max_tokens.
echo.