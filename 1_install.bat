@echo off
chcp 949 >nul
cd /d "%~dp0"

echo ============================================
echo   Gameloc 설치
echo ============================================
echo.
echo  파이썬을 찾는 중...

call "%~dp0_findpy.bat"
if defined PY goto :haspy

echo.
echo  파이썬이 없습니다.
echo.
winget --version >nul 2>&1
if errorlevel 1 goto :nowinget

set /p OK="  자동으로 설치할까요? (y/n): "
if /i not "%OK%"=="y" goto :nowinget

echo.
echo  파이썬을 설치합니다. 몇 분 걸립니다...
echo.
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
echo.
echo  설치가 끝났습니다. 다시 찾아봅니다...

rem PATH 는 아직 갱신이 안 됐을 수 있으니, 설치 폴더를 직접 뒤집니다.
call "%~dp0_findpy.bat"
if defined PY (
  echo  찾았습니다. 창을 다시 열 필요 없습니다.
  goto :haspy
)

echo.
echo  [X] 설치는 됐는데 아직 못 찾겠습니다.
echo      컴퓨터를 한 번 다시 켠 뒤 1_install.bat 을 다시 눌러 주세요.
echo.
pause
exit /b 1

:nowinget
echo.
echo  [X] 직접 설치해 주세요.
echo      https://www.python.org/downloads/
echo.
echo      설치 화면 맨 아래 "Add python.exe to PATH" 를 꼭 체크하세요.
echo.
pause
exit /b 1

:haspy
for /f "tokens=*" %%v in ('%PY% --version 2^>^&1') do echo  [O] %%v
echo %PY%> "%~dp0python.txt"
echo.
echo  필요한 것들을 설치합니다. 1~2분 걸립니다...
echo.

%PY% -m pip install --upgrade pip -q
%PY% -m pip install -e .
if errorlevel 1 (
    echo.
    echo  [X] 설치 실패. 위 메시지를 복사해서 물어봐 주세요.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   설치 완료
echo   이제 2_start.bat 을 실행하세요.
echo ============================================
pause
