@echo off
cd /d "%~dp0"

python -m streamlit run browser_app.py --server.address 127.0.0.1 --server.port 8501 --server.headless false

if errorlevel 1 pause