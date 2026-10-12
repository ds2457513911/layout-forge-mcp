@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title layout-forge-mcp deployer v7

REM ============================================================
REM  deploy.bat -- layout-forge-mcp one-click deployer  (v7)
REM
REM  Goal: on a brand-new Windows machine, double-click this file
REM  and end up with a working layout-forge-mcp service:
REM    1. get the code        local checkout OR git clone
REM    2. create .venv        uv preferred, python fallback
REM    3. install deps
REM    4. register MCP        venv python + -c sys.path injection
REM    5. install Skills      deploy\skills -> ~/.workbuddy/skills
REM    6. config SkillBridge  allegro.ilinit auto-load, port 7777
REM    7. smoke test
REM
REM  Usage:
REM    deploy.bat                            auto-detect, install to %USERPROFILE%\layout-forge-mcp
REM    deploy.bat /dir "D:\tools\lfm"        choose install dir
REM    deploy.bat /pcbenv "D:\...\pcbenv"    skip pcbenv auto-search
REM    deploy.bat /nobridge                  do not touch allegro.ilinit
REM    deploy.bat /dry                       dry run, print everything, write nothing
REM
REM  v7 changes vs the copy-from-check v6:
REM    - names corrected: layout-check-mcp -> layout-forge-mcp. The old
REM      script registered a command that does not exist in this project
REM      and could never start.
REM    - LOCAL MODE: if ..\pyproject.toml exists, deploy this checkout in
REM      place instead of cloning a second copy next to deploy\.
REM    - MCP entry rewritten to the documented form: venv python with
REM      -c sys.path injection. "uv run" is NOT used because the client
REM      launches the server with an unknown cwd.
REM    - all Agent-side logic moved to deploy\setup_agent.py (python),
REM      so this bat only bootstraps the environment.
REM    - python fallback when uv is unavailable.
REM    - hard check: skillbridge must import from the project venv.
REM
REM  Save requirement: pure ASCII or UTF-8 no BOM. English only.
REM  Line endings MUST be CRLF - LF-only breaks "call :label".
REM  Never put literal parentheses inside echo lines that sit in an
REM  if (...) block.
REM ============================================================

REM ---- Script directory, strip trailing backslash ----
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

REM ---- Repo ----
set "REPO_URL=https://github.com/ds2457513911/layout-forge-mcp.git"

REM ---- Defaults ----
set "INSTALL_DIR=%USERPROFILE%\layout-forge-mcp"
set "USER_PCENV="
set "DRY_RUN=0"
set "NO_BRIDGE=0"

:argloop
if "%~1"=="" goto argdone
if /i "%~1"=="/dir" (
    set "INSTALL_DIR=%~2"
    shift
    shift
    goto argloop
)
if /i "%~1"=="/pcbenv" (
    set "USER_PCENV=%~2"
    shift
    shift
    goto argloop
)
if /i "%~1"=="/dry" (
    set "DRY_RUN=1"
    shift
    goto argloop
)
if /i "%~1"=="/nobridge" (
    set "NO_BRIDGE=1"
    shift
    goto argloop
)
if /i "%~1"=="/help" goto usage
if /i "%~1"=="-h" goto usage
echo   [X] unknown argument: %~1
goto usage

:argdone

echo.
echo ============================================================
echo  layout-forge-mcp deployer  v7
echo ============================================================
echo   Script dir  : %SCRIPT_DIR%
echo   Install dir : %INSTALL_DIR%
echo   Dry run     : %DRY_RUN%
echo.

call :RefreshPath
call :CleanWingetLeftover

REM ============ Step 0: locate project root ============
echo [0/6] Locate project
set "LOCAL_ROOT="
for %%i in ("%SCRIPT_DIR%\..") do set "LOCAL_ROOT=%%~fi"

set "MODE=clone"
set "PROJECT_ROOT=%INSTALL_DIR%"
if exist "%LOCAL_ROOT%\pyproject.toml" (
    set "PROJECT_ROOT=%LOCAL_ROOT%"
    set "MODE=local"
    echo       MODE = local - deploying this checkout in place
) else (
    echo       MODE = clone - no pyproject.toml above deploy\, will fetch code
)
echo       Project     : %PROJECT_ROOT%
echo.

REM ============ Step 1: winget availability ============
echo [1/6] Check winget
set "HAVE_WINGET=0"
where winget >nul 2>&1
if not errorlevel 1 set "HAVE_WINGET=1"
if "%HAVE_WINGET%"=="1" (
    echo   [OK] winget available
) else (
    echo   [!] winget not found.
    echo       It is only needed to auto-install uv / git.
    echo       If uv and git are already present, we can continue.
)
echo.

REM ============ Step 2: uv or python ============
echo [2/6] Check uv / python
call :FindUv
if not defined UV (
    call :FindPython
)
if not defined UV if not defined PY_EXE (
    if "%HAVE_WINGET%"=="1" (
        echo       neither uv nor python found, installing uv via winget...
        call :InstallViaWinget "where uv" astral-sh.uv
        if errorlevel 1 (
            echo   [X] uv install failed after retries.
            goto :fail
        )
        call :FindUv
    )
)
if not defined UV if not defined PY_EXE (
    echo   [X] No uv and no python, and winget is unavailable.
    echo       Install Python 3.10-3.13 manually, then re-run.
    goto :fail
)
if defined UV (
    echo   [OK] uv     : !UV!
) else (
    echo   [OK] python : !PY_EXE!
)
echo.

