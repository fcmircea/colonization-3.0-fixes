<#
.SYNOPSIS
Binary patches for Sid Meier's Colonization, MS-DOS version 3.0.

.DESCRIPTION
PowerShell version of apply_patch.py, for machines without Python. It reads
patches.json from its own folder and follows the same rules and exit codes.
-Manifest reads another patch list (same format) instead, for example an
optional set kept apart from the bug fixes.
The test suite in tests/ runs against both and checks they behave the same.

Works on Windows PowerShell 5.1 and PowerShell 7+.

Exit codes:
  0 success or nothing to do   1 file not found      2 bad command line
  3 unexpected bytes on disk   5 conflicting patches 6 invalid manifest

.EXAMPLE
  .\Apply-Patch.ps1 "D:\Games\Games\MPS\COLONIZE\VICEROY.EXE" -List
  .\Apply-Patch.ps1 "...\VICEROY.EXE" -Status
  .\Apply-Patch.ps1 "...\VICEROY.EXE" -All
  .\Apply-Patch.ps1 "...\VICEROY.EXE" traderoute-topup
  .\Apply-Patch.ps1 "...\VICEROY.EXE" traderoute-topup -Revert
  .\Apply-Patch.ps1 "...\VICEROY.EXE" -Manifest other.json -List
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][string] $Exe,
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]] $Id = @(),
    [switch] $All,
    [switch] $Revert,
    [switch] $List,
    [Alias('Check')][switch] $Status,
    [switch] $Force,
    [string] $Manifest = ''
)

# Errors are reported with Fail, which sets a real exit code. Do not use
# Write-Error here: with ErrorActionPreference=Stop it throws and every
# failure would exit 1.
$ErrorActionPreference = 'Stop'
$Statuses = @('recommended', 'experimental')

function Fail([int] $code, [string] $msg) {
    [Console]::Error.WriteLine($msg)
    exit $code
}

function ConvertFrom-Hex([string] $hex) {
    if ($hex.Length % 2 -ne 0 -or $hex -notmatch '^[0-9a-fA-F]*$') { throw "bad hex '$hex'" }
    $b = New-Object byte[] ($hex.Length / 2)
    for ($i = 0; $i -lt $b.Length; $i++) { $b[$i] = [Convert]::ToByte($hex.Substring($i * 2, 2), 16) }
    return ,$b
}

