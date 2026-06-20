# deploy-win.ps1 — Windows bootstrap for devtool
# Downloads install.py from GitHub and runs it.
# install.py handles everything else: install, update, uninstall, run.

$ErrorActionPreference = "Stop"

Write-Host "[devtool] Windows" -ForegroundColor Cyan

$py = $null
foreach ($c in @("py", "python", "python3")) {
    $found = Get-Command $c -ErrorAction SilentlyContinue
    if ($found) { $py = $c; break }
}
if (-not $py) {
    Write-Host "[!] Python not found." -ForegroundColor Red
    Write-Host "    https://www.python.org/downloads/" -ForegroundColor Gray
    Write-Host "    During setup, check 'Add Python to PATH'." -ForegroundColor Gray
    exit 1
}
Write-Host "[+] Python: $((Get-Command $py).Source)"

$url = "https://raw.githubusercontent.com/jimprivate/devtool/master/install.py"
$tmp = "$env:TEMP\devtool-install.py"
Write-Host "[+] Fetching install.py..."
Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
& $py $tmp
Remove-Item $tmp -ErrorAction SilentlyContinue

Write-Host "[devtool] Done." -ForegroundColor Cyan