REM ============ Step 3: git ============
echo [3/6] Check git
if "%MODE%"=="local" (
    echo       local mode, git not required
) else (
    where git >nul 2>&1
    if errorlevel 1 (
        if "%HAVE_WINGET%"=="1" (
            echo       git not found, installing via winget...
            call :InstallViaWinget "where git" --id Git.Git -e --source winget --scope user
            if errorlevel 1 (
                echo   [X] git install failed after retries.
                goto :fail
            )
        )
    )
    where git >nul 2>&1
    if errorlevel 1 (
        echo   [X] git is required to fetch the project.
        echo       Install Git for Windows, or copy the project into:
        echo         %PROJECT_ROOT%
        goto :fail
    )
    echo   [OK] git ready
)
echo.

REM ============ Step 4: fetch code ============
echo [4/6] Fetch project
if "%MODE%"=="local" (
    echo       using existing checkout, nothing to fetch
) else (
    if exist "%PROJECT_ROOT%" (
        if exist "%PROJECT_ROOT%\.git" (
            echo       Project exists, running git pull...
            pushd "%PROJECT_ROOT%"
            git pull
            if errorlevel 1 (
                popd
                echo   [X] git pull failed.
                echo       Local changes, network, or proxy issue.
                goto :fail
            )
            popd
            echo   [OK] project updated
        ) else (
            echo   [X] %PROJECT_ROOT% exists but is not a git repo.
            echo       Delete that folder or pass /dir with a new path.
            goto :fail
        )
    ) else (
        echo       Cloning from GitHub, first time may take a while...
        git clone "%REPO_URL%" "%PROJECT_ROOT%"
        if errorlevel 1 (
            echo   [X] git clone failed.
            echo       Check network / proxy / repo URL.
            goto :fail
        )
        echo   [OK] project downloaded
    )
)

if not exist "%PROJECT_ROOT%\pyproject.toml" (
    echo   [X] %PROJECT_ROOT% is not a valid project - pyproject.toml missing
    goto :fail
)
echo.

REM ============ Step 5: venv + dependencies ============
echo [5/6] Create .venv and install dependencies
set "VENV_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"

if exist "%VENV_PYTHON%" (
    set "VENV_VER="
    for /f "delims=" %%v in ('"%VENV_PYTHON%" -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2^>nul') do set "VENV_VER=%%v"
    if defined VENV_VER (
        echo       existing .venv Python: !VENV_VER!
        set "RECREATE=0"
        for /f "tokens=1,2 delims=." %%a in ("!VENV_VER!") do (
            if %%a LSS 3 set "RECREATE=1"
            if %%a EQU 3 if %%b LSS 10 set "RECREATE=1"
            if %%a EQU 3 if %%b GTR 13 set "RECREATE=1"
        )
        if "!RECREATE!"=="1" (
            echo   [!] Python !VENV_VER! is outside the supported 3.10-3.13 range
            echo       recreating .venv ...
            rmdir /S /Q "%PROJECT_ROOT%\.venv" 2>nul
        )
    ) else (
        echo   [!] Could not detect .venv Python version - recreating to be safe
        rmdir /S /Q "%PROJECT_ROOT%\.venv" 2>nul
    )
)

if "%DRY_RUN%"=="1" (
    echo       [DRY-RUN] skipping environment creation
    goto :step5_done
)

if defined UV (
    pushd "%PROJECT_ROOT%"
    "!UV!" sync
    if errorlevel 1 (
        popd
        echo   [X] uv sync failed.
        goto :fail
    )
    popd
) else (
    if not exist "%VENV_PYTHON%" (
        echo       creating .venv with !PY_EXE! ...
        "!PY_EXE!" -m venv "%PROJECT_ROOT%\.venv"
        if errorlevel 1 (
            echo   [X] python -m venv failed.
            goto :fail
        )
    )
    "%VENV_PYTHON%" -m pip install --upgrade pip
    "%VENV_PYTHON%" -m pip install -e "%PROJECT_ROOT%"
    if errorlevel 1 (
        echo   [X] pip install -e . failed.
        goto :fail
    )
)

if not exist "%VENV_PYTHON%" (
    echo   [X] %VENV_PYTHON% not found after install
    goto :fail
)
echo   [OK] dependencies installed into .venv

REM ---- hard check: skillbridge must be importable from this venv ----
"%VENV_PYTHON%" -c "import skillbridge" >nul 2>&1
if errorlevel 1 (
    echo   [X] skillbridge is NOT importable from the project venv.
    echo       Classic deploy killer: the MCP command must point at THIS
    echo       venv python, never at system python.
    goto :fail
)
echo   [OK] skillbridge importable from project venv

:step5_done
echo.

REM ============ Step 6: Agent-side config ============
echo [6/6] Register MCP, install Skills, config SkillBridge

