[CmdletBinding()]
param(
    [string]$ArchiveName = "bilibili-windows.7z"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildRoot = Join-Path $ProjectRoot "build\\nuitka"
$CompiledDistDir = Join-Path $BuildRoot "main.dist"
$DistRoot = Join-Path $ProjectRoot "dist"
$BundleDir = Join-Path $DistRoot "bilibili"
$BundleExe = Join-Path $BundleDir "bilibili.exe"
$SourceBinDir = Join-Path $ProjectRoot "bin"
$BundleBinDir = Join-Path $BundleDir "bin"
$ArchivePath = Join-Path $DistRoot $ArchiveName
$ArchiveHelper = Join-Path $ProjectRoot "scripts\\make_7z.py"
$IconSource = Join-Path $ProjectRoot "static\\Bilibili_logo_2.webp"
$IconTarget = Join-Path $ProjectRoot "static\\Bilibili_logo_2.ico"
$StaticDir = Join-Path $ProjectRoot "static"
$EntryPoint = Join-Path $ProjectRoot "main.py"
$RequiredBinaries = @("ffmpeg.exe", "yt-dlp.exe")

function Resolve-CommandPath {
    param([Parameter(Mandatory = $true)][string]$Name)

    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }
    return $null
}

function Resolve-PythonCommand {
    $VenvPython = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"
    if (Test-Path $VenvPython) {
        return @($VenvPython)
    }

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

if (-not (Test-Path $EntryPoint)) {
    throw "Missing entry point: $EntryPoint"
}

if (-not (Test-Path $StaticDir)) {
    throw "Missing static directory: $StaticDir"
}

if (-not (Test-Path $SourceBinDir)) {
    throw "Missing source bin directory: $SourceBinDir"
}

if (-not (Test-Path $ArchiveHelper)) {
    throw "Missing archive helper script: $ArchiveHelper"
}

if (-not (Test-Path $IconSource)) {
    throw "Missing icon source file: $IconSource"
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
        Invoke-Native @($Uv, "run", "python", "scripts\\convert_icon.py", $IconSource, $IconTarget)
        Invoke-Native @(
            $Uv,
            "run",
            "--with",
            "Nuitka",
            "python",
            "-m",
            "nuitka",
            "--mode=standalone",
            "--assume-yes-for-downloads",
            "--enable-plugins=pyside6",
            "--windows-console-mode=disable",
            "--windows-icon-from-ico=$IconTarget",
            "--include-data-dir=$StaticDir=static",
            "--output-dir=$BuildRoot",
            "--output-filename=bilibili.exe",
            "main.py"
        )
    }
    else {
        Invoke-Native ($PythonCommand + @("scripts\\convert_icon.py", $IconSource, $IconTarget))
        Invoke-Native (
            $PythonCommand + @(
                "-m",
                "nuitka",
                "--mode=standalone",
                "--assume-yes-for-downloads",
                "--enable-plugins=pyside6",
                "--windows-console-mode=disable",
                "--windows-icon-from-ico=$IconTarget",
                "--include-data-dir=$StaticDir=static",
                "--output-dir=$BuildRoot",
                "--output-filename=bilibili.exe",
                "main.py"
            )
        )
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path $CompiledDistDir)) {
    throw "Nuitka finished, but the packaged directory was not found: $CompiledDistDir"
}

New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null
Copy-Item -Path $CompiledDistDir -Destination $BundleDir -Recurse -Force

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
