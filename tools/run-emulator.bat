@echo off
rem ===========================================================================
rem  Heroic Chant -- bring up a device, the boot shim and the game server.
rem
rem  Finds the first device adb will talk to (connecting to the usual emulator
rem  endpoints if none is attached), points the device's loopback back at this
rem  PC with `adb reverse`, starts the boot shim in its own minimised window,
rem  waits for it to answer, then runs the game server in THIS window.
rem
rem  Ctrl+C stops the server.  The shim is torn down on the way out, and again
rem  at the start of the next run, so a hard kill cannot leave one orphaned.
rem
rem      tools\run-emulator.bat                normal
rem      tools\run-emulator.bat --debug        DEBUG logging (packet detail)
rem      tools\run-emulator.bat --log          also write logs\game.log
rem      tools\run-emulator.bat --wait 120     wait up to 120s for a device
rem      tools\run-emulator.bat --serial NAME  choose the device by hand
rem      tools\run-emulator.bat --skip-adb     servers only, touch no device
rem      tools\run-emulator.bat --stop         kill a previous run and exit
rem ===========================================================================
setlocal enabledelayedexpansion
rem  shift (in the argument loop below) moves %0 too, so the script name
rem  has to be taken before any of it runs.
set "SELF=%~nx0"

set "SERVER=%~dp0.."
pushd "%SERVER%" || (echo Cannot enter %SERVER% & exit /b 1)
set "SERVER=%CD%"

rem  The device dials 127.0.0.1:8080 because that is what patch_apk.py wrote
rem  into global-metadata.dat.  The host side does not have to be 8080 -- see
rem  the reverse rules below.
set "DEVICE_HTTP=8080"
set "GAME_PORT=21010"
set "WEB_PORT=8099"
set "SHIM_TITLE=HC boot shim"

set "WAIT_SECS=20"
set "SERIAL="
set "LOGLEVEL=INFO"
set "LOGFILE="
set "SKIP_ADB=0"

:args
if "%~1"=="" goto args_done
if /i "%~1"=="--debug"    (set "LOGLEVEL=DEBUG" & shift & goto args)
if /i "%~1"=="--log"      (set "LOGFILE=--log-file logs\game.log" & shift & goto args)
if /i "%~1"=="--skip-adb" (set "SKIP_ADB=1"     & shift & goto args)
if /i "%~1"=="--wait"     (set "WAIT_SECS=%~2"  & shift & shift & goto args)
if /i "%~1"=="--serial"   (set "SERIAL=%~2"     & shift & shift & goto args)
if /i "%~1"=="--stop"     goto stop_only
if /i "%~1"=="--help"     goto usage
if /i "%~1"=="-h"         goto usage
echo Unknown option: %~1
goto usage
:args_done

rem --------------------------------------------------------------- tooling ---
rem  Prefer plain `python` over the `py -3` launcher: py.exe spawns the real
rem  interpreter as a CHILD, so the process holding the port is one level
rem  below what we started, which makes cleanup and Ctrl+C messier.  The
rem  import check weeds out the Windows Store stub, which is on PATH by
rem  default and does nothing but advertise the Store.
set "PY="
python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  echo [x] No Python found on PATH.  Install 3.8+ and try again.
  popd
  exit /b 1
)

set "ADB="
for %%A in (adb.exe) do if not defined ADB if not "%%~$PATH:A"=="" set "ADB=%%~$PATH:A"
if not defined ADB if exist "C:\platform-tools\adb.exe" set "ADB=C:\platform-tools\adb.exe"
if not defined ADB if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
if not defined ADB if "%SKIP_ADB%"=="0" (
  echo [x] adb not found.  Put platform-tools on PATH, or pass --skip-adb.
  popd
  exit /b 1
)

if not exist "..\files\ngelgames" (
  echo [x] ..\files\ngelgames is missing -- the shim has no assets to serve.
  echo     The client will start and then hang on its download.
)
if not exist "logs" mkdir "logs"

rem  Anything left behind by a previous run goes now, before we look at ports.
call :kill_shim

rem ---------------------------------------------------------------- device ---
if "%SKIP_ADB%"=="1" (
  echo [-] --skip-adb: leaving the device alone.
  goto ports
)

