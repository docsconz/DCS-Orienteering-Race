@echo off
rem Drag your race .miz onto this file (or run: make_kneeboards.bat MyRace.miz)
python -I "%~dp0kneeboard\kneeboard.py" go %*
pause