function ConvertTo-Hex([byte[]] $bytes) {
    return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Get-Md5([byte[]] $bytes) {
    $h = [System.Security.Cryptography.MD5]::Create()
    try { return ConvertTo-Hex $h.ComputeHash($bytes) } finally { $h.Dispose() }
}

function Get-Site([byte[]] $data, $p) {
    $cur = New-Object byte[] $p._orig.Length
    [Array]::Copy($data, $p._off, $cur, 0, $cur.Length)
    return ,$cur
}

function Get-PatchState([byte[]] $data, $p) {
    $cur = ConvertTo-Hex (Get-Site $data $p)
    if ($cur -eq (ConvertTo-Hex $p._new))  { return 'applied' }
    if ($cur -eq (ConvertTo-Hex $p._orig)) { return 'not applied' }
    return 'UNEXPECTED'
}

function Has-Prop($obj, [string] $name) {
    return $null -ne $obj.PSObject.Properties[$name]
}

function Read-Manifest([string] $path) {
    try { $m = [System.IO.File]::ReadAllText($path) | ConvertFrom-Json }
    catch { throw "cannot read ${path}: $($_.Exception.Message)" }

    if (-not (Has-Prop $m 'schema') -or $m.schema -ne 2) { throw "unsupported schema (expected 2)" }
    foreach ($k in 'name', 'version', 'size', 'md5_pristine') {
        if (-not (Has-Prop $m.target $k)) { throw "target.$k missing" }
    }
    $seen = @{}
    foreach ($p in @($m.patches)) {
        foreach ($k in 'id', 'title', 'status', 'offset', 'original', 'patched',
                       'asm_before', 'asm_after', 'md5_alone', 'summary') {
            if (-not (Has-Prop $p $k)) { throw "patch '$($p.id)': field '$k' missing" }
        }
        if ($seen.ContainsKey($p.id)) { throw "patch id '$($p.id)' appears twice" }
        $seen[$p.id] = $true
        if ($Statuses -notcontains $p.status) { throw "patch '$($p.id)': status '$($p.status)' is not one of $($Statuses -join ', ')" }
        if ($p.offset -notmatch '^0[xX][0-9a-fA-F]+$') { throw "patch '$($p.id)': offset '$($p.offset)' is not a hex string" }
        try {
            $off  = [Convert]::ToInt32($p.offset.Substring(2), 16)
            $orig = ConvertFrom-Hex $p.original
            $new  = ConvertFrom-Hex $p.patched
        } catch { throw "patch '$($p.id)': bad hex value ($($_.Exception.Message))" }
        if ($orig.Length -ne $new.Length -or $orig.Length -eq 0) { throw "patch '$($p.id)': original and patched must be the same non-zero length" }
        if ($off + $orig.Length -gt $m.target.size) { throw "patch '$($p.id)': site is past the end of the target" }
        if (-not (Has-Prop $p 'conflicts')) { $p | Add-Member -NotePropertyName conflicts -NotePropertyValue @() }
        $p | Add-Member -NotePropertyName _off  -NotePropertyValue $off
        $p | Add-Member -NotePropertyName _orig -NotePropertyValue $orig
        $p | Add-Member -NotePropertyName _new  -NotePropertyValue $new
    }
    foreach ($p in @($m.patches)) {
        foreach ($c in @($p.conflicts)) {
            if (-not $seen.ContainsKey($c)) { throw "patch '$($p.id)': conflicts with unknown id '$c'" }
        }
    }
    $sorted = @($m.patches | Sort-Object { $_._off })
    for ($i = 0; $i -lt $sorted.Count - 1; $i++) {
        $a = $sorted[$i]; $b = $sorted[$i + 1]
        if ($b._off -lt $a._off + $a._orig.Length) {
            $declared = (@($a.conflicts) -contains $b.id) -or (@($b.conflicts) -contains $a.id)
            if (-not $declared) { throw "patches '$($a.id)' and '$($b.id)' overlap but do not declare a conflict" }
        }
    }
    return $m
}

function Write-Atomic([string] $path, [byte[]] $data) {
    $dir = [System.IO.Path]::GetDirectoryName($path)
    $tmp = [System.IO.Path]::Combine($dir, [System.IO.Path]::GetFileName($path) + '.' + [Guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $fs = New-Object System.IO.FileStream($tmp, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
        try { $fs.Write($data, 0, $data.Length); $fs.Flush($true) } finally { $fs.Dispose() }
        # [NullString]::Value, not $null: PowerShell turns $null into "" for string arguments.
        [System.IO.File]::Replace($tmp, $path, [NullString]::Value)
    } finally {
        if ([System.IO.File]::Exists($tmp)) { [System.IO.File]::Delete($tmp) }
    }
}

# ---- load manifest -------------------------------------------------------------

# A relative -Manifest resolves against the PowerShell location, like $Exe below.
$ManifestPath = if ($Manifest) { $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Manifest) }
                else { Join-Path $PSScriptRoot 'patches.json' }
try { $man = Read-Manifest $ManifestPath }
catch { Fail 6 "error: invalid $([System.IO.Path]::GetFileName($ManifestPath)): $($_.Exception.Message)" }
$patches = @($man.patches)
$byId = @{}
foreach ($p in $patches) { $byId[$p.id] = $p }

if ($List) {
    Write-Output "Target: $($man.target.version)`n"
    foreach ($p in $patches) {
        Write-Output "  $($p.id)   [$($p.status)]"
        Write-Output "      $($p.title)"
        Write-Output "      $($p.offset): $($p.original) -> $($p.patched)   ($($p.asm_before)  =>  $($p.asm_after))"
        Write-Output "      $($p.summary)"
        if (Has-Prop $p 'caveat') { Write-Output "      CAVEAT: $($p.caveat)" }
        Write-Output ''
    }
    exit 0
}

if ($Force) { [Console]::Error.WriteLine('note: -Force is no longer needed; every patch site is verified byte by byte.') }

# ---- load target ---------------------------------------------------------------
# Resolve against the PowerShell location, then use only the absolute path.
# [System.IO.File] resolves relative paths against the process folder, which
# is often different, so a relative path must never reach it.
$ExePath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Exe)
if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) { Fail 1 "error: no such file: $Exe" }
$data = [System.IO.File]::ReadAllBytes($ExePath)

if ($data.Length -ne $man.target.size) {
    Fail 3 "error: $ExePath is $($data.Length) bytes; $($man.target.name) v3.0 is $($man.target.size). Refusing."
}

