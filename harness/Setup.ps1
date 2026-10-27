# Setup.ps1 - choose how this folder starts Colonization on Windows: emulator,
# default language, CD music and its volume, fullscreen or window. Saves the
# answers in settings-windows.txt, which Play.ps1, Play.cmd, Romana.cmd and
# English.cmd read. Started by Setup.cmd.
#   Setup.cmd             # ask; Enter keeps the value shown in [ ]
#   Setup.cmd -Reset      # forget the saved answers, then ask again
# Linux twin: setup.sh, which keeps settings-linux.txt.
# Nothing is downloaded unless "Download DOSBox Staging" is chosen and confirmed.

param(
    [switch]$Reset,
    [switch]$FromPlay   # started by Play.ps1: no "start now?" at the end
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$mps = Join-Path $here 'MPS'
$settingsFile = Join-Path $here 'settings-windows.txt'

if ($Reset -and (Test-Path -LiteralPath $settingsFile)) {
    Remove-Item -LiteralPath $settingsFile
    Write-Host 'Saved answers removed.'
}
$cur = @{}
if (Test-Path -LiteralPath $settingsFile) {
    foreach ($line in Get-Content -LiteralPath $settingsFile) {
        if ($line -match '^([a-z_]+)=(.*)$') { $cur[$Matches[1]] = $Matches[2].Trim() }
    }
}

function Ask($prompt) {
    $a = Read-Host $prompt
    if ($null -eq $a) { Write-Host ''; Write-Host 'No answer, stopped.'; exit 1 }
    $a.Trim()
}
# Choose TITLE DEFAULT LABELS -> 0-based index
function Choose($title, [int]$default, [string[]]$labels) {
    Write-Host ''
    Write-Host $title
    for ($i = 0; $i -lt $labels.Count; $i++) { Write-Host "  $($i + 1)) $($labels[$i])" }
    while ($true) {
        $a = Ask "Choice [$($default + 1)]"
        if ($a -eq '') { return $default }
        $n = 0
        if ([int]::TryParse($a, [ref]$n) -and $n -ge 1 -and $n -le $labels.Count) { return $n - 1 }
        Write-Host "  Type a number from 1 to $($labels.Count)."
    }
}
function Confirm-Answer($question, [bool]$default) {
    $hint = if ($default) { 'Y/n' } else { 'y/N' }
    $a = Read-Host "$question [$hint]"
    if ($null -eq $a -or $a.Trim() -eq '') { return $default }
    $a.Trim() -match '^[YyDd]'
}
function Index-Of($values, $value) {
    for ($i = 0; $i -lt $values.Count; $i++) { if ($values[$i] -eq $value) { return $i } }
    0
}

# --- emulators found on this system
function Find-Staging {
    $dirs = @()
    $local = Join-Path $here 'emulators\staging-windows'
    if (Test-Path -LiteralPath $local) {
        $dirs += Get-ChildItem -LiteralPath $local -Directory | Sort-Object Name -Descending | ForEach-Object { $_.FullName }
    }
    foreach ($base in @($env:ProgramFiles, $env:LOCALAPPDATA)) {
        if ($base) { $dirs += (Join-Path $base 'DOSBox Staging'); $dirs += (Join-Path $base 'Programs\DOSBox Staging') }
    }
    foreach ($d in $dirs) {
        $exe = Join-Path $d 'dosbox.exe'
        if (Test-Path -LiteralPath $exe) { return $exe }
    }
    $null
}
function Find-DosboxX {
    $cands = @('C:\DOSBox-X\dosbox-x.exe')
    if ($env:ProgramFiles) { $cands += (Join-Path $env:ProgramFiles 'DOSBox-X\dosbox-x.exe') }
    foreach ($c in $cands) { if (Test-Path -LiteralPath $c) { return $c } }
    foreach ($n in 'dosbox-x.exe', 'dosbox-x') {
        $c = Get-Command $n -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($c) { return $c.Source }
    }
    $null
}
function Get-Staging {
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        $rel = Invoke-RestMethod -UseBasicParsing 'https://api.github.com/repos/dosbox-staging/dosbox-staging/releases/latest'
        $asset = $rel.assets | Where-Object { $_.name -match 'windows-x64' -and $_.name -like '*.zip' } | Select-Object -First 1
        if (-not $asset) { Write-Host '  No Windows zip in the latest release.'; return }
        if ("$($asset.digest)" -notlike 'sha256:*') { Write-Host '  The release gives no checksum; not downloaded.'; return }
        $mb = [math]::Round($asset.size / 1MB)
        if (-not (Confirm-Answer "  Download $($asset.name) ($mb MB) from github.com?" $false)) { return }
        $tmp = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName())
        New-Item -ItemType Directory -Path $tmp | Out-Null
        try {
            $zip = Join-Path $tmp $asset.name
            Write-Host '  Downloading ...'
            Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $zip
            $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLower()
            if ($hash -ne $asset.digest.Substring(7).ToLower()) { Write-Host '  Checksum does not match; deleted.'; return }
            $dest = Join-Path $here 'emulators\staging-windows'
            New-Item -ItemType Directory -Force -Path $dest | Out-Null
            Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force
            Write-Host '  Checksum OK; unpacked into emulators\staging-windows.'
        } finally { Remove-Item -Recurse -Force -LiteralPath $tmp }
    } catch { Write-Host "  Download failed: $($_.Exception.Message)" }
}

