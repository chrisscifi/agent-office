$ErrorActionPreference = "Continue"
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)
$vram = 0
try { $v = & nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>$null; if ($v) { $vram = [math]::Round([int]($v | Select-Object -First 1)/1024) } } catch {}
Write-Host "This laptop: $ram GB RAM, NVIDIA GPU memory: $vram GB"

if ($vram -ge 12 -or $ram -ge 32) { $model = "qwen3:14b"; $size = "9 GB" }
elseif ($vram -ge 6 -or $ram -ge 16) { $model = "qwen3:8b"; $size = "5 GB" }
elseif ($ram -ge 8) { $model = "qwen3:4b"; $size = "2.5 GB" }
else { Write-Host "Under 8 GB RAM: a local model would be too slow. Use the free Groq/Gemini keys instead."; exit }

Write-Host ""
Write-Host "Recommended model: $model (download about $size)."
Write-Host "It is used only as a backup when the free online models hit their daily limits."
$ans = Read-Host "Install Ollama (if needed) and download $model now? (y/n)"
if ($ans -ne "y") { exit }

if (-not (Get-Command ollama -ErrorAction SilentlyContinue) -and -not (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")) {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install --id Ollama.Ollama -e --accept-source-agreements
  } else {
    Write-Host "Please install Ollama from https://ollama.com/download, then run this again."; Start-Process "https://ollama.com/download"; exit
  }
}
$ol = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ol) { $ol = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
& $ol pull $model
$cfg = (Get-Content -Encoding UTF8 config.toml) -replace '^model = ".*"', "model = `"$model`""
[IO.File]::WriteAllLines((Join-Path $PWD "config.toml"), $cfg)  # UTF-8 without BOM
Write-Host "Done. Restart the office; Ollama now shows as connected."
