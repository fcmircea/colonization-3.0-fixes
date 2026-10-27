<#
.SYNOPSIS
Install the Colonization 3.0 fixes into a new play folder.

.DESCRIPTION
PowerShell twin of install.py, for Windows without Python. Same steps, same
folders, same exit codes; tests/test_install.py runs both.
Started by Install.cmd. Works on Windows PowerShell 5.1 and PowerShell 7+.

Your game folder is only read. The installer makes a play folder (default: a
folder "Colonization" next to your game folder) laid out as the launchers
expect:
  MPS\COLONIZE   your game, English, with every recommended fix
  MPS\ROMANA     the same plus the Romanian translation (optional)
  MPS\CD         your CD image, for CD music (optional)
  MPS\CD\mp3     the CD as MP3 files (optional; needs Python 3 and ffmpeg)
  Play.cmd, Setup.cmd, play.sh, ...   the launchers
Run it again to add what you skipped: folders already there are left as they are.

Exit codes: 0 ok, 1 file not found, 2 usage, 3 refused (unknown game files or
an unexpected byte), 4 a step failed.

.EXAMPLE
  Install.cmd
  Install.cmd -Game "D:\Games\MPS\COLONIZE" -Yes
  Install.cmd -Game "D:\Games\MPS\COLONIZE" -Cd "D:\CD\Colonize.cue" -Ro -Yes
#>
[CmdletBinding()]
param(
    [string] $Game = '',
    [string] $Dest = '',
    [string] $Cd = '',
    [switch] $NoCd,
    [switch] $Mp3,
    [switch] $NoMp3,
    [string] $Windows = '',
    [switch] $Ro,
    [switch] $NoRo,
    [string] $RoFiles = '',     # tests: the built text and fonts
    [string] $ShimFile = '',    # tests: a built CD shim
    [string[]] $Patch = @(),
    [switch] $Setup,
    [switch] $NoSetup,
    [switch] $Yes
)

$ErrorActionPreference = 'Stop'
$PristineMd5 = '0f5d5b0063721fbc6aca314e5a43ddaf'
$PsoundMd5 = 'c294733957d12c68fdbbad2b73442f34'       # the original PSOUND.COL
$Launchers = @('play.sh', 'Romana.sh', 'English.sh', 'setup.sh',
               'Play.ps1', 'Play.cmd', 'Romana.cmd', 'English.cmd', 'Setup.ps1', 'Setup.cmd')
# The installer runs from the release zip or from the repo; the parts sit in
# slightly different places in each.
$Root = Split-Path -Parent $PSScriptRoot

function Fail([int] $code, [string] $msg) {
    [Console]::Error.WriteLine("error: $msg")
    exit $code
}

function Get-Part([string[]] $candidates) {
    foreach ($c in $candidates) {
        $p = Join-Path $Root $c
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

function Get-Md5([string] $path) {
    return (Get-FileHash -LiteralPath $path -Algorithm MD5).Hash.ToLower()
}

# --- questions (only asked when the answer was not given on the command line)

function Ask-Yes([string] $question, [bool] $default) {
    if ($Yes) { return $default }
    $hint = if ($default) { 'Y/n' } else { 'y/N' }
    $a = Read-Host "$question [$hint]"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default }
    return 'yd'.Contains($a.Trim().ToLower()[0])
}

function Ask-Path([string] $question, [string] $default) {
    if ($Yes) { return $default }
    $shown = if ($default) { " [$default]" } else { '' }
    $a = Read-Host "$question$shown"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default }
    return $a.Trim().Trim('"')
}

# Its output goes to the screen, not into the caller's return value.
function Invoke-Patcher([string] $exe, [string[]] $ids = @(), [switch] $all, [switch] $revert,
                        [string] $manifest = '', [switch] $mayFail) {
    $script = Get-Part @('patch/Apply-Patch.ps1')
    $a = @{ Exe = $exe }
    if ($ids.Count -gt 0) { $a.Id = $ids }
    if ($all) { $a.All = $true }
    if ($revert) { $a.Revert = $true }
    if ($manifest) { $a.Manifest = $manifest }
    & $script @a | Out-Host
    if ($LASTEXITCODE -ne 0 -and -not $mayFail) {
        Fail 3 "the patcher stopped with exit code $LASTEXITCODE; nothing more was done"
    }
}

# --- steps