if ($Status) {
    $digest = Get-Md5 $data
    $tag = if ($digest -eq $man.target.md5_pristine) { '   (pristine)' } else { '' }
    Write-Output "file : $ExePath"
    Write-Output "md5  : $digest$tag"
    foreach ($p in $patches) {
        Write-Output ("  {0,-22} {1,-12} [{2}]" -f $p.id, (Get-PatchState $data $p), $p.status)
    }
    exit 0
}

# ---- select ---------------------------------------------------------------------

$unknown = @($Id | Where-Object { -not $byId.ContainsKey($_) })
if ($unknown.Count -gt 0) {
    Fail 2 "error: unknown patch id(s): $($unknown -join ', ')`nknown: $(($patches | ForEach-Object { $_.id }) -join ', ')"
}
if ($All -and $Id.Count -gt 0) { Fail 2 'error: use either -All or patch ids, not both' }

if ($All) {
    if ($Revert) {
        $selected = $patches
    } else {
        $selected = @($patches | Where-Object { $_.status -eq 'recommended' })
        foreach ($p in ($patches | Where-Object { $_.status -ne 'recommended' })) {
            Write-Output ("  {0,-22} skipped [{1}] - name it to apply it" -f $p.id, $p.status)
        }
    }
} else {
    $selected = @($Id | Select-Object -Unique | ForEach-Object { $byId[$_] })
}
if ($selected.Count -eq 0) { Fail 2 'Nothing selected. Use -All, -List, or name a patch id.' }

# ---- verify everything before writing anything ----------------------------------

foreach ($p in $selected) {
    if ((Get-PatchState $data $p) -eq 'UNEXPECTED') {
        $cur = ConvertTo-Hex (Get-Site $data $p)
        Fail 3 ("error: {0}: unexpected bytes at {1}: {2}`n       expected {3} (original) or {4} (patched).`n       Refusing to write anything." -f $p.id, $p.offset, $cur, $p.original, $p.patched)
    }
}

if (-not $Revert) {
    $appliedAfter = New-Object System.Collections.Generic.HashSet[string]
    foreach ($p in $patches) { if ((Get-PatchState $data $p) -eq 'applied') { [void]$appliedAfter.Add($p.id) } }
    foreach ($p in $selected) { [void]$appliedAfter.Add($p.id) }
    foreach ($p in $selected) {
        $clash = @(@($p.conflicts) | Where-Object { $appliedAfter.Contains($_) })
        foreach ($q in $patches) {
            if ($appliedAfter.Contains($q.id) -and (@($q.conflicts) -contains $p.id)) { $clash += $q.id }
        }
        $clash = @($clash | Sort-Object -Unique)
        if ($clash.Count -gt 0) { Fail 5 "error: $($p.id) conflicts with $($clash -join ', '). Revert the other one first." }
    }
}

# ---- build the new image ----------------------------------------------------------

$new = [byte[]]$data.Clone()
$want = if ($Revert) { 'applied' } else { 'not applied' }
$changed = @()
foreach ($p in $selected) {
    if ((Get-PatchState $data $p) -ne $want) {
        $done = if ($Revert) { 'reverted' } else { 'applied' }
        Write-Output ("  {0,-22} already {1}" -f $p.id, $done)
        continue
    }
    if ($Revert) { $frm = $p._new;  $to = $p._orig }
    else         { $frm = $p._orig; $to = $p._new }
    [Array]::Copy($to, 0, $new, $p._off, $to.Length)
    $changed += ,@($p, $frm, $to)
}

if ($changed.Count -eq 0) { Write-Output 'Nothing to do.'; exit 0 }

# ---- backup, then atomic write ---------------------------------------------------

$bak = "$ExePath.$((Get-Md5 $data).Substring(0, 8)).bak"
if (Test-Path -LiteralPath $bak) {
    Write-Output "backup : $([System.IO.Path]::GetFileName($bak)) (already there)"
} else {
    [System.IO.File]::Copy($ExePath, $bak, $false)
    Write-Output "backup : $([System.IO.Path]::GetFileName($bak))"
}

Write-Atomic $ExePath $new

foreach ($entry in $changed) {
    $p = $entry[0]
    $verb = if ($Revert) { 'reverted' } else { 'applied' }
    Write-Output ("  {0,-22} {1}  {2}: {3} -> {4}" -f $p.id, $verb, $p.offset, (ConvertTo-Hex $entry[1]), (ConvertTo-Hex $entry[2]))
}
$digest = Get-Md5 $new
$tag = if ($digest -eq $man.target.md5_pristine) { '   (pristine)' } else { '' }
Write-Output "md5    : $digest$tag"
exit 0
