@echo off
cd /d "%~dp0"
if not exist node_modules call npm.cmd install
if not exist dist call npm.cmd run build
start "" http://127.0.0.1:4318
npm.cmd start