$staging = Find-Staging
$xexe = Find-DosboxX
$curEmu = $cur['emulator']
while ($true) {
    $labels = @(); $values = @(); $paths = @()
    # Daum is kept in daum\; a new play folder has none
    if (Test-Path -LiteralPath (Join-Path $here 'daum\dosbox.exe')) {
        $labels += 'DOSBox-Daum (the build in daum\)'; $values += 'daum'; $paths += ''
    }
    if ($staging) { $labels += "DOSBox Staging ($staging)"; $values += 'staging'; $paths += $staging }
    if ($xexe) { $labels += "DOSBox-X ($xexe)"; $values += 'x'; $paths += $xexe }
    if (-not $staging) { $labels += 'Download DOSBox Staging from GitHub'; $values += 'download'; $paths += '' }
    $i = Choose 'Emulator:' (Index-Of $values $curEmu) $labels
    if ($values[$i] -eq 'download') { Get-Staging; $staging = Find-Staging; $curEmu = 'staging'; continue }
    $emu = $values[$i]; $emuPath = $paths[$i]
    break
}

$labels = @(); $values = @()
if (Test-Path -LiteralPath (Join-Path $mps 'ROMANA\VICEROY.EXE')) { $labels += 'Romanian (MPS\ROMANA)'; $values += 'ROMANA' }
if (Test-Path -LiteralPath (Join-Path $mps 'COLONIZE\VICEROY.EXE')) { $labels += 'English (MPS\COLONIZE)'; $values += 'COLONIZE' }
if ($values.Count -eq 0) { Write-Host "No game in $mps."; exit 1 }
$language = $values[(Choose 'Default language (Romana.cmd and English.cmd always start their own):' (Index-Of $values $cur['language']) $labels)]

$labels = @(); $values = @()
if (Test-Path -LiteralPath (Join-Path $mps 'CD\Colonize.cue')) { $labels += 'CD music from the disc image (MPS\CD\Colonize.cue)'; $values += 'image' }
if (Test-Path -LiteralPath (Join-Path $mps 'CD\mp3\Colonize.cue')) { $labels += 'CD music from the MP3 set (MPS\CD\mp3)'; $values += 'mp3' }
$labels += "No CD music: the game's own FM music"; $values += 'off'
$cdmusic = $values[(Choose 'Music:' (Index-Of $values $cur['cdmusic']) $labels)]

$volume = if ($cur['music_volume']) { $cur['music_volume'] } else { '100' }
if ($cdmusic -ne 'off') {
    Write-Host ''
    Write-Host 'CD music volume, 0-100 (only the CD tracks; game sounds keep theirs).'
    while ($true) {
        $a = Ask "Volume [$volume]"
        if ($a -eq '') { $a = $volume }
        $n = 0
        if ([int]::TryParse($a, [ref]$n) -and $n -ge 0 -and $n -le 100) { $volume = "$n"; break }
        Write-Host '  Type a number from 0 to 100.'
    }
}

$window = Choose 'Screen:' $(if ($cur['window'] -eq '1') { 1 } else { 0 }) @('Fullscreen', 'Window')

Write-Host ''
Write-Host "Emulator : $emu$(if ($emuPath) { " ($emuPath)" })"
Write-Host "Language : $language"
Write-Host "Music    : $cdmusic$(if ($cdmusic -ne 'off') { ", volume $volume" })"
Write-Host "Screen   : $(if ($window -eq 1) { 'window' } else { 'fullscreen' })"
if (-not (Confirm-Answer 'Save these settings?' $true)) { Write-Host 'Not saved.'; exit 1 }
$out = @("# Written by Setup.ps1 on $(Get-Date -Format yyyy-MM-dd). Change: Setup.cmd   Start over: Setup.cmd -Reset",
         "emulator=$emu")
if ($emuPath) { $out += "emulator_path=$emuPath" }
$out += @("language=$language", "cdmusic=$cdmusic", "music_volume=$volume", "window=$window")
Set-Content -LiteralPath $settingsFile -Value $out -Encoding ascii
Write-Host "Saved in $settingsFile."
if ($FromPlay) { exit 0 }
if (Confirm-Answer 'Start the game now?' $true) { & (Join-Path $here 'Play.ps1') }
