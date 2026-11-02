@echo off
title Forex and Gold Paper Observation Dashboard
cd /d "%~dp0"
echo =======================================================================
echo Starting Forex and Gold Paper Observation Dashboard...
echo Local URL: http://localhost:8765/
echo Press Ctrl+C in this window to stop the dashboard.
echo =======================================================================

start "" "http://localhost:8765/"
"C:\Program Files\LibreOffice\program\python.exe" -m forex.dashboard --host 0.0.0.0 --port 8765
pause
