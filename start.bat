@echo off
chcp 65001 >nul
title LUME shop
cd /d "%~dp0"
echo LUME backend + static: http://127.0.0.1:8300  (Ctrl+C - stop)
start "" http://127.0.0.1:8300/
python server/app.py
