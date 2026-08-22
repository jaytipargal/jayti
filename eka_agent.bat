@echo off
REM EKA Agent Windows Client
REM Usage: eka_agent.bat "your query here"
REM        eka_agent.bat "query" --json
REM        eka_agent.bat "query" --raw

setlocal

set EKA_AGENT_URL=https://agent.urgaa.in

if "%~1"=="" (
    echo EKA Agent Client (Windows)
    echo.
    echo Usage: eka_agent.bat "your query" [options]
    echo.
    echo Options:
    echo   --json     Output raw JSON response
    echo   --raw      Direct LLM query, no RAG
    echo   --top-k N  Number of retrieval results (default: 5)
    echo.
    echo Examples:
    echo   eka_agent.bat "What data was found?"
    echo   eka_agent.bat "WhatsApp chats" --json
    echo   eka_agent.bat "quick question" --raw
    exit /b 1
)

set QUERY=%~1
set USE_RAG=true
set RAW_MODE=
set TOP_K=5
set JSON_FLAG=

:parse
shift
if "%~1"=="" goto run
if /I "%~1"=="--json" set JSON_FLAG=--json& goto parse
if /I "%~1"=="--raw" set RAW_MODE=--raw& set USE_RAG=false& goto parse
if /I "%~1"=="--no-rag" set USE_RAG=false& goto parse
if /I "%~1"=="--top-k" (
    shift
    set TOP_K=%~1
    goto parse
)
goto parse

:run
if defined RAW_MODE (
    set ENDPOINT=%EKA_AGENT_URL%/agent/raw
) else (
    set ENDPOINT=%EKA_AGENT_URL%/agent/query
)

if defined JSON_FLAG (
    curl -s -X POST %ENDPOINT% -H "Content-Type: application/json" -d "{\"query\": \"%QUERY%\", \"top_k\": %TOP_K%, \"use_rag\": %USE_RAG%}"
) else (
    curl -s -X POST %ENDPOINT% -H "Content-Type: application/json" -d "{\"query\": \"%QUERY%\", \"top_k\": %TOP_K%, \"use_rag\": %USE_RAG%}"
)

echo.
endlocal