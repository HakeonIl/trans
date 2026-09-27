@echo off
rem ---------------------------------------------------------------------
rem  파이썬 실행기를 찾아 PY 에 넣습니다. 못 찾으면 PY 는 빈 값입니다.
rem
rem  왜 이렇게 복잡한가:
rem   - 윈도우에는 python.exe 라는 껍데기가 하나 들어 있습니다
rem     (%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe).
rem     진짜 파이썬이 아니라 마이크로소프트 스토어를 여는 물건인데,
rem     PATH 에서 진짜보다 앞에 있어서 "python --version" 이 실패합니다.
rem     그래서 파이썬을 깔아도 계속 "파이썬이 없다" 고 나옵니다.
rem   - winget 으로 막 설치한 직후에는 PATH 가 아직 갱신되지 않습니다.
rem     그래서 PATH 를 믿지 않고 설치 폴더를 직접 뒤집니다.
rem ---------------------------------------------------------------------
set "PY="

rem 1) 지난번에 찾아 둔 것
if exist "%~dp0python.txt" (
  for /f "usebackq delims=" %%P in ("%~dp0python.txt") do call :try "%%P"
)
if defined PY goto :done

rem 2) py 런처 (스토어 껍데기가 없는 확실한 길)
call :trypy
if defined PY goto :done

rem 3) PATH 의 python.exe - 스토어 껍데기는 건너뜁니다
for %%i in (python.exe) do call :trypath "%%~$PATH:i"
if defined PY goto :done

rem 4) 흔히 깔리는 자리를 직접 뒤집니다
for /f "delims=" %%P in ('dir /b /s /a-d "%LOCALAPPDATA%\Programs\Python\python.exe" 2^>nul') do call :try "%%P"
if defined PY goto :done
for /f "delims=" %%P in ('dir /b /s /a-d "%ProgramFiles%\Python3*\python.exe" 2^>nul') do call :try "%%P"
if defined PY goto :done
for /f "delims=" %%P in ('dir /b /s /a-d "%ProgramFiles(x86)%\Python3*\python.exe" 2^>nul') do call :try "%%P"
if defined PY goto :done
for /f "delims=" %%P in ('dir /b /s /a-d "C:\Python3*\python.exe" 2^>nul') do call :try "%%P"
goto :done

:trypath
if "%~1"=="" exit /b
echo %~1| find /i "WindowsApps" >nul
if not errorlevel 1 exit /b
call :try "%~1"
exit /b

:try
if defined PY exit /b
if not exist "%~1" exit /b
"%~1" -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 exit /b
set PY="%~1"
exit /b

:trypy
py -3 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 exit /b
set PY=py -3
exit /b

:done
exit /b 0
