@echo off
setlocal

where codex-os.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  codex-os.exe mcp
  exit /b %ERRORLEVEL%
)

if exist "%~dp0..\..\..\.venv\Scripts\codex-os.exe" (
  "%~dp0..\..\..\.venv\Scripts\codex-os.exe" mcp
  exit /b %ERRORLEVEL%
)

if exist "%USERPROFILE%\.local\bin\codex-os.exe" (
  "%USERPROFILE%\.local\bin\codex-os.exe" mcp
  exit /b %ERRORLEVEL%
)

1>&2 echo AI Engineering OS runtime not found. Run: uv tool install ^<repository-root^>
exit /b 50
