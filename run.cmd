@echo off
setlocal enabledelayedexpansion

:: Target default settings
set "PORT=8765"
set "BIND=127.0.0.1"
set "STOP_ONLY=0"
set "NO_BROWSER=0"

:: Parse arguments
:parse_args
if "%~1"=="" goto run_script
if /i "%~1"=="-Port" (set "PORT=%~2" & shift & shift & goto parse_args)
if /i "%~1"=="-Bind" (set "BIND=%~2" & shift & shift & goto parse_args)
if /i "%~1"=="-Stop" (set "STOP_ONLY=1" & shift & goto parse_args)
if /i "%~1"=="-NoBrowser" (set "NO_BROWSER=1" & shift & goto parse_args)
shift
goto parse_args

:run_script
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

if "%STOP_ONLY%"=="1" (
    call :stop_designer
    exit /b 0
)

:: Environment Setup
set "PY_VENV=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%PY_VENV%" (
    where py >nul 2>&1 && (set "BOOTSTRAP=py") || (
        where python >nul 2>&1 && (set "BOOTSTRAP=python") || (
            echo no Python found on PATH — install Python 3.10+ from python.org
            exit /b 1
        )
    )
    echo   creating .venv ...
    !BOOTSTRAP! -m venv .venv
    if errorlevel 1 (
        echo could not create .venv — is Python 3.10+ installed?
        exit /b 1
    )
    "%SCRIPT_DIR%.venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    "%SCRIPT_DIR%.venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
)

:: Restart Logic
call :stop_designer
echo   starting designer on http://%BIND%:%PORT%
start "" /b "%PY_VENV%" "%SCRIPT_DIR%app.py" --host %BIND% --port %PORT%

:: Wait loop
set "READY=0"
for /l %%i in (1,1,40) do (
    timeout /t 1 /nobreak >nul
    curl -s --max-time 2 "http://127.0.0.1:%PORT%/api/maps" >nul && (
        set "READY=1"
        goto loop_end
    )
)
:loop_end

if "%READY%"=="0" (
    echo the designer did not come up on port %PORT%
    exit /b 1
)

echo   ready — designer is active
echo   stop it with:  run.cmd -Stop
if "%NO_BROWSER%"=="0" start "" "http://127.0.0.1:%PORT%/"
exit /b 0

:: Functions
:stop_designer
:: Kill process holding the port via netstat tracking
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":%PORT% *[^ ]* *LISTENING"') do (
    if not "%%a"=="0" (
        curl -s -X POST --max-time 2 "http://127.0.0.1:%PORT%/api/shutdown" >nul 2>&1
        timeout /t 1 /nobreak >nul
        taskkill /f /pid %%a >nul 2>&1
    )
)
:: Kill lingering app.py processes matching current folder path
for /f "tokens=2 delims=," %%p in ('wmic process where "name='python.exe' or name='pythonw.exe'" get commandline^,processid /format:csv ^| findstr /i "app.py" 2^>nul') do (
    wmic process where processid=%%p get commandline | findstr /i "%SCRIPT_DIR:\=\\%" >nul && (
        taskkill /f /pid %%p >nul 2>&1
    )
)
goto :eof
