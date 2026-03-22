$WshShell = New-Object -comObject WScript.Shell
$Desktop = [Environment]::GetFolderPath('Desktop')
$Shortcut = $WshShell.CreateShortcut("$Desktop\GYM Getter.lnk")
$Shortcut.TargetPath = 'powershell.exe'
$Shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "C:\dev_app\GYM_getter\start.ps1"'
$Shortcut.WorkingDirectory = 'C:\dev_app\GYM_getter'
$Shortcut.Description = 'GYM Getter'
$Shortcut.IconLocation = 'shell32.dll,25'
$Shortcut.Save()
Write-Host "Shortcut updated at $Desktop\GYM Getter.lnk"
