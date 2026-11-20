@echo off
chcp 65001 >nul
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath(Startup)+\Pico Keyboard.lnk); $s.TargetPath=%~dp0start_hidden.vbs; $s.WorkingDirectory=%~dp0; $s.Save()"
echo Готово: клавиатура будет запускаться вместе с Windows.
start "" "%~dp0start_hidden.vbs"
pause
