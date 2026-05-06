' Silent launcher for Telegram Bot - no console window
' Used by Task Scheduler to start the bot on logon

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\User\Desktop\SU\telegram_agent"

' Run pythonw (no console) with the bot script, redirect output to log
WshShell.Run "cmd /c ""C:\Users\User\Desktop\SU\telegram_agent\venv\Scripts\python.exe"" ""C:\Users\User\Desktop\SU\telegram_agent\main.py"" > ""C:\Users\User\Desktop\SU\telegram_agent\bot_debug.log"" 2>&1", 0, False
