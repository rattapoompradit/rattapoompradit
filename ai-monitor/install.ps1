# AI Monitor installer for Windows (PowerShell 5.1+).
# One-liner:  irm https://raw.githubusercontent.com/rattapoompradit/rattapoompradit/claude/hopeful-faraday-7c9bsw/ai-monitor/install.ps1 | iex
& {
$ErrorActionPreference = "Stop"
$Branch = "claude/hopeful-faraday-7c9bsw"
$ZipUrl = "https://github.com/rattapoompradit/rattapoompradit/archive/refs/heads/$Branch.zip"
$Dest = Join-Path $HOME "ai-monitor"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "  [OK]  $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!!]  $msg" -ForegroundColor Yellow }

# ---------- 1. Python ----------
Step "Checking Python 3.10+"
function Find-Python {
    foreach ($c in @(@{ Exe = "py"; Args = @("-3") }, @{ Exe = "python"; Args = @() })) {
        try {
            $a = $c.Args
            $v = & $c.Exe @a -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and [version]"$v" -ge [version]"3.10") { return $c }
        } catch {}
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    Warn "Python not found - installing Python 3.12 with winget"
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $py = Find-Python
    if (-not $py) { throw "Python install failed. Install Python 3.12 from https://www.python.org and run this again." }
}
$pyArgs = $py.Args
Ok ("Python " + (& $py.Exe @pyArgs --version))

# ---------- 2. Download (keeps your .env and config.yaml) ----------
Step "Downloading AI Monitor to $Dest"
$tmp = Join-Path $env:TEMP ("ai-monitor-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
Invoke-WebRequest $ZipUrl -OutFile "$tmp\src.zip" -UseBasicParsing
Expand-Archive "$tmp\src.zip" -DestinationPath $tmp
$src = Get-ChildItem $tmp -Directory | Select-Object -First 1 | ForEach-Object { Join-Path $_.FullName "ai-monitor" }
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$keep = @(".env", "config.yaml") | Where-Object { Test-Path (Join-Path $Dest $_) }
Get-ChildItem $src -Force | Where-Object { $keep -notcontains $_.Name } | Copy-Item -Destination $Dest -Recurse -Force
Remove-Item $tmp -Recurse -Force
if ($keep) { Ok ("Kept your existing: " + ($keep -join ", ")) }
Ok "Files ready"

# ---------- 3. Virtual env + packages ----------
Step "Installing Python packages"
Set-Location $Dest
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $py.Exe @pyArgs -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
Set-Content ".venv\.deps-ok" "ok"
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
Ok "Packages installed"

# ---------- 4. Find the Xeneon Edge (ultra-wide 32:9 screen) ----------
Step "Looking for the Xeneon Edge screen"
Add-Type -AssemblyName System.Windows.Forms
$screens = [System.Windows.Forms.Screen]::AllScreens
foreach ($s in $screens) { Write-Host ("  screen {0}: {1}x{2} at ({3},{4}){5}" -f $s.DeviceName, $s.Bounds.Width, $s.Bounds.Height, $s.Bounds.X, $s.Bounds.Y, $(if ($s.Primary) { " primary" } else { "" })) }
$edge = $screens | Where-Object { $_.Bounds.Width / $_.Bounds.Height -ge 3 } | Select-Object -First 1
if ($edge) {
    (Get-Content run.bat) -replace '^set EDGE_X=.*', "set EDGE_X=$($edge.Bounds.X)" -replace '^set EDGE_Y=.*', "set EDGE_Y=$($edge.Bounds.Y)" | Set-Content run.bat
    Ok ("Xeneon Edge found at ({0},{1}) - run.bat updated" -f $edge.Bounds.X, $edge.Bounds.Y)
} else {
    Warn "No 32:9 screen found. Edit EDGE_X / EDGE_Y in run.bat later (see README)."
}

# ---------- 5. Check what will be monitored ----------
Step "Checking your setup"
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi) { Ok ("GPU: " + ((& nvidia-smi --query-gpu=name,memory.total --format=csv,noheader) -join "; ")) } else { Warn "nvidia-smi not found (GPU card will show STANDBY)" }

try {
    $tags = Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3
    Ok ("Ollama models: " + (($tags.models | ForEach-Object { $_.name }) -join ", "))
    Write-Host "        -> make sure model_hint for Sparkx / Qwen in config.yaml matches one of these names"
} catch { Warn "Ollama is not running on http://localhost:11434" }

$hermes = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA "hermes" }
if (Test-Path $hermes) { Ok "Hermes home: $hermes" } else { Warn "Hermes home not found at $hermes (set 'home:' in config.yaml)" }
if (Test-Path "$HOME\.claude\.credentials.json") { Ok "Claude Code login found (Claude usage bars)" } else { Warn "Claude Code login not found - run 'claude' and log in for usage bars" }
if (Test-Path "$HOME\.codex\auth.json") { Ok "Codex CLI login found (ChatGPT usage bars)" } else { Warn "Codex CLI login not found - run 'codex login' for ChatGPT usage bars" }
$envText = Get-Content ".env" -Raw
foreach ($k in "MIMO_API_KEY", "ZAI_API_KEY", "NVIDIA_API_KEY") {
    if ($envText -match "(?m)^$k=\S+") { Ok "$k set" } else { Warn "$k empty in $Dest\.env" }
}

# ---------- 6. Start with Windows ----------
Step "Auto-start"
$answer = Read-Host "  Start AI Monitor automatically when Windows starts? (y/N)"
if ($answer -match '^[yY]') {
    $lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "AI Monitor.lnk"
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $sc.TargetPath = Join-Path $Dest "run.bat"
    $sc.WorkingDirectory = $Dest
    $sc.WindowStyle = 7
    $sc.Save()
    Ok "Added to Startup"
}

Step "Done"
Write-Host "  Folder : $Dest"
Write-Host "  Edit   : .env (API keys), config.yaml (model names, thresholds)"
Write-Host "  Demo   : $Dest\run.bat --demo"
Write-Host "  Real   : $Dest\run.bat"
$answer = Read-Host "`n  Launch now with real data? (Y/n)"
if ($answer -notmatch '^[nN]') { Start-Process -FilePath (Join-Path $Dest "run.bat") -WorkingDirectory $Dest }
}
