@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "ROOT=%cd%"
if defined VTUBE_VENV (
    set "VENV=%VTUBE_VENV%"
) else (
    set "VENV=%LOCALAPPDATA%\VTubeVoiceAssistant\venv"
)
set "VENV_PY=%VENV%\Scripts\python.exe"
set "REQ=%ROOT%\requirements.txt"

set "BASE_PY="
if defined VTUBE_PYTHON (
    set "BASE_PY=%VTUBE_PYTHON%"
)
if not defined BASE_PY (
    for /d %%D in ("%APPDATA%\uv\python\cpython-3.11*") do (
        if exist "%%~D\python.exe" set "BASE_PY=%%~D\python.exe"
    )
)
if not defined BASE_PY (
    for /f "usebackq tokens=*" %%P in (`powershell -NoProfile -Command "$o = py -0p 2>$null; foreach ($l in $o) { if ($l -match '3\.11' -and $l -match '(\\[^ ]+\.exe)\s*$') { $Matches[1]; break } }"`) do set "BASE_PY=%%P"
)
if not defined BASE_PY (
    echo [ERROR] Python 3.11 not found. PyAudio needs 3.11-3.13 on Windows.
    echo Install:  py install 3.11
    echo Or set VTUBE_PYTHON to your python.exe path.
    pause
    exit /b 1
)

if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys; raise SystemExit(0 if sys.version_info[0:2]==(3,11) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo Recreating venv with Python 3.11 ^(PyAudio does not support 3.14 yet^)...
        rmdir /s /q "%VENV%" 2>nul
    )
)

if not exist "%VENV_PY%" (
    echo Creating virtual environment:
    echo   %VENV%
    echo Using: %BASE_PY%
    "%BASE_PY%" -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
)

set "USE_VENV=1"
"%VENV_PY%" -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Device Guard blocked venv python. Using: %BASE_PY%
    set "USE_VENV="
    set "VENV_PY=%BASE_PY%"
)

if defined USE_VENV (
    set "VIRTUAL_ENV=%VENV%"
    set "PATH=%VENV%\Scripts;%VENV%\Library\bin;%PATH%"
    set "PYTHONNOUSERSITE=1"
    call "%VENV%\Scripts\activate.bat"
)

echo Installing / updating dependencies...
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r "%REQ%"
if errorlevel 1 (
    echo.
    echo [ERROR] pip install failed. See messages above.
    pause
    exit /b 1
)

"%VENV_PY%" -c "import pyaudio" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] pyaudio is still missing. Try running this window as Administrator once,
    echo or install "Microsoft C++ Build Tools" if pip could not find a wheel.
    pause
    exit /b 1
)

echo.
echo Starting voice assistant...
"%VENV_PY%" "%ROOT%\code.py"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo [ERROR] Voice assistant exited with code %RC%.
    pause
    exit /b %RC%
)

echo.
echo Voice assistant stopped.
pause
exit /b 0