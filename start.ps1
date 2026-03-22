Set-Location C:\dev_app\GYM_getter

$python = 'C:\Users\tom44\AppData\Local\Programs\Python\Python313\python.exe'

Write-Host "Starting GYM Getter server..."

# Start server in a new window
Start-Process $python -ArgumentList 'run.py' -WorkingDirectory 'C:\dev_app\GYM_getter'

# Wait until port 8000 is open (max 30s)
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    $conn = Test-NetConnection -ComputerName localhost -Port 8000 -WarningAction SilentlyContinue
    if ($conn.TcpTestSucceeded) {
        $ready = $true
        break
    }
    Write-Host "Waiting... ($($i+1)s)"
}

if ($ready) {
    Write-Host "Server ready. Opening browser..."
    Start-Process 'http://localhost:8000'
} else {
    Write-Host "Server did not start. Check the server window for errors."
}
