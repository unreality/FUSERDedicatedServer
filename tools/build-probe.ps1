param(
    # The UE 4.24.3 source engine root (the folder holding Engine\ and Setup.bat). Default: $LocalEngineRoot below.
    [string]$EngineRoot = '',
    [ValidateSet('Build', 'Package')][string]$Mode = 'Build',
    # Linux: cross-compiled with Epic's v15_clang-8.0.1-centos7 toolchain (docs/LINUX.md); output in build\package-linux.
    [ValidateSet('Win64', 'Linux')][string]$Platform = 'Win64',
    # The v15_clang-8.0.1-centos7 toolchain folder. Default: $LocalLinuxToolchain below, else LINUX_MULTIARCH_ROOT.
    [string]$LinuxToolchain = '',
    # With -Mode Package: also write the distribution archive (tools\make-dist.py, build\dist): a .tar.gz plus the
    # build\docker context for Linux, a .zip for Win64.
    [switch]$Dist,
    [string]$DistVersion = '',
    # python.exe for -Dist (Python 3). Default: $LocalPython below, else python on PATH.
    [string]$Python = ''
)

# ---- Local paths: set these for this machine, or pass -EngineRoot, -LinuxToolchain and -Python. ----
# For example 'C:\UE-4.24.3\UnrealEngine-4.24.3-release'. Required.
$LocalEngineRoot = ''
# For example 'C:\UE-4.24.3\LinuxToolchain\v15_clang-8.0.1-centos7\'. Only for -Platform Linux; empty uses
# LINUX_MULTIARCH_ROOT, which the toolchain installer sets machine-wide.
$LocalLinuxToolchain = ''
# For example 'C:\Program Files\Python313\python.exe'. Only for -Dist; empty uses python on PATH.
$LocalPython = ''
# ----

