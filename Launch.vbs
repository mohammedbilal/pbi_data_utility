' Launch.vbs — double-click to start PBI Test Utility with no visible windows.
' Runs Start-App.ps1 via PowerShell completely hidden.

Dim oShell, scriptDir, psCmd

Set oShell = CreateObject("WScript.Shell")

' Resolve the directory this .vbs file lives in
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)

psCmd = "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -NonInteractive" & _
        " -File """ & scriptDir & "\Start-App.ps1"""

' Run hidden (second arg 0 = hidden window, third arg False = don't wait)
oShell.Run psCmd, 0, False

Set oShell = Nothing
