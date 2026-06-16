@echo off
setlocal
set SCRIPT_DIR=%~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run_import_btb_lc_live_cycle.ps1" -LauncherPath "file_picker" -Config "D:\customs-automation\workflow.toml" -PauseAtEnd
endlocal

