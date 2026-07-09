Set objShell = CreateObject("WScript.Shell")

' Change to your project directory
projectPath = "C:\Users\pcuser\OneDrive\Desktop\POS generator"

' Build the PowerShell command
cmd = "powershell.exe -NoExit -Command " & Chr(34) & _
      "Set-ExecutionPolicy Unrestricted -Scope CurrentUser -Force; " & _
      "Set-Location '" & projectPath & "'; " & _
      ".\pos_generator\Scripts\Activate.ps1; " & _
      ".\Django-Back-Front\start.ps1; " & _
      "Start-Sleep -Seconds 5; " & _
      "Start-Process 'http://localhost:8000'" & _
      Chr(34)

' Run PowerShell
objShell.Run cmd, 1, False