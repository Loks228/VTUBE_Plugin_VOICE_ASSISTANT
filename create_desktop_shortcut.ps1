$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $root "start_voice_assistant.cmd"
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "VTube Voice Assistant.lnk"
$pythonIcon = Join-Path $env:LOCALAPPDATA "VTubeVoiceAssistant\venv\Scripts\python.exe"
if (-not (Test-Path $pythonIcon)) {
    $pythonIcon = Join-Path $root ".venv\Scripts\python.exe"
}

$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($shortcutPath)
$sc.TargetPath = $launcher
$sc.WorkingDirectory = $root
$sc.WindowStyle = 1
if (Test-Path $pythonIcon) {
    $sc.IconLocation = "$pythonIcon,0"
}
$sc.Description = "VTube voice assistant (venv + dependencies)"
$sc.Save()

Write-Host "Created: $shortcutPath"
