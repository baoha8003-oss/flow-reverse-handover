@echo off
REM Khoi dong Flowboard local: agent (REST 8101 + WS 9223) va frontend (5173).
REM Yeu cau: da chay `npm install` trong frontend\ va tao venv trong agent\.

setlocal
set ROOT=%~dp0

REM Cong phai co dinh: extension duoc bien dich cung ws://127.0.0.1:9223 va
REM callback http://127.0.0.1:8101. Neu agent nhay sang cong khac thi extension
REM khong bao gio ket noi duoc, va loi do rat kho doan ra.
set BUSY=0
for %%P in (8101 9223 5173) do (
  netstat -ano | findstr /C:"LISTENING" | findstr /C:":%%P " >nul 2>&1
  if not errorlevel 1 (
    echo [LOI] Cong %%P dang bi chiem.
    set BUSY=1
  )
)

if "%BUSY%"=="1" (
  echo.
  echo Dong cac cua so flowboard cu roi chay lai file nay.
  echo Xem ai dang giu cong:   netstat -ano ^| findstr :8101
  echo Tat theo PID:           taskkill /PID ^<pid^> /F
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo  FLOWBOARD LOCAL
echo  Agent    : http://127.0.0.1:8101   (WebSocket 9223)
echo  Canvas   : http://localhost:5173
echo ============================================================
echo.
echo  TRUOC KHI DUNG, LAM 1 LAN:
echo   1. chrome://extensions  -^>  bat Developer mode
echo   2. Load unpacked  -^>  chon thu muc:
echo      %ROOT%extension
echo   3. Mo https://flow.google.com va dang nhap, DE TAB DO MO
echo      (Google da doi sang host nay; moi lenh duoc ky ngay trong tab,
echo       khong con token nao de doi - dong tab la agent khong goi duoc)
echo   4. Bam Kiem tra tab Flow trong popup extension neu muon xac nhan
echo.

start "flowboard-agent" cmd /k "cd /d %ROOT%agent && .venv\Scripts\python.exe -m uvicorn flowboard.main:app --host 127.0.0.1 --port 8101 --timeout-graceful-shutdown 2"
start "flowboard-frontend" cmd /k "cd /d %ROOT%frontend && npm run dev -- --strictPort"

echo Da khoi dong 2 cua so. Dong cua so de tat tien trinh.
echo Mo canvas: http://localhost:5173
endlocal
