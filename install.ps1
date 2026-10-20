<#
.SYNOPSIS
Install agent-autosave on Windows (experimental for v0.1).

.DESCRIPTION
  irm https://raw.githubusercontent.com/KhairallahA/agent-autosave/main/install.ps1 | iex
  .\install.ps1 -From .           # from a checkout, a wheel or an sdist
  .\install.ps1 -Uninstall

Re-running it upgrades in place. It uses pipx when pipx is installed;
otherwise it makes a private virtualenv under %LOCALAPPDATA%\agent-autosave
and puts a small agent-autosave.cmd in its bin folder, which it adds to your
user PATH. It never needs an administrator shell.

Each parameter also has an environment variable: AGENT_AUTOSAVE_SOURCE,
AGENT_AUTOSAVE_VERSION, AGENT_AUTOSAVE_METHOD; and AGENT_AUTOSAVE_PYTHON,
AGENT_AUTOSAVE_HOME, AGENT_AUTOSAVE_BIN_DIR, AGENT_AUTOSAVE_PIP_ARGS.
#>
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification = 'Used by the functions below.')]
[CmdletBinding()]
param(
    # A path (checkout, wheel, sdist), URL or pip requirement. Default: agent-autosave from PyPI.
    [string]$From = $env:AGENT_AUTOSAVE_SOURCE,
    # A PyPI release, e.g. 0.1.0.
    [string]$Version = $env:AGENT_AUTOSAVE_VERSION,
    # auto (pipx when installed), pipx or venv.
    [ValidateSet('auto', 'pipx', 'venv', '')]
    [string]$Method = $env:AGENT_AUTOSAVE_METHOD,
    # Remove what this script installed.
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0
$Name = 'agent-autosave'
$OnWindows = [System.Environment]::OSVersion.Platform -eq 'Win32NT'

function Show-InstallFailure([string]$Message, [string]$Hint = '') {
    [Console]::Error.WriteLine("agent-autosave install: $Message")
    if ($Hint) { [Console]::Error.WriteLine("  hint: $Hint") }
    exit 1
}

if (-not $Method) { $Method = 'auto' }
$BaseDir = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME '.local/share' }
$Data = if ($env:AGENT_AUTOSAVE_HOME) { $env:AGENT_AUTOSAVE_HOME } else { Join-Path $BaseDir $Name }
$Bin = if ($env:AGENT_AUTOSAVE_BIN_DIR) { $env:AGENT_AUTOSAVE_BIN_DIR } else { Join-Path $Data 'bin' }
$Venv = Join-Path $Data 'venv'
$Marker = Join-Path $Data 'installed-by-script'
$Log = Join-Path $Data 'install.log'
$PipArgs = if ($env:AGENT_AUTOSAVE_PIP_ARGS) { -split $env:AGENT_AUTOSAVE_PIP_ARGS } else { @() }
$VenvPython = if ($OnWindows) { Join-Path $Venv 'Scripts\python.exe' } else { Join-Path $Venv 'bin/python' }
$VenvCommand = if ($OnWindows) { Join-Path $Venv "Scripts\$Name.exe" } else { Join-Path $Venv "bin/$Name" }

# --- Python ----------------------------------------------------------------
function Test-Python([string[]]$Command) {
    # True when the interpreter runs and is 3.9 or newer. The Microsoft Store
    # "python" alias fails to run, so it is skipped here.
    try {
        $exe = $Command[0]; $rest = @($Command | Select-Object -Skip 1)
        & $exe @rest -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}
function Get-PythonVersion([string[]]$Command) {
    try {
        $exe = $Command[0]; $rest = @($Command | Select-Object -Skip 1)
        $text = & $exe @rest -c 'import sys; print("Python %d.%d" % sys.version_info[:2])' 2>$null
        if ($LASTEXITCODE -eq 0 -and $text) { return "$text" }
    } catch { $null = $_ }
    return 'an unknown version'
}
function Find-Python {
    if ($env:AGENT_AUTOSAVE_PYTHON) {
        $candidate = @($env:AGENT_AUTOSAVE_PYTHON)
        if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) {
            Show-InstallFailure "AGENT_AUTOSAVE_PYTHON=$($env:AGENT_AUTOSAVE_PYTHON) was not found"
        }
        if (-not (Test-Python $candidate)) {
            Show-InstallFailure "$($candidate[0]) is $(Get-PythonVersion $candidate); agent-autosave needs Python 3.9 or newer"
        }
        return $candidate
    }
    $seen = @()
    foreach ($candidate in @(@('py', '-3'), @('python3'), @('python'))) {
        if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
        if (Test-Python $candidate) { return $candidate }
        $seen += "$($candidate -join ' ') ($(Get-PythonVersion $candidate))"
    }
    if ($seen) {
        Show-InstallFailure "agent-autosave needs Python 3.9 or newer; found only: $($seen -join ', ')" 'install Python from https://www.python.org/downloads/ or: winget install Python.Python.3.13'
    }
    Show-InstallFailure 'Python was not found on PATH' 'install Python 3.9 or newer (winget install Python.Python.3.13), then run this again'
}

