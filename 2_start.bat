@echo off
chcp 949 >nul
cd /d "%~dp0"

call "%~dp0_findpy.bat"
if not defined PY (
  echo.
  echo  [X] 파이썬을 찾지 못했습니다. 1_install.bat 을 먼저 실행하세요.
  echo.
  pause
  exit /b 1
)

echo 번역기를 여는 중입니다. 브라우저가 자동으로 열립니다...
echo 끝내려면 이 검은 창을 닫으세요.
echo.
%PY% -m gameloc.webui
if errorlevel 1 (
    echo.
    echo  [X] 실행 실패. 1_install.bat 을 먼저 실행했는지 확인하세요.
    pause
)
