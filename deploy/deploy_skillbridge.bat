@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title layout-check-mcp deploy

REM ============================================================
REM  deploy.bat -- layout-check-mcp standalone deployer  (v5)
REM
REM  v5 change (fix winget false-success bug):
REM    - InstallViaWinget now takes a verify command as its first arg
REM      and runs it for real after each attempt. winget's exit code is
REM      NOT trusted: the underlying installer can fail (exit 1) while
REM      winget still returns 0. Same lesson as extracta.exe -- do not
REM      trust the exit code, verify the artifact.
REM    - Git install: dropped "--custom /o:PathOption=CmdTools" (it
REM      caused the installer to fail with exit 1), added "--scope user"
REM      (avoids requiring admin rights).
REM    - winget log flag fixed: --log-file does not exist, use --log.
REM
REM  Save requirement: pure ASCII or UTF-8 (no BOM). English only.
REM  Editor requirement: line endings MUST be CRLF. LF-only breaks
REM                      CMD's "call :label" resolution.
REM  NOTE: never put ( or ) inside "echo" lines that live inside an
REM        "if (...)" block. CMD parses them as block delimiters.
REM ============================================================

REM ---- Script directory (strip trailing backslash) ----
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

REM ---- GitHub repo info ----
set "REPO_URL=https://github.com/ds2457513911/layout-check-mcp.git"
set "REPO_NAME=layout-check-mcp"
set "PROJECT_ROOT=%SCRIPT_DIR%\%REPO_NAME%"

REM ---- Fixed paths ----
set "LEGACY_ENV=%USERPROFILE%\.local\share\skillbridge_env"
set "VENV_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "VENV_PYTHONW=%PROJECT_ROOT%\.venv\Scripts\pythonw.exe"

echo.
echo ============================================================
echo  layout-check-mcp deployer
echo ============================================================
echo   Work dir : %SCRIPT_DIR%
echo   Project  : %PROJECT_ROOT%
echo.

call :RefreshPath
call :CleanWingetLeftover

REM ---- Check winget ----
where winget >nul 2>&1
if errorlevel 1 (
    echo   [X] winget not found.
    echo       Install "App Installer" from Microsoft Store and retry.
    goto :fail
)

REM ============ Step 1: uv ============
echo.
echo [1/7] Check uv
call :FindUv
if not defined UV (
    echo       uv not found, installing via winget...
    call :InstallViaWinget "where uv" astral-sh.uv
    if errorlevel 1 (
        echo   [X] uv install failed after retries.
        goto :fail
    )
    call :FindUv
)
if not defined UV (
    echo   [X] uv still not found after install.
    goto :fail
)
echo   [OK] uv: !UV!

REM ============ Step 2: git ============
echo.
echo [2/7] Check git
where git >nul 2>&1
if errorlevel 1 (
    echo       git not found, installing via winget...
    call :InstallViaWinget "where git" --id Git.Git -e --source winget --scope user
    if errorlevel 1 (
        echo   [X] git install failed after retries.
        goto :fail
    )
)
where git >nul 2>&1
if errorlevel 1 (
    echo   [X] git still not found after install.
    goto :fail
)
echo   [OK] git ready

REM ============ Step 3: clone / pull ============
echo.
echo [3/7] Fetch project

if exist "%PROJECT_ROOT%" (
    if exist "%PROJECT_ROOT%\.git" (
        echo       Project exists, running git pull...
        pushd "%PROJECT_ROOT%"
        git pull
        if errorlevel 1 (
            popd
            echo   [X] git pull failed.
            echo       Possible: local changes, network issue, or proxy needed.
            goto :fail
        )
        popd
        echo   [OK] project updated
    ) else (
        echo   [X] %PROJECT_ROOT% exists but is not a git repo.
        echo       Delete that folder and retry.
        goto :fail
    )
) else (
    echo       Cloning from GitHub, first time may take a while...
    git clone "%REPO_URL%" "%PROJECT_ROOT%"
    if errorlevel 1 (
        echo   [X] git clone failed.
        echo       Possible reasons:
        echo         - Cannot reach GitHub - proxy may be required
        echo         - Repo URL changed
        echo         - Disk permission issue
        goto :fail
    )
    echo   [OK] project downloaded
)

if not exist "%PROJECT_ROOT%\pyproject.toml" (
    echo   [X] %PROJECT_ROOT% is not a valid project - pyproject.toml missing
    goto :fail
)

REM ============ Step 4: Cleanup legacy skillbridge_env ============
echo.
echo [4/7] Cleanup legacy skillbridge_env
if exist "%LEGACY_ENV%" (
    echo       Found legacy env: %LEGACY_ENV%
    echo       Removing it. v4 uses a single .venv inside the project.
    rmdir /S /Q "%LEGACY_ENV%" 2>nul
    if exist "%LEGACY_ENV%" (
        echo   [!] Could not fully delete - Allegro may be running.
        echo       Close Allegro, then re-run deploy.bat to clean it up.
        echo       Continuing anyway; the new env does not depend on it.
    ) else (
        echo   [OK] legacy env removed
    )
) else (
    echo   [OK] no legacy env found
)