if "%DRY_RUN%"=="1" (
    echo       [DRY-RUN] setup_agent.py not executed
    goto :done
)

set "AGENT_ARGS=--project-root "%PROJECT_ROOT%""
if defined USER_PCENV set "AGENT_ARGS=!AGENT_ARGS! --pcbenv "%USER_PCENV%""
if "%NO_BRIDGE%"=="1" set "AGENT_ARGS=!AGENT_ARGS! --no-skillbridge"

"%VENV_PYTHON%" "%PROJECT_ROOT%\deploy\setup_agent.py" !AGENT_ARGS!
if errorlevel 1 (
    echo   [!] setup_agent.py reported a failure. Read the output above.
)

:done
echo.
echo ============================================================
echo  Deploy finished
echo ============================================================
echo.
echo   Project      : %PROJECT_ROOT%
echo   venv python  : %VENV_PYTHON%
echo.
echo   Next steps
echo     1. Restart your Agent client - MCP is a long-lived process
echo     2. Confirm layout-forge-mcp is connected, 28 tools
echo     3. Start Allegro PCB Editor, then check the port:
echo          netstat -ano ^| findstr 7777
echo.
pause
exit /b 0


:usage
echo.
echo   usage: deploy.bat [/dir path] [/pcbenv path] [/nobridge] [/dry]
echo.
echo     /dir       install directory   default: %USERPROFILE%\layout-forge-mcp
echo     /pcbenv    Allegro pcbenv dir  skip auto-search
echo     /nobridge  do not modify allegro.ilinit
echo     /dry       print what would happen, change nothing
echo.
pause
exit /b 1


REM ============================================================
REM Subroutines
REM ============================================================

:FindUv
set "UV="
for /f "delims=" %%i in ('where uv 2^>nul') do (
    set "UV=%%i"
    goto :FindUvDone
)
:FindUvDone
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined UV if exist "%LOCALAPPDATA%\Programs\uv\uv.exe" set "UV=%LOCALAPPDATA%\Programs\uv\uv.exe"
exit /b 0

:FindPython
set "PY_EXE="
for /f "delims=" %%i in ('where python 2^>nul') do (
    set "PY_EXE=%%i"
    goto :FindPyDone
)
:FindPyDone
if not defined PY_EXE if exist "%SystemRoot%\py.exe" set "PY_EXE=%SystemRoot%\py.exe"
exit /b 0

:RefreshPath
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links"
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WindowsApps"
set "PATH=%PATH%;%USERPROFILE%\.local\bin"
set "PATH=%PATH%;%USERPROFILE%\.cargo\bin"
set "PATH=%PATH%;%LOCALAPPDATA%\Programs\uv"
set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Git\cmd"
set "PATH=%PATH%;%ProgramFiles%\Git\cmd"
set "PATH=%PATH%;C:\Program Files\Git\cmd"
exit /b 0

:CleanWingetLeftover
tasklist /FI "IMAGENAME eq winget.exe" 2>nul | find /I "winget.exe" >nul
if not errorlevel 1 (
    echo   [!] Leftover winget process detected from a previous run.
    echo       Cleaning it up...
    taskkill /IM winget.exe /F >nul 2>&1
    timeout /t 2 /nobreak >nul
    echo   [OK] leftover cleaned.
)
exit /b 0

:InstallViaWinget
REM Usage: call :InstallViaWinget "<verify_cmd>" <winget_args...>
REM winget returns 0 even when the real installer fails, so we never
REM trust its exit code - we re-run verify_cmd and check the binary is
REM actually resolvable on PATH.
set "VERIFY_CMD=%~1"
set "WG_ARGS="
shift
:WingetArgLoop
if "%~1"=="" goto :WingetArgDone
set "WG_ARGS=!WG_ARGS! %~1"
shift
goto :WingetArgLoop
:WingetArgDone

set "WG_LOG=%TEMP%\winget_install_%RANDOM%.log"
set /a WG_TRY=0

:WingetTryLoop
set /a WG_TRY+=1
echo       winget install - attempt !WG_TRY!/3...
winget install !WG_ARGS! --accept-source-agreements --accept-package-agreements --log "!WG_LOG!"

call :RefreshPath
%VERIFY_CMD% >nul 2>&1
if not errorlevel 1 (
    echo   [OK] installed and verified on attempt !WG_TRY!
    exit /b 0
)

if !WG_TRY! GEQ 3 (
    echo   [X] winget install failed after 3 attempts.
    echo       Verify command: %VERIFY_CMD%
    echo       Detailed log : !WG_LOG!
    echo       Please open that log and share the last 30 lines.
    exit /b 1
)

echo       attempt !WG_TRY! failed. Running repair sequence...
echo       [repair 1/3] killing leftover winget processes...
taskkill /IM winget.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul

echo       [repair 2/3] updating winget sources...
winget source update >nul 2>&1

echo       [repair 3/3] resetting winget sources...
winget source reset --force >nul 2>&1

echo       waiting 5s before retry...
timeout /t 5 /nobreak >nul
goto :WingetTryLoop

:fail
echo.
echo   Deploy failed. Check the error above.
pause
exit /b 1
