# Play.ps1 - start Colonization from this folder. Windows twin of
# harness/play.sh; harness/deploy-play.sh copies both next to daum\ and MPS\.
# The emulator, default language, CD music and window come from
# settings-windows.txt, written by Setup.ps1 (Setup.cmd). With no such file
# Setup runs first; started without a console, DOSBox-Daum, Romanian, CD image,
# fullscreen. Options override the settings for one start:
#   .\Play.ps1                     # the default language from the settings
#   .\Play.ps1 COLONIZE            # English (ROMANA: Romanian)
#   .\Play.ps1 ROMANA -Window -KeepOpen
#   .\Play.ps1 -Mp3                # CD music from the MP3 set instead of the CD image
#   .\Play.ps1 -MusicVolume 60     # CD music at 60 % (effects and FM music unchanged)
# The CD drive D: is MPS\CD\Colonize.cue (the disc image), or MPS\CD\mp3\Colonize.cue
# (the same disc, audio tracks as MP3 files). The CD shim (PSOUND.COL, installed
# by deploy-play.sh) plays the music from it; with no CD the game plays FM.
# Emulators: daum = DOSBox-Daum (daum\dosbox.exe, settings from daum\dosbox.conf
# before [autoexec]); staging = DOSBox Staging; x = DOSBox-X. Staging and X get
# the settings below, chosen to look and sound like Daum.
# The config is written to daum\run-<FOLDER>[-staging|-x].conf.

param(
    [string]$Folder = '',
    [switch]$Window,     # windowed instead of fullscreen
    [switch]$KeepOpen,   # DOSBox stays open after the game ends, so error text stays readable
    [switch]$Mp3,        # mount the MP3 set instead of the CD image
    [ValidateRange(0, 100)]
    [int]$MusicVolume = -1   # 0-100: CD tracks only
)

$ErrorActionPreference = 'Stop'

$mps  = Join-Path $PSScriptRoot 'MPS'
$daum = Join-Path $PSScriptRoot 'daum'
$settingsFile = Join-Path $PSScriptRoot 'settings-windows.txt'

