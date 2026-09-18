' Double-click launcher: opens console and runs start_voice_assistant.cmd with venv on PATH.
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = root
shell.Run "cmd.exe /c """ & root & "\start_voice_assistant.cmd""", 1, False
