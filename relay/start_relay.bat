@echo off
title FFXIV Board Relay
cd /d "%~dp0"
python ffxiv_relay.py %*
if errorlevel 1 py ffxiv_relay.py %*
pause
