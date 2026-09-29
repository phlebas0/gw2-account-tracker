@echo off
cd /d "%~dp0"
python local_app.py
if errorlevel 1 pause
