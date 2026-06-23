' Stop-App.vbs — double-click to stop PBI Test Utility servers.

Dim oShell, ans
Set oShell = CreateObject("WScript.Shell")

ans = MsgBox("Stop the PBI Test Utility?" & vbCrLf & vbCrLf & _
             "This will shut down the backend and frontend servers.", _
             vbYesNo + vbQuestion, "PBI Test Utility")

If ans <> vbYes Then WScript.Quit

' Kill uvicorn (backend)
oShell.Run "taskkill /f /fi ""IMAGENAME eq uvicorn.exe""", 0, True

' Kill the node process running Vite (frontend)
' Vite runs as node.exe — we target only the one on port 5173 via netstat
Dim psCmd
psCmd = "powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -Command """ & _
        "$p = netstat -ano | Select-String ':5173 ' | ForEach-Object { ($_ -split '\s+')[-1] } | Select-Object -First 1;" & _
        "if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }"""

oShell.Run psCmd, 0, True

MsgBox "Servers stopped.", vbInformation, "PBI Test Utility"

Set oShell = Nothing
