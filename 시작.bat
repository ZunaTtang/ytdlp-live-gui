@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ytarchive GUI 를 시작합니다...
python server.py
pause
