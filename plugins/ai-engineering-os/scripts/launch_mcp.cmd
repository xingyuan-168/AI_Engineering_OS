@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PLUGIN_ROOT=%~dp0.."
python "%~dp0runtime_entry.py" mcp
exit /b %ERRORLEVEL%