# no settings, or no emulator chosen yet (the installer writes only language and cdmusic)
$chosen = (Test-Path -LiteralPath $settingsFile) -and (Select-String -LiteralPath $settingsFile -Pattern '^emulator=' -Quiet)
if (-not $chosen -and [Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
    & (Join-Path $PSScriptRoot 'Setup.ps1') -FromPlay
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
}
# key=value lines, read as data, never run
$s = @{}
if (Test-Path -LiteralPath $settingsFile) {
    foreach ($line in Get-Content -LiteralPath $settingsFile) {
        if ($line -match '^([a-z_]+)=(.*)$') { $s[$Matches[1]] = $Matches[2].Trim() }
    }
}
function Setting($key, $default) { if ($s.ContainsKey($key) -and $s[$key] -ne '') { $s[$key] } else { $default } }
$emu      = Setting 'emulator' 'daum'
$emuPath  = Setting 'emulator_path' ''
$cdmusic  = Setting 'cdmusic' 'image'
$volume   = [int](Setting 'music_volume' '100')
$win      = (Setting 'window' '0') -eq '1'
if ($Folder -eq '') { $Folder = Setting 'language' 'ROMANA' }
if ($Window) { $win = $true }
if ($Mp3) { $cdmusic = 'mp3' }
if ($MusicVolume -ge 0) { $volume = $MusicVolume }

if ('daum', 'staging', 'x' -notcontains $emu) { throw "emulator '$emu' in ${settingsFile}: daum, staging or x" }
switch ($cdmusic) {
    'image' { $cue = Join-Path $mps 'CD\Colonize.cue' }
    'mp3'   { $cue = Join-Path $mps 'CD\mp3\Colonize.cue' }
    'off'   { $cue = '' }
    default { throw "cdmusic '$cdmusic' in ${settingsFile}: image, mp3 or off" }
}
if ($Folder -notmatch '^[A-Za-z0-9_-]{1,8}$') { throw "'$Folder' is not a DOS 8.3 name" }
if (-not (Test-Path -LiteralPath (Join-Path $mps "$Folder\VICEROY.EXE"))) { throw "No VICEROY.EXE in $mps\$Folder" }
if ($cue -and -not (Test-Path -LiteralPath $cue)) { throw "No $cue" }
if ($volume -lt 0 -or $volume -gt 100) { throw "music volume takes 0-100" }

# CD music volume, per emulator (measured 2026-10-25): Daum honours the shim's
# CDVOL; Staging ignores it and takes MIXER CDAUDIO; DOSBox-X reads CDVOL as
# n/64, so CDVOL = p/4 gives p %. Nothing at 100 or with no CD.
$vol = ''
if ($cue -and $volume -lt 100) {
    switch ($emu) {
        'daum'    { $vol = "SET CDVOL=$volume" }
        'staging' { $vol = "MIXER CDAUDIO ${volume}:$volume" }
        'x'       { $vol = "SET CDVOL=$([math]::Floor(($volume + 2) / 4))" }
    }
}

switch ($emu) {
    'daum' {
        $exe = Join-Path $daum 'dosbox.exe'
        $conf = Join-Path $daum "run-$Folder.conf"
        $settings = @()
        foreach ($line in Get-Content -LiteralPath (Join-Path $daum 'dosbox.conf')) {
            if ($line -match '^\s*\[autoexec\]') { break }
            if ($win -and $line -match '^\s*fullscreen\s*=') { $line = 'fullscreen=false' }
            $settings += $line
        }
        $emuArgs = @('-conf', "`"$conf`"")
    }
    'staging' {
        $exe = $emuPath
        $conf = Join-Path $daum "run-$Folder-staging.conf"
        $fs = if ($win) { 'off' } else { 'on' }
        $settings = @('[sdl]', "fullscreen = $fs",
            '[render]', 'shader = scaler/advinterp3x', 'aspect = off', 'integer_scaling = off',
            'viewport = fit', 'image_adjustments = off',
            '[dosbox]', 'machine = svga_s3', 'memsize = 16',
            '[cpu]', 'core = auto', 'cpu_cycles = 105000', 'cpu_cycles_protected = 105000',
            '[mixer]', 'rate = 44100', 'blocksize = 1024', 'prebuffer = 20', 'negotiate = off',
            '[sblaster]', 'sbtype = sb16', 'sbbase = 220', 'irq = 7', 'dma = 1', 'hdma = 5', 'oplmode = auto',
            '[dos]', 'xms = on', 'ems = on', 'umb = on')
        $emuArgs = @('--noprimaryconf', '--nolocalconf', '--conf', "`"$conf`"")
    }
    'x' {
        $exe = $emuPath
        if (-not $exe) {
            $c = Get-Command dosbox-x.exe, dosbox-x -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($c) { $exe = $c.Source }
        }
        $conf = Join-Path $daum "run-$Folder-x.conf"
        $fs = if ($win) { 'false' } else { 'true' }
        $settings = @('[sdl]', "fullscreen = $fs", 'output = openglnb',
            '[render]', 'scaler = hq3x',
            '# with OpenGL, aspect = false stretches to the screen (16:9); Daum shows 16:10',
            'aspect = true', 'aspect_ratio = 16:10', 'doublescan = true', 'autofit = true',
            '[dosbox]', 'quit warning = false', 'machine = svga_s3', 'memsize = 16',
            '[cpu]', 'core = auto', 'cycles = fixed 105000',
            '[mixer]', 'rate = 44100', 'blocksize = 1024', 'prebuffer = 25',
            '[sblaster]', 'sbtype = sb16', 'sbbase = 220', 'irq = 7', 'dma = 1', 'hdma = 5', 'oplmode = auto',
            '[dos]', 'xms = true', 'ems = true', 'umb = true')
        $emuArgs = @('-defaultconf', '-conf', "`"$conf`"")
    }
}
if (-not $exe -or -not (Test-Path -LiteralPath $exe)) { throw "Emulator '$emu' not found ('$exe'): run Setup.cmd" }

$auto = @('[autoexec]', "mount C `"$mps\`"")
if ($cue) { $auto += "imgmount D `"$cue`" -t iso" }
if ($vol) { $auto += $vol }
$auto += @('C:', "CD $Folder", 'CLS', 'OPENING -g')
if ($cue) { $auto += 'mount -u D' }
if (-not $KeepOpen) { $auto += 'exit' }

Set-Content -LiteralPath $conf -Value ($settings + '' + $auto) -Encoding ascii

Write-Host "Game     : $mps\$Folder"
Write-Host "Emulator : $emu ($exe)"
Write-Host "CD       : $(if ($cue) { $cue } else { 'none (FM music)' })$(if ($vol) { ", $vol" })"
Write-Host "Config   : $conf"
Start-Process -FilePath $exe -ArgumentList $emuArgs -WorkingDirectory (Split-Path -Parent $exe)
