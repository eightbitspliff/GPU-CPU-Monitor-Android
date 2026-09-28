@echo off
cd /d "%~dp0"
pip install -r requirements.txt
python pc_monitor_server.py
pause
