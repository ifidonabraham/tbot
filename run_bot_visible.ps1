Set-Location -LiteralPath "C:\TradingBot"
Add-Content -Path "C:\TradingBot\data\runner_marker.log" -Value ("started " + (Get-Date -Format o))
& "C:\TradingBot\venv\Scripts\python.exe" -u "C:\TradingBot\bot.py"
