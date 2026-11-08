# Downloads the latest AivisSpeech Engine (Windows x64) into .\AivisSpeech-Engine
# Called by build.bat. Safe to re-run: it skips anything already done.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'   # makes Invoke-WebRequest much faster

$root = $PSScriptRoot
$dest = Join-Path $root 'AivisSpeech-Engine'
$tmp  = Join-Path $root '_engine_download'

if (Test-Path (Join-Path $dest 'run.exe')) {
    Write-Host 'AivisSpeech Engine is already in this folder - skipping download.'
    exit 0
}

Write-Host 'Looking up the latest AivisSpeech Engine release...'
$rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/Aivis-Project/AivisSpeech-Engine/releases/latest' `
    -Headers @{ 'User-Agent' = 'VRChatTTSChat-build' }

# Release files are split 7z volumes: AivisSpeech-Engine-Windows-x64-<version>.7z.001, .002, ...
$assets = @($rel.assets | Where-Object { $_.name -match '^AivisSpeech-Engine-Windows-x64-.*\.7z\.\d+$' } | Sort-Object name)
if ($assets.Count -eq 0) {
    $names = ($rel.assets | ForEach-Object { $_.name }) -join ', '
    throw "No Windows engine archive found in release $($rel.tag_name). Files in that release: $names"
}
$totalMB = [math]::Round((($assets | Measure-Object -Property size -Sum).Sum) / 1MB)
Write-Host "Release $($rel.tag_name): $($assets.Count) file(s), about $totalMB MB in total."

New-Item -ItemType Directory -Force $tmp | Out-Null
foreach ($a in $assets) {
    $out = Join-Path $tmp $a.name
    if ((Test-Path $out) -and ((Get-Item $out).Length -eq $a.size)) { continue }   # resume after a failed run
    Write-Host "Downloading $($a.name) ..."
    Invoke-WebRequest -Uri $a.browser_download_url -OutFile $out -UseBasicParsing
    if ((Get-Item $out).Length -ne $a.size) { throw "Incomplete download: $($a.name)" }
}

# Find 7-Zip, or fetch the small standalone 7zr.exe (it can open split .7z archives)
$sevenZ = $null
$cmd = Get-Command 7z.exe -ErrorAction SilentlyContinue
if ($cmd) { $sevenZ = $cmd.Source }
if (-not $sevenZ) {
    foreach ($p in @("$env:ProgramFiles\7-Zip\7z.exe", "${env:ProgramFiles(x86)}\7-Zip\7z.exe")) {
        if (Test-Path $p) { $sevenZ = $p; break }
    }
}
if (-not $sevenZ) {
    Write-Host 'Downloading 7zr.exe (standalone 7-Zip extractor)...'
    $sevenZ = Join-Path $tmp '7zr.exe'
    Invoke-WebRequest -Uri 'https://www.7-zip.org/a/7zr.exe' -OutFile $sevenZ -UseBasicParsing
}

Write-Host 'Extracting (this can take a few minutes)...'
$ex = Join-Path $tmp 'extracted'
if (Test-Path $ex) { Remove-Item -Recurse -Force $ex }
& $sevenZ x -y "-o$ex" (Join-Path $tmp $assets[0].name) | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Extraction failed (7-Zip exit code $LASTEXITCODE)." }

$exe = Get-ChildItem -Path $ex -Recurse -Filter run.exe | Select-Object -First 1
if (-not $exe) { throw 'Extraction finished but run.exe was not found.' }
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
Move-Item -Path $exe.Directory.FullName -Destination $dest

Remove-Item -Recurse -Force $tmp
Write-Host "AivisSpeech Engine $($rel.tag_name) is ready in: $dest"
