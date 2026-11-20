@echo off
chcp 65001 >nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Pico Keyboard.lnk" 2>nul
echo Автозапуск отключён.
pause
