@echo off
chcp 949 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   Gameloc 제거
echo ============================================
echo.
echo  지울 것:
echo    1. 파이썬에 등록된 gameloc      (pip uninstall)
echo    2. 설정 파일  %USERPROFILE%\.gameloc\
echo       - 번역 API 키가 들어 있습니다
echo.
echo  지우지 않는 것:
echo    - 파이썬 본체
echo    - 게임 폴더 안의 __gameloc_backup__ (번역 되돌리기용)
echo    - 이 폴더 자체 (제거가 끝나면 손으로 지우세요)
echo.

set /p OK="계속할까요? (y/n): "
if /i not "%OK%"=="y" (
  echo  취소했습니다.
  pause
  exit /b 0
)

echo.
echo  [1/3] 등록 해제...
python -m pip uninstall -y gameloc
if errorlevel 1 (
  echo    ^(이미 지워져 있거나 파이썬이 없습니다. 넘어갑니다^)
)

echo.
echo  [2/3] 설정 파일...
if exist "%USERPROFILE%\.gameloc" (
  rd /s /q "%USERPROFILE%\.gameloc"
  echo    지웠습니다: %USERPROFILE%\.gameloc
) else (
  echo    없습니다. 넘어갑니다.
)

echo.
echo  [3/3] 함께 깔렸던 라이브러리
echo.
echo    UnityPy, openpyxl, click, TypeTreeGeneratorAPI 를 같이 지울까요?
echo    다른 프로그램이 쓰고 있으면 그쪽이 고장납니다.
echo    잘 모르겠으면 n 을 고르세요. 용량만 조금 차지할 뿐 해롭지 않습니다.
echo.
set /p LIB="같이 지울까요? (y/n): "
if /i "%LIB%"=="y" (
  %PY% -m pip uninstall -y UnityPy openpyxl click TypeTreeGeneratorAPI
) else (
  echo    그대로 둡니다.
)

echo.
echo ============================================
echo   제거 끝
echo.
echo   이제 이 폴더를 통째로 지우시면 됩니다:
echo   %~dp0
echo.
echo   번역했던 게임을 원래대로 되돌리려면, 지우기 전에
echo   2_start.bat 을 켜고 그 게임 폴더에서 [되돌리기] 를
echo   먼저 눌러 주세요.
echo ============================================
pause