# Copy a folder, leaving out the names that match one of $skip.
function Copy-Tree([string] $src, [string] $dst, [string[]] $skip) {
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    foreach ($f in Get-ChildItem -LiteralPath $src -Force) {
        $drop = $false
        foreach ($s in $skip) { if ($f.Name -like $s) { $drop = $true } }
        if ($drop) { continue }
        $to = Join-Path $dst $f.Name
        if ($f.PSIsContainer) { Copy-Tree $f.FullName $to $skip }
        else { Copy-Item -LiteralPath $f.FullName -Destination $to }
    }
}

# The original VICEROY.EXE in the player's folder: the EXE itself, or a backup
# the patcher (or the player) kept.
function Find-PristineExe([string] $game) {
    $names = @('VICEROY.EXE', "VICEROY.EXE.$($PristineMd5.Substring(0, 8)).bak", 'VICEROY.old.exe')
    $names += Get-ChildItem -LiteralPath $game -Filter 'VICEROY.EXE.*.bak' | Sort-Object Name | ForEach-Object { $_.Name }
    foreach ($n in $names) {
        $p = Join-Path $game $n
        if ((Test-Path -LiteralPath $p -PathType Leaf) -and (Get-Md5 $p) -eq $PristineMd5) { return $p }
    }
    return $null
}

function Copy-Game([string] $game, [string] $dst) {
    Copy-Tree $game $dst @('*.bak', 'VICEROY.old.exe', 'PSOUND.FM')
    # a game that already has the CD shim: the copy gets the original driver back
    $col = Join-Path $game 'PSOUND.COL'; $fm = Join-Path $game 'PSOUND.FM'
    if ((Test-Path -LiteralPath $col) -and (Test-Path -LiteralPath $fm) -and (Test-Shim $col) -and (Get-Md5 $fm) -eq $PsoundMd5) {
        Copy-Item -LiteralPath $fm -Destination (Join-Path $dst 'PSOUND.COL') -Force
    }
    $exe = Join-Path $dst 'VICEROY.EXE'
    $src = Find-PristineExe $game
    if ($src) { Copy-Item -LiteralPath $src -Destination $exe -Force; return }
    # No original anywhere: try to undo the fixes this patcher knows.
    Write-Host '  no original VICEROY.EXE found; reverting known fixes on the copy'
    Invoke-Patcher $exe -all -revert -mayFail
    Get-ChildItem -LiteralPath $dst -Filter 'VICEROY.EXE.*.bak' | Remove-Item
    if ((Get-Md5 $exe) -ne $PristineMd5) {
        Remove-Item -LiteralPath $dst -Recurse -Force
        Fail 3 ("$(Join-Path $game 'VICEROY.EXE') is not Colonization 3.0 as this patcher knows it, " +
                "and no original copy is next to it. Put the original VICEROY.EXE " +
                "(md5 $PristineMd5) in that folder and run this again.")
    }
}

function Install-Fixes([string] $game, [string] $dst) {
    if (Test-Path -LiteralPath $dst) { Write-Host "${dst}: already there, left as it is"; return $false }
    Write-Host "${dst}: copying your game"
    Copy-Game $game $dst
    Write-Host 'Fixes:'
    Invoke-Patcher (Join-Path $dst 'VICEROY.EXE') -all
    if ($Patch.Count -gt 0) { Invoke-Patcher (Join-Path $dst 'VICEROY.EXE') -ids $Patch }
    return $true
}

function Install-Romanian([string] $english, [string] $dst, [string] $files) {
    $manifest = Get-Part @('ro/patches-ro.json')
    if (-not $files -or -not (Test-Path -LiteralPath $files -PathType Container) -or -not $manifest) {
        Fail 4 'the Romanian files are missing from this package'
    }
    Write-Host "${dst}: Romanian"
    Copy-Tree $english $dst @('*.bak')
    Get-ChildItem -LiteralPath $files -File | Sort-Object Name | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $dst $_.Name) -Force
    }
    Invoke-Patcher (Join-Path $dst 'VICEROY.EXE') -all -manifest $manifest
}

function Test-Shim([string] $path) {
    $b = [IO.File]::ReadAllBytes($path)
    return $b.Length -ge 0x47 -and [Text.Encoding]::ASCII.GetString($b, 0x40, 7) -eq 'CD shim'
}