REM ============ Step 5: uv sync ============
echo.
echo [5/7] Install dependencies into project .venv

if exist "%VENV_PYTHON%" (
    set "VENV_VER="
    for /f "delims=" %%v in ('"%VENV_PYTHON%" -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2^>nul') do set "VENV_VER=%%v"
    if defined VENV_VER (
        echo       existing .venv Python: !VENV_VER!
        set "RECREATE="
        for /f "tokens=1,2 delims=." %%a in ("!VENV_VER!") do (
            if %%a GTR 3 set "RECREATE=1"
            if %%a EQU 3 if %%b GEQ 13 set "RECREATE=1"
        )
        if defined RECREATE (
            echo   [!] Python !VENV_VER! is incompatible with skillbridge - recreating .venv
            rmdir /S /Q "%PROJECT_ROOT%\.venv" 2>nul
        )
    ) else (
        echo   [!] Could not detect .venv Python version - recreating to be safe
        rmdir /S /Q "%PROJECT_ROOT%\.venv" 2>nul
    )
)

pushd "%PROJECT_ROOT%"
"!UV!" sync
if errorlevel 1 (
    popd
    echo   [X] uv sync failed.
    goto :fail
)
popd

if not exist "%VENV_PYTHON%" (
    echo   [X] %VENV_PYTHON% not found after uv sync
    goto :fail
)
echo   [OK] dependencies installed into .venv

REM ============ Step 6: Configure Allegro auto-load ============
echo.
echo [6/7] Configure Allegro SkillBridge auto-load
if not exist "%PROJECT_ROOT%\Tools\install_skillbridge.py" (
    echo   [X] Tools\install_skillbridge.py not found.
    goto :fail
)
"!UV!" run --directory "%PROJECT_ROOT%" python "%PROJECT_ROOT%\Tools\install_skillbridge.py"
if errorlevel 1 (
    echo   [X] install_skillbridge.py failed.
    goto :fail
)
echo   [OK] Allegro configured

REM ============ Step 7: MCP config ============
echo.
echo [7/7] Generate MCP config

set "UV_ESC=!UV:\=\\!"
set "ROOT_ESC=!PROJECT_ROOT:\=\\!"
set "MCP_FILE=%SCRIPT_DIR%\mcp-config.json"

(
echo {
echo   "mcpServers": {
echo     "layout-check-v2": {
echo       "command": "!UV_ESC!",
echo       "args": ["run", "--directory", "!ROOT_ESC!", "layout-check-mcp"]
echo     }
echo   }
echo }
) > "!MCP_FILE!"

set "SKILL_DIR="
if exist "%PROJECT_ROOT%\.trae\skills\footprint-tolerance-check\SKILL.md" (
    set "SKILL_DIR=%PROJECT_ROOT%\.trae\skills\footprint-tolerance-check"
) else if exist "%PROJECT_ROOT%\skills\footprint-tolerance-check\SKILL.md" (
    set "SKILL_DIR=%PROJECT_ROOT%\skills\footprint-tolerance-check"
)

echo.
echo ============================================================
echo  Deploy complete
echo ============================================================
echo.
echo [1] MCP config saved to: !MCP_FILE!
echo.
type "!MCP_FILE!"
echo.
echo [2] SKILL.md path
if defined SKILL_DIR (
    echo     !SKILL_DIR!
    echo     Copy the footprint-tolerance-check folder into your Agent's skills dir.
) else (
    echo     [SKILL.md not found - check project structure]
)
echo.
echo [3] Next steps
echo     1. Restart Allegro PCB Editor
echo     2. Verify port: netstat -ano ^| findstr 7777
echo     3. Restart your Agent app, confirm MCP is connected
echo.
pause
exit /b 0


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

:RefreshPath
REM Append, never reset. Reset loses the original Windows PATH.
REM Include every path where winget may drop a binary:
REM   - WindowsApps : alias shims
REM   - WinGet\Links: portable package aliases (uv lands here)
REM   - .local\bin / .cargo\bin : astral installer locations
REM   - Programs\uv / Programs\Git / Program Files\Git : winget scope-user/machine
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
REM ---- Usage ----
REM   call :InstallViaWinget "<verify_cmd>" <winget_args...>
REM Examples:
REM   call :InstallViaWinget "where uv" astral-sh.uv
REM   call :InstallViaWinget "where git" --id Git.Git -e --source winget --scope user
REM
REM Why verify_cmd:
REM   winget returns 0 even when the underlying installer fails (exit 1).
REM   So we do not trust winget's exit code. We run verify_cmd after each
REM   attempt and only consider the install successful if the binary is
REM   actually resolvable on PATH. Same principle as extracta.exe.
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

REM Refresh PATH, then actually run the verify command.
call :RefreshPath
%VERIFY_CMD% >nul 2>&1
if not errorlevel 1 (
    echo   [OK] installed and verified on attempt !WG_TRY!
    exit /b 0
)

if !WG_TRY! GEQ 3 (
    echo   [X] winget install failed after 3 attempts.
    echo       Verify command: %VERIFY_CMD%
    echo       Detailed log: !WG_LOG!
    echo       Please open that file and share the last 30 lines.
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