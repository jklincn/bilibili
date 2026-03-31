[CmdletBinding()]
param(
    [string]$ArchiveName = "bilibili-windows.7z"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SpecPath = Join-Path $ProjectRoot "bilibili.spec"
$BuildRoot = Join-Path $ProjectRoot "build"
$DistRoot = Join-Path $ProjectRoot "dist"
$BundleDir = Join-Path $DistRoot "bilibili"
$BundleExe = Join-Path $BundleDir "bilibili.exe"
$SourceBinDir = Join-Path $ProjectRoot "bin"
$BundleBinDir = Join-Path $BundleDir "bin"
$ArchivePath = Join-Path $DistRoot $ArchiveName
$ArchiveHelper = Join-Path $ProjectRoot "scripts\\make_7z.py"
$RequiredBinaries = @("ffmpeg.exe", "ffprobe.exe", "yt-dlp.exe")

function Resolve-CommandPath {
    param([Parameter(Mandatory = $true)][string]$Name)

    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }
    return $null
}

function Resolve-PythonCommand {
    $Python = Resolve-CommandPath -Name "python"
    if ($Python) {
        return @($Python)
    }

    $PyLauncher = Resolve-CommandPath -Name "py"
    if ($PyLauncher) {
        return @($PyLauncher, "-3")
    }

    return @()
}

function Resolve-7Zip {
    $SevenZip = Resolve-CommandPath -Name "7z"
    if ($SevenZip) {
        return $SevenZip
    }

    $Candidates = @()
    if ($env:ProgramFiles) {
        $Candidates += Join-Path $env:ProgramFiles "7-Zip\\7z.exe"
    }
    if (${env:ProgramFiles(x86)}) {
        $Candidates += Join-Path ${env:ProgramFiles(x86)} "7-Zip\\7z.exe"
    }

    foreach ($Candidate in $Candidates) {
        if (Test-Path $Candidate) {
            return $Candidate
        }
    }

    return $null
}

function Invoke-Native {
    param([Parameter(Mandatory = $true)][string[]]$Command)

    if ($Command.Count -eq 1) {
        & $Command[0]
    }
    else {
        & $Command[0] $Command[1..($Command.Count - 1)]
    }

    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $($Command -join ' ')"
    }
}

if ($env:OS -ne "Windows_NT") {
    throw "This packaging script must be run on Windows."
}

if (-not (Test-Path $SpecPath)) {
    throw "Missing PyInstaller spec file: $SpecPath"
}

if (-not (Test-Path $SourceBinDir)) {
    throw "Missing source bin directory: $SourceBinDir"
}

if (-not (Test-Path $ArchiveHelper)) {
    throw "Missing archive helper script: $ArchiveHelper"
}

$Uv = Resolve-CommandPath -Name "uv"
$PythonCommand = Resolve-PythonCommand

if (-not $Uv -and $PythonCommand.Count -eq 0) {
    throw "Neither uv nor Python was found in PATH."
}

Remove-Item $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $BundleDir -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $ArchivePath -Force -ErrorAction SilentlyContinue

Push-Location $ProjectRoot
try {
    if ($Uv) {
        Invoke-Native @(
            $Uv,
            "run",
            "--no-project",
            "--with",
            "pyinstaller",
            "pyinstaller",
            "--clean",
            "--noconfirm",
            $SpecPath
        )
    }
    else {
        Invoke-Native ($PythonCommand + @("-m", "PyInstaller", "--clean", "--noconfirm", $SpecPath))
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path $BundleExe)) {
    throw "PyInstaller finished, but the packaged exe was not found: $BundleExe"
}

New-Item -ItemType Directory -Force -Path $BundleBinDir | Out-Null
Get-ChildItem -Path $SourceBinDir -Filter "*.exe" -File | Copy-Item -Destination $BundleBinDir -Force

$MissingBinaries = @(
    $RequiredBinaries | Where-Object { -not (Test-Path (Join-Path $BundleBinDir $_)) }
)
if ($MissingBinaries.Count -gt 0) {
    throw "Missing required binaries in package: $($MissingBinaries -join ', ')"
}

$SevenZip = Resolve-7Zip
if ($SevenZip) {
    Push-Location $BundleDir
    try {
        Invoke-Native @($SevenZip, "a", "-t7z", "-mx=9", $ArchivePath, ".\\*")
    }
    finally {
        Pop-Location
    }
}
elseif ($Uv) {
    Push-Location $ProjectRoot
    try {
        Invoke-Native @(
            $Uv,
            "run",
            "--no-project",
            "--with",
            "py7zr",
            "python",
            $ArchiveHelper,
            $BundleDir,
            $ArchivePath
        )
    }
    finally {
        Pop-Location
    }
}
else {
    Push-Location $ProjectRoot
    try {
        Invoke-Native ($PythonCommand + @($ArchiveHelper, $BundleDir, $ArchivePath))
    }
    finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "Package created successfully:"
Write-Host "  EXE:     $BundleExe"
Write-Host "  BIN DIR: $BundleBinDir"
Write-Host "  ARCHIVE: $ArchivePath"