# --- What to install ---------------------------------------------------------
function Get-Requirement {
    if ($From) {
        if ($Version) { Show-InstallFailure 'use -From or -Version, not both' }
        if (Test-Path -LiteralPath $From) { return (Resolve-Path -LiteralPath $From).Path }
        return $From
    }
    if ($Version) { return "$Name==$($Version.TrimStart('v'))" }
    return $Name
}
function Get-RecordedMethod {
    if (-not (Test-Path -LiteralPath $Marker)) { return '' }
    foreach ($line in Get-Content -LiteralPath $Marker) {
        if ($line -like 'method=*') { return $line.Substring(7) }
    }
    return ''
}
function Test-Pipx { return [bool](Get-Command pipx -ErrorAction SilentlyContinue) }
function Test-PipxHas {
    if (-not (Test-Pipx)) { return $false }
    return [bool](& pipx list --short 2>$null | Where-Object { $_ -like "$Name *" })
}
function Invoke-Logged([string]$Exe, [string[]]$Arguments, [string]$What) {
    & $Exe @Arguments *>> $Log
    if ($LASTEXITCODE -ne 0) {
        Get-Content -LiteralPath $Log | ForEach-Object { [Console]::Error.WriteLine($_) }
        Show-InstallFailure "$What failed (log: $Log)"
    }
}

# --- Install -----------------------------------------------------------------
function Install-WithPipx([string[]]$Python, [string]$Requirement) {
    if (-not (Test-Pipx)) { Show-InstallFailure '-Method pipx was asked for, but pipx is not installed' 'drop -Method, or install pipx first' }
    $pythonPath = (Get-Command $Python[0]).Source
    if ($Python[0] -eq 'py') { $pythonPath = (& py -3 -c 'import sys; print(sys.executable)').Trim() }
    $arguments = @('install', '--force', '--python', $pythonPath)
    if ($PipArgs) { $arguments += "--pip-args=$($PipArgs -join ' ')" }
    Invoke-Logged 'pipx' ($arguments + $Requirement) 'pipx install'
    $pipxBin = (& pipx environment --value PIPX_BIN_DIR 2>$null)
    if (-not $pipxBin) { $pipxBin = Join-Path $HOME '.local/bin' }
    $exe = if ($OnWindows) { "$Name.exe" } else { $Name }
    return (Join-Path "$pipxBin".Trim() $exe)
}
function Install-WithVenv([string[]]$Python, [string]$Requirement) {
    if ((Test-Path -LiteralPath $Venv) -and -not (Test-Python @($VenvPython))) {
        Remove-Item -LiteralPath $Venv -Recurse -Force  # its Python is gone: rebuild
    }
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        $exe = $Python[0]; $rest = @($Python | Select-Object -Skip 1)
        Invoke-Logged $exe ($rest + @('-m', 'venv', $Venv)) 'creating the virtualenv'
    }
    $arguments = @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--upgrade', '--force-reinstall') + $PipArgs + $Requirement
    Invoke-Logged $VenvPython $arguments "pip install $Requirement"
    New-Item -ItemType Directory -Force -Path $Bin | Out-Null
    if ($OnWindows) {
        $shim = Join-Path $Bin "$Name.cmd"
        Set-Content -LiteralPath $shim -Encoding ASCII -Value "@echo off`r`n`"$VenvCommand`" %*"
    } else {
        $shim = Join-Path $Bin $Name
        if (Test-Path -LiteralPath $shim) {
            $item = Get-Item -LiteralPath $shim
            if ($item.LinkTarget -ne $VenvCommand) {
                Show-InstallFailure "$shim already exists and was not installed by this script" 'remove it, or set AGENT_AUTOSAVE_BIN_DIR'
            }
            Remove-Item -LiteralPath $shim
        }
        New-Item -ItemType SymbolicLink -Path $shim -Target $VenvCommand | Out-Null
    }
    return $shim
}
function Add-ToUserPath([string]$Dir) {
    $separator = [System.IO.Path]::PathSeparator
    if (($env:PATH -split [regex]::Escape($separator)) -contains $Dir) { return }
    if ($OnWindows -and -not $env:AGENT_AUTOSAVE_BIN_DIR) {
        $user = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (-not $user) { $user = '' }
        if (-not (($user -split ';') -contains $Dir)) {
            [Environment]::SetEnvironmentVariable('Path', ($Dir + ';' + $user).TrimEnd(';'), 'User')
            Write-Output "Added $Dir to your user PATH. Open a new terminal to use agent-autosave."
        }
        $env:PATH = "$Dir;$env:PATH"
        return
    }
    Write-Output "Note: $Dir is not on PATH. Add it to use agent-autosave from any folder."
}
function Install-AgentAutosave {
    $python = Find-Python
    $requirement = Get-Requirement
    New-Item -ItemType Directory -Force -Path $Data | Out-Null
    $previous = Get-RecordedMethod
    $chosen = $Method
    if ($chosen -eq 'auto') {
        $chosen = if ($previous) { $previous } elseif (Test-Pipx) { 'pipx' } else { 'venv' }
    }
    if ($previous -and $previous -ne $chosen) {
        Show-InstallFailure "agent-autosave was installed with $previous by this script" "run it with -Uninstall first, then install with -Method $chosen"
    }
    Write-Output "Installing $requirement with $chosen ($(Get-PythonVersion $python))..."
    Set-Content -LiteralPath $Log -Value '' -Encoding UTF8
    $command = if ($chosen -eq 'pipx') { Install-WithPipx $python $requirement } else { Install-WithVenv $python $requirement }
    Set-Content -LiteralPath $Marker -Encoding UTF8 -Value "method=$chosen`nbin=$command"
    $output = (& $command --version 2>$null) -join ''
    if ($output -notmatch '"version":\s*"([^"]+)"') { Show-InstallFailure "installed, but $command --version failed" "see $Log" }
    Write-Output "Installed agent-autosave $($Matches[1]) at $command"
    Add-ToUserPath (Split-Path -Parent $command)
    Write-Output 'Next: cd into a repository and run: agent-autosave init'
}

