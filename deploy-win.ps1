# deploy-win.ps1 — Windows bootstrap trigger for devtool
# Responsibility: find Python, hand off to install.py on GitHub.
# Change frequency: NEVER (static, lives on your own server)
# install logic lives at: github.com/jimprivate/devtool/master/install.py

$ErrorActionPreference = "Stop"

Write-Host "[devtool] Windows" -ForegroundColor Cyan

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
$url = "https://raw.githubusercontent.com/jimprivate/devtool/master/install.py"
$tmp = "$env:TEMP\devtool-install.py"
Write-Host "[+] Fetching install.py from GitHub..."
Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
& $py $tmp
Remove-Item $tmp -ErrorAction SilentlyContinue

Write-Host "[devtool] Done." -ForegroundColor Cyan