# The rules of harness/deploy-play.sh: the original PSOUND.COL becomes
# PSOUND.FM, the shim becomes PSOUND.COL; anything unknown is refused.
function Install-Shim([string] $game, [string] $shim) {
    $col = Join-Path $game 'PSOUND.COL'; $fm = Join-Path $game 'PSOUND.FM'
    if (-not (Test-Path -LiteralPath $col)) { Write-Host "${game}: no PSOUND.COL, CD music skipped"; return }
    $hasFm = Test-Path -LiteralPath $fm
    if ($hasFm -and (Get-Md5 $fm) -ne $PsoundMd5) { Fail 3 "$fm is not the original PSOUND.COL" }
    if ((Get-Md5 $col) -eq $PsoundMd5) {
        if (-not $hasFm) { Copy-Item -LiteralPath $col -Destination $fm }
    } elseif (-not (Test-Shim $col)) {
        Fail 3 "$col is neither the original nor the CD shim"
    } elseif (-not $hasFm) {
        Fail 3 "$col is the shim but PSOUND.FM is missing"
    }
    if ((Get-Md5 $col) -eq (Get-Md5 $shim)) { Write-Host "${game}: CD shim up to date" }
    else { Copy-Item -LiteralPath $shim -Destination $col -Force; Write-Host "${game}: CD shim installed" }
}

# Copy the cue sheet as MPS\CD\Colonize.cue and the files it names.
function Install-Cd([string] $cue, [string] $cd) {
    if (Test-Path -LiteralPath (Join-Path $cd 'Colonize.cue')) { Write-Host "${cd}: already there, left as it is"; return }
    $text = Get-Content -LiteralPath $cue -Raw
    $names = @([regex]::Matches($text, '(?im)^\s*FILE\s+"([^"]+)"') | ForEach-Object { $_.Groups[1].Value })
    if ($names.Count -eq 0) { Fail 3 "${cue}: no FILE line, not a cue sheet" }
    $dir = Split-Path -Parent $cue
    $missing = @($names | Where-Object { -not (Test-Path -LiteralPath (Join-Path $dir $_) -PathType Leaf) })
    if ($missing.Count -gt 0) { Fail 3 "${cue}: missing $($missing -join ', ')" }
    New-Item -ItemType Directory -Path $cd -Force | Out-Null
    foreach ($n in $names) {
        Write-Host "${cd}: copying $n"
        Copy-Item -LiteralPath (Join-Path $dir $n) -Destination (Join-Path $cd $n)
    }
    Copy-Item -LiteralPath $cue -Destination (Join-Path $cd 'Colonize.cue')
}

function Find-Python {
    foreach ($n in @('python3', 'python', 'py')) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
    }
    return $null
}

function Install-Mp3([string] $cd, [string] $windows) {
    $out = Join-Path $cd 'mp3'
    if (Test-Path -LiteralPath (Join-Path $out 'Colonize.cue')) { Write-Host "${out}: already there, left as it is"; return }
    $tool = Get-Part @('cdshim/cue2audio.py')
    $argv = @($tool, (Join-Path $cd 'Colonize.cue'), $out)
    if ($windows) {
        if ((Get-Content -LiteralPath $tool -Raw).Contains('--jingles')) { $argv += @('--jingles', $windows) }
        else { Write-Host '  this package cannot add the jingles yet; MP3 set without them' }
    }
    Write-Host "${out}: making the MP3 set (a few minutes)"
    & (Find-Python) @argv
    if ($LASTEXITCODE -ne 0) { Fail 4 "making the MP3 set failed (exit $LASTEXITCODE)" }
}

function Install-Launchers([string] $dest) {
    $src = Get-Part @('launchers', 'harness')
    foreach ($n in $Launchers) {
        $f = Join-Path $src $n
        if (Test-Path -LiteralPath $f -PathType Leaf) { Copy-Item -LiteralPath $f -Destination (Join-Path $dest $n) -Force }
    }
}

# Set these keys in both settings files, keeping every other line, so the
# launchers start what was just installed; Setup.cmd / setup.sh change them.
function Write-Settings([string] $dest, [System.Collections.Specialized.OrderedDictionary] $values) {
    foreach ($name in @('settings-linux.txt', 'settings-windows.txt')) {
        $p = Join-Path $dest $name
        $lines = @()
        if (Test-Path -LiteralPath $p) {
            $lines = @(Get-Content -LiteralPath $p | Where-Object { -not $values.Contains(($_ -split '=', 2)[0]) })
        }
        foreach ($k in $values.Keys) { $lines += "$k=$($values[$k])" }
        $nl = if ($name -eq 'settings-windows.txt') { "`r`n" } else { "`n" }
        [IO.File]::WriteAllText($p, (($lines -join $nl) + $nl))
    }
}

function Find-Cue([string] $game) {
    $parent = Split-Path -Parent $game
    foreach ($d in @($game, $parent, (Join-Path $parent 'CD'))) {
        if (Test-Path -LiteralPath $d -PathType Container) {
            $c = Get-ChildItem -LiteralPath $d -Filter '*.cue' | Sort-Object Name | Select-Object -First 1
            if ($c) { return $c.FullName }
        }
    }
    return $null
}

