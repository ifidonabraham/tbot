Set-Location -LiteralPath "C:\TradingBot"

$logDir = "C:\TradingBot\data"
$watchdogLog = Join-Path $logDir "bot_watchdog.log"
$processLog = Join-Path $logDir "bot_process.log"
$pythonExe = "C:\TradingBot\venv\Scripts\python.exe"
$botScript = "C:\TradingBot\bot.py"

if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

while ($true) {
    "$(Get-Date -Format o) starting bot.py" | Add-Content -LiteralPath $watchdogLog
    & $pythonExe $botScript *>> $processLog
    "$(Get-Date -Format o) bot.py exited with code $LASTEXITCODE" | Add-Content -LiteralPath $watchdogLog
    Start-Sleep -Seconds 5
}