"%ADB%" start-server >nul 2>&1

set /a TRIES=%WAIT_SECS%
:find_device
call :first_device
if defined SERIAL goto got_device
rem  Emulators wait to be dialled rather than announcing themselves.  MuMu has
rem  been seen on 7555 and 16384, LDPlayer/BlueStacks on 5555/5565, Nox on
rem  62001 -- and the serial changes between sessions, so never hardcode one.
for %%E in (127.0.0.1:7555 127.0.0.1:16384 127.0.0.1:5555 127.0.0.1:5565 127.0.0.1:62001 127.0.0.1:21503) do "%ADB%" connect %%E >nul 2>&1
call :first_device
if defined SERIAL goto got_device
set /a TRIES-=2
if !TRIES! GTR 0 (
  echo     waiting for a device... !TRIES!s left
  call :sleep 2
  goto find_device
)
echo [x] No device after %WAIT_SECS%s.  Start the emulator, or plug the phone
echo     in with USB debugging on, then re-run -- or pass --wait 120.
"%ADB%" devices
popd
exit /b 1

:got_device
echo [+] device: !SERIAL!
set "BOOTED="
for /f "usebackq delims=" %%B in (`"%ADB%" -s !SERIAL! shell getprop sys.boot_completed 2^>nul`) do set "BOOTED=%%B"
echo !BOOTED! | findstr /c:"1" >nul
if errorlevel 1 (
  echo     ...still booting, waiting for it
  "%ADB%" -s !SERIAL! wait-for-device >nul 2>&1
)

rem ----------------------------------------------------------------- ports ---
:ports
call :port_owner %GAME_PORT%
if defined OWNER_PID (
  if /i "!OWNER_IMAGE!"=="python.exe" (
    echo [x] Port %GAME_PORT% is already held by python.exe ^(PID !OWNER_PID!^).
    echo     A server is still running.  Close its window, or:  %SELF% --stop
  ) else (
    echo [x] Port %GAME_PORT% is held by !OWNER_IMAGE! ^(PID !OWNER_PID!^).
    echo     Free it before starting the server.
  )
  popd
  exit /b 1
)

rem  Pick a host port for the shim.  8080 is the natural one, but Steam's
rem  steamwebhelper.exe squats on it, so fall forward rather than fail.
set "HOST_HTTP="
for %%P in (8080 8081 8082 8083 8084 8085) do (
  if not defined HOST_HTTP (
    call :port_owner %%P
    if not defined OWNER_PID set "HOST_HTTP=%%P"
  )
)
if not defined HOST_HTTP (
  echo [x] Nothing free in 8080-8085 for the boot shim.
  popd
  exit /b 1
)
if not "%HOST_HTTP%"=="%DEVICE_HTTP%" echo [-] %DEVICE_HTTP% is taken on this PC; the shim takes %HOST_HTTP% instead.

rem --------------------------------------------------------------- reverse ---
if "%SKIP_ADB%"=="1" goto shim

"%ADB%" -s !SERIAL! reverse --remove-all >nul 2>&1
rem  The patched APK asks for 127.0.0.1:8080 on the DEVICE; send that to
rem  whichever port the shim actually got on this PC.
call :reverse %DEVICE_HTTP% %HOST_HTTP%
rem  The shim writes its own host:port into the cdninfo it serves, so when it
rem  is not on 8080 the client follows that URL next.  Map it too.
if not "%HOST_HTTP%"=="%DEVICE_HTTP%" call :reverse %HOST_HTTP% %HOST_HTTP%
call :reverse %GAME_PORT% %GAME_PORT%
rem  Optional: lets the device's own browser reach the dashboard.
"%ADB%" -s !SERIAL! reverse tcp:%WEB_PORT% tcp:%WEB_PORT% >nul 2>&1

echo [+] reverse:
for /f "usebackq delims=" %%R in (`"%ADB%" -s !SERIAL! reverse --list`) do echo       %%R

rem ------------------------------------------------------------------ shim ---
:shim
echo [+] boot shim on 127.0.0.1:%HOST_HTTP%  ^(logs\boot.log^)
start "%SHIM_TITLE%" /min /d "%SERVER%" cmd /c %PY% -u tools\bootserver.py --host 127.0.0.1 --http-port %HOST_HTTP% --no-dns --bind 127.0.0.1 ^>logs\boot.log 2^>^&1