if ($Dist -and $Mode -ne 'Package') { throw '-Dist needs -Mode Package.' }
$ErrorActionPreference = 'Stop'
if (!$EngineRoot) { $EngineRoot = $LocalEngineRoot }
if (!$EngineRoot) { throw 'No engine root: set $LocalEngineRoot in tools\build-probe.ps1 or pass -EngineRoot.' }
if ($Platform -eq 'Linux') {
    if (!$LinuxToolchain) { $LinuxToolchain = $LocalLinuxToolchain }
    if (!$LinuxToolchain) { $LinuxToolchain = $env:LINUX_MULTIARCH_ROOT }
    if (!$LinuxToolchain) {
        throw 'No Linux toolchain: set $LocalLinuxToolchain in tools\build-probe.ps1, pass -LinuxToolchain or set LINUX_MULTIARCH_ROOT.'
    }
    if (!$LinuxToolchain.EndsWith('\')) { $LinuxToolchain += '\' }
    if (!(Test-Path -LiteralPath (Join-Path $LinuxToolchain 'x86_64-unknown-linux-gnu'))) { throw "Linux toolchain not found: $LinuxToolchain" }
}
if ($Dist) {
    if (!$Python) { $Python = $LocalPython }
    if ($Python) {
        if (!(Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python not found: $Python" }
    } else {
        # Skips the Microsoft Store alias in WindowsApps, which only offers to install Python.
        $onPath = Get-Command python.exe -CommandType Application -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notlike '*\WindowsApps\*' } | Select-Object -First 1
        if (!$onPath) { throw 'No Python for -Dist: set $LocalPython in tools\build-probe.ps1, pass -Python or put python on PATH.' }
        $Python = $onPath.Source
    }
}
$project = Join-Path (Split-Path $PSScriptRoot) 'Fuser\Fuser.uproject'
$versionPath = Join-Path $EngineRoot 'Engine\Build\Build.version'
if (!(Test-Path -LiteralPath $versionPath)) { throw "Engine version not found: $versionPath" }
$version = Get-Content -LiteralPath $versionPath -Raw | ConvertFrom-Json
if ($version.MajorVersion -ne 4 -or $version.MinorVersion -ne 24 -or $version.PatchVersion -ne 3) {
    throw "Expected UE4.24.3; found $($version.MajorVersion).$($version.MinorVersion).$($version.PatchVersion). Select the source revision before building."
}
if (Test-Path (Join-Path $EngineRoot 'Engine\Build\InstalledBuild.txt')) {
    throw 'Use the source-engine checkout, not the Launcher distribution.'
}
$outputRoot = Join-Path (Split-Path $PSScriptRoot) 'build'
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
$ubt = Join-Path $EngineRoot 'Engine\Binaries\DotNET\UnrealBuildTool.exe'
if (!(Test-Path -LiteralPath $ubt)) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    $vsRoot = & $vswhere -latest -products '*' -version '[16.0,17.0)' -requires Microsoft.Component.MSBuild -property installationPath
    if (!$vsRoot) { throw 'VS2019 MSBuild was not found. Complete the supported toolchain installation first.' }
    $msbuild = Join-Path $vsRoot 'MSBuild\Current\Bin\MSBuild.exe'
    $ubtProject = Join-Path $EngineRoot 'Engine\Source\Programs\UnrealBuildTool\UnrealBuildTool.csproj'
    & $msbuild $ubtProject /nologo /verbosity:minimal /property:Configuration=Development /property:Platform=AnyCPU 2>&1 |
        Tee-Object -FilePath (Join-Path $outputRoot 'ubt-bootstrap.log')
    if ($LASTEXITCODE -ne 0) { throw 'UnrealBuildTool bootstrap failed; inspect ubt-bootstrap.log.' }
}
# Engine tools write progress to stderr; under 'Stop' Windows PowerShell 5.1 turns the first such line into a
# terminating error. Exit codes are checked below.
$ErrorActionPreference = 'Continue'
# RunUAT.bat starts AutomationToolLauncher.exe from its working directory without a path; cmd refuses that while
# NoDefaultCurrentDirectoryInExePath is set (as in some shells). Clear it for this process only.
Remove-Item Env:NoDefaultCurrentDirectoryInExePath -ErrorAction SilentlyContinue
$suffix = ''
if ($Platform -eq 'Linux') {
    $env:LINUX_MULTIARCH_ROOT = $LinuxToolchain
    $suffix = '-linux'
}
if ($Mode -eq 'Build') {
    $build = Join-Path $EngineRoot 'Engine\Build\BatchFiles\Build.bat'
    & $build FuserServerServer $Platform Development "-Project=$project" -WaitMutex -NoHotReloadFromIDE 2>&1 |
        Tee-Object -FilePath (Join-Path $outputRoot "server-build$suffix.log")
} else {
    $uat = Join-Path $EngineRoot 'Engine\Build\BatchFiles\RunUAT.bat'
    # The cooker skips maps in DirectoriesToAlwaysCook, so maps are named explicitly. The venue worlds
    # (Config\FDSVenueMaps.txt) and the sublevel stubs clients stream (Config\FDSVenueSublevels-*.txt) reach the cook
    # through the FDSEditor module: on -map= they overrun the editor's command line.
    $mapArg = '-map=/Game/Maps/Venue01/Venue01+/Game/Maps/Server/ServerDefaultMap'
    # Build both binaries: editor commandlet for cooking plus standalone server.
    & $uat BuildCookRun "-project=$project" -noP4 -utf8output -server -noclient `
        "-serverplatform=$Platform" -serverconfig=Development -build -cook -stage -pak -archive `
        $mapArg "-archivedirectory=$(Join-Path $outputRoot "package$suffix")" 2>&1 |
        Tee-Object -FilePath (Join-Path $outputRoot "server-package$suffix.log")
}
$buildExitCode = $LASTEXITCODE
if ($buildExitCode -ne 0) { throw "Engine tools failed with exit code $buildExitCode; see $outputRoot" }
if ($Dist) {
    $distArgs = @((Join-Path $PSScriptRoot 'make-dist.py'), '--platform', $Platform)
    if ($DistVersion) { $distArgs += @('--version', $DistVersion) }
    & $Python @distArgs
    if ($LASTEXITCODE -ne 0) { throw "make-dist.py failed with exit code $LASTEXITCODE" }
}
