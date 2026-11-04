@echo off
rem Drag your race .miz onto this file (or run: make_kneeboards.bat MyRace.miz)
rem Pages are made at 768x1024; add --size 1536x2048 for the larger ones.
python -I "%~dp0kneeboard\kneeboard.py" go --size 768x1024 %*
pause