set /a SPIN=0
:wait_shim
call :port_owner %HOST_HTTP%
if defined OWNER_PID goto shim_up
set /a SPIN+=1
if !SPIN! GTR 15 (
  echo [x] The shim never came up.  logs\boot.log says:
  type logs\boot.log
  call :kill_shim
  popd
  exit /b 1
)
call :sleep 1
goto wait_shim
:shim_up

rem ---------------------------------------------------------------- server ---
echo [+] dashboard: http://127.0.0.1:%WEB_PORT%
echo [+] game server on %GAME_PORT% ^(%LOGLEVEL%^) -- Ctrl+C to stop
echo.
%PY% -m hc.main --host 127.0.0.1 --public-host 127.0.0.1 --port %GAME_PORT% --log-level %LOGLEVEL% %LOGFILE%

echo.
echo [-] server stopped; cleaning up.
call :kill_shim
popd
exit /b 0

rem =========================================================== subroutines ===

:first_device
rem  Sets SERIAL to the first device in state "device".  MuMu reports the same
rem  emulator under several aliases; whichever answers first is as good as any.
if defined SERIAL exit /b 0
for /f "usebackq skip=1 tokens=1,2" %%A in (`"%ADB%" devices`) do (
  if not defined SERIAL if "%%B"=="device" set "SERIAL=%%A"
  if "%%B"=="unauthorized" echo     %%A is unauthorized -- accept the RSA prompt on the device
)
exit /b 0

:reverse
"%ADB%" -s !SERIAL! reverse tcp:%1 tcp:%2 >nul 2>&1
if errorlevel 1 echo [x] adb reverse tcp:%1 to tcp:%2 failed
exit /b 0

:port_owner
rem  %1 = port.  Sets OWNER_PID / OWNER_IMAGE if something is LISTENING on it.
set "OWNER_PID="
set "OWNER_IMAGE="
for /f "usebackq tokens=5" %%P in (`netstat -ano -p TCP ^| findstr /r /c:":%1 .*LISTENING"`) do set "OWNER_PID=%%P"
if not defined OWNER_PID exit /b 0
for /f "usebackq tokens=1" %%I in (`tasklist /FI "PID eq !OWNER_PID!" /NH 2^>nul`) do set "OWNER_IMAGE=%%I"
exit /b 0

:sleep
rem  Sleep %1 seconds.  `timeout` reads the console directly and dies with
rem  "Input redirection is not supported" whenever stdin is not a terminal,
rem  which turns both wait loops into busy spins.  ping always sleeps.
ping -n %1 -w 1000 127.0.0.1 >nul 2>&1
exit /b 0

:kill_shim
taskkill /FI "WINDOWTITLE eq %SHIM_TITLE%*" /T /F >nul 2>&1
exit /b 0

:stop_only
call :kill_shim
call :port_owner %GAME_PORT%
if defined OWNER_PID if /i "!OWNER_IMAGE!"=="python.exe" (
  taskkill /PID !OWNER_PID! /T /F >nul 2>&1
  echo [-] stopped the game server ^(PID !OWNER_PID!^)
)
for %%P in (8080 8081 8082 8083 8084 8085) do (
  call :port_owner %%P
  if defined OWNER_PID if /i "!OWNER_IMAGE!"=="python.exe" (
    taskkill /PID !OWNER_PID! /T /F >nul 2>&1
    echo [-] stopped a boot shim on %%P ^(PID !OWNER_PID!^)
  )
)
echo [-] stopped.
popd
exit /b 0

:usage
echo.
echo   %SELF% [--debug] [--log] [--wait SECS] [--serial NAME] [--skip-adb] [--stop]
echo.
echo     --debug      DEBUG log level on the game server
echo     --log        also write the server log to logs\game.log
echo     --wait N     seconds to keep looking for a device (default 20)
echo     --serial X   use this device instead of the first one found
echo     --skip-adb   start the servers only; do not touch any device
echo     --stop       kill a previous run's shim and server, then exit
echo.
popd
exit /b 0