# --- Uninstall ---------------------------------------------------------------
function Uninstall-AgentAutosave {
    $recorded = Get-RecordedMethod
    if (-not $recorded) {
        if (Test-PipxHas) { Show-InstallFailure 'agent-autosave was installed with pipx, not by this script' 'pipx uninstall agent-autosave' }
        Show-InstallFailure "nothing to uninstall: this script did not install agent-autosave (no $Marker)" 'if you used pip: python -m pip uninstall agent-autosave'
    }
    $configHome = if ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME } elseif ($env:APPDATA) { $env:APPDATA } else { Join-Path $HOME '.config' }
    $registry = Join-Path $configHome 'agent-autosave/installs.json'
    if ((Test-Path -LiteralPath $registry) -and (Get-Item -LiteralPath $registry).Length -gt 0) {
        Write-Output "Note: folders set up with 'agent-autosave init' keep their hooks. Run 'agent-autosave remove' in each first;"
        Write-Output "      they are listed in $registry"
    }
    if ($recorded -eq 'pipx') {
        if (Test-PipxHas) { & pipx uninstall $Name | Out-Null }
    } elseif ($recorded -eq 'venv') {
        foreach ($shim in @((Join-Path $Bin "$Name.cmd"), (Join-Path $Bin $Name))) {
            if (Test-Path -LiteralPath $shim) {
                $item = Get-Item -LiteralPath $shim
                $ours = ($item.LinkTarget -eq $VenvCommand) -or ($shim.EndsWith('.cmd') -and ((Get-Content -LiteralPath $shim -Raw) -like "*$VenvCommand*"))
                if ($ours) { Remove-Item -LiteralPath $shim -Force }
            }
        }
        if (Test-Path -LiteralPath $Venv) { Remove-Item -LiteralPath $Venv -Recurse -Force }
    } else {
        Show-InstallFailure "unrecognised install record in $Marker"
    }
    foreach ($file in @($Marker, $Log)) { if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force } }
    foreach ($dir in @($Bin, $Data)) {
        if ((Test-Path -LiteralPath $dir) -and -not (Get-ChildItem -LiteralPath $dir -Force)) { Remove-Item -LiteralPath $dir }
    }
    Write-Output "Uninstalled agent-autosave ($recorded)."
}

if ($Uninstall) { Uninstall-AgentAutosave } else { Install-AgentAutosave }
exit 0
