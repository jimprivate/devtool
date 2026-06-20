# deploy-win.ps1 — Windows bootstrap trigger for w11-tools
# Responsibility: find Python, hand off to bootstrap.py on GitHub.
# Change frequency: NEVER (static, lives on your own server)
# bootstrap logic lives at: github.com/jimprivate/tools/main/bootstrap.py

$ErrorActionPreference = "Stop"

Write-Host "[w11-tools] Windows" -ForegroundColor Cyan

# Find Python — try common names in order
$py = $null
foreach ($c in @("py", "python", "python3")) {
    $found = Get-Command $c -ErrorAction SilentlyContinue
    if ($found) { $py = $c; break }
}
if (-not $py) {
    Write-Host "[!] Python not found." -ForegroundColor Red
    Write-Host "    Install from https://www.python.org/downloads/"
    Write-Host "    During setup, check 'Add Python to PATH'."
    exit 1
}
Write-Host "[+] Python: $((Get-Command $py).Source)"

# Download and run bootstrap from GitHub
$url = "https://raw.githubusercontent.com/jimprivate/devtool/main/bootstrap.py"
$tmp = "$env:TEMP\w11-bootstrap.py"
Write-Host "[+] Fetching bootstrap from GitHub..."
Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
& $py $tmp
Remove-Item $tmp -ErrorAction SilentlyContinue

Write-Host "[w11-tools] Done." -ForegroundColor Cyan