# --- main

Write-Host 'Colonization 3.0 fixes: installer'
Write-Host 'Your game folder is only read; everything goes into a new play folder.'
Write-Host ''
if (-not $Game) { $Game = Ask-Path 'Your COLONIZE folder (it holds VICEROY.EXE)' '' }
if (-not $Game) { Fail 2 'no game folder given' }
if (-not (Test-Path -LiteralPath (Join-Path $Game 'VICEROY.EXE') -PathType Leaf)) { Fail 1 "no VICEROY.EXE in $Game" }
$Game = (Resolve-Path -LiteralPath $Game).Path.TrimEnd('\', '/')
if (-not $Dest) { $Dest = Ask-Path 'New play folder' (Join-Path (Split-Path -Parent $Game) 'Colonization') }
$Dest = [IO.Path]::GetFullPath($Dest).TrimEnd('\', '/')
$sep = [IO.Path]::DirectorySeparatorChar
if ($Dest -eq $Game -or $Dest.StartsWith($Game + $sep)) { Fail 2 'the play folder must not be the game folder or inside it' }

$cue = ''
if (-not $NoCd) {
    $cue = $Cd
    if (-not $cue -and -not $Yes) {
        $found = Find-Cue $Game
        if (Ask-Yes 'CD music from your Colonization CD image?' ([bool]$found)) { $cue = Ask-Path 'Its .cue file' $found }
    }
}
if ($cue -and -not (Test-Path -LiteralPath $cue -PathType Leaf)) { Fail 1 "no such file: $cue" }
$haveMp3Tools = (Find-Python) -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)
$doMp3 = $false
if ($cue) {
    if ($Mp3) { $doMp3 = $true }
    elseif (-not $NoMp3 -and $haveMp3Tools) {
        $doMp3 = Ask-Yes 'Also make an MP3 copy of the CD (smoother on slow disks, allows the jingles)?' $false
    }
}
if ($doMp3 -and -not $haveMp3Tools) { Fail 1 'the MP3 set needs Python 3 and ffmpeg, which are not both installed' }
if ($doMp3 -and -not $Windows -and -not $Yes) {
    $Windows = Ask-Path 'Your Windows Colonization folder, for the jingles (Enter: none)' ''
}
$doRo = $false
if ($Ro) { $doRo = $true } elseif (-not $NoRo) { $doRo = Ask-Yes 'Install the Romanian translation too?' $false }

$mps = Join-Path $Dest 'MPS'
$english = Join-Path $mps 'COLONIZE'; $romana = Join-Path $mps 'ROMANA'
$settings = [ordered]@{}
New-Item -ItemType Directory -Path $mps -Force | Out-Null
if (Install-Fixes $Game $english) { $settings['language'] = 'COLONIZE' }
if ($doRo -and -not (Test-Path -LiteralPath $romana)) {
    $files = if ($RoFiles) { $RoFiles } else { Get-Part @('ro/files') }
    Install-Romanian $english $romana $files
    $settings['language'] = 'ROMANA'
}
if ($cue) {
    $settings['cdmusic'] = if ($doMp3) { 'mp3' } else { 'image' }
    $shim = if ($ShimFile) { $ShimFile } else { Get-Part @('cdshim/PSOUND.COL') }
    if (-not $shim) { Fail 4 'the CD shim is missing from this package' }
    foreach ($g in @($english, $romana)) { if (Test-Path -LiteralPath $g) { Install-Shim $g $shim } }
    Install-Cd $cue (Join-Path $mps 'CD')
    if ($doMp3) { Install-Mp3 (Join-Path $mps 'CD') $Windows }
} elseif (-not (Test-Path -LiteralPath (Join-Path $Dest 'settings-windows.txt'))) {
    $settings['cdmusic'] = 'off'
}
Install-Launchers $Dest
if ($settings.Count -gt 0) { Write-Settings $Dest $settings }

Write-Host ''
Write-Host "Done. Play folder: $Dest"
$runSetup = $false
if ($Setup) { $runSetup = $true } elseif (-not $NoSetup) { $runSetup = Ask-Yes 'Choose the emulator and screen now (Setup)?' $true }
$setupScript = Join-Path $Dest 'Setup.ps1'
if ($runSetup -and (Test-Path -LiteralPath $setupScript)) { & $setupScript }
else { Write-Host "Start the game: $(Join-Path $Dest 'Play.cmd')" }
exit 0
