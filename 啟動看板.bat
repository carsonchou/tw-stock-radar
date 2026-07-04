@echo off
cd /d "%~dp0"
:: 啟動 server（背景，視窗隱藏）
start "" /B pythonw team_server.py 2>nul
:: 等 server 就緒（最多 15 秒輪詢）
timeout /t 2 /nobreak >nul
:wait
powershell -Command "try{(New-Object Net.WebClient).DownloadString('http://127.0.0.1:8900/api/status');exit 0}catch{exit 1}" >nul 2>&1
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait
)
:: 用 Edge --app 模式開啟（像 native app，無工具列）
start "" "msedge.exe" --app=http://127.0.0.1:8900/ --window-size=1440,900
