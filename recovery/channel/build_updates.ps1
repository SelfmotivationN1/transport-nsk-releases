# Runs on an isolated Windows GitHub Actions runner.
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
$root = Join-Path $env:RUNNER_TEMP 'transport-nsk-channel-v2'
$installed = Join-Path $root 'installed'
$originalName = 'Транспорт НСК.exe'
$original = Join-Path $installed $originalName
$channel = 'recovery/channel/channel-v2.json'
$workspaceSetting = 'HKCU:\Software\TransportNSK'
$quiet = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-')
New-Item -ItemType Directory -Path $root | Out-Null

function Assert-Hash($path, $expected) {
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash -ne $expected) {
        throw "Unexpected SHA-256 for $path."
    }
}

function Run-Setup($path, $arguments) {
    $setup = Start-Process -FilePath $path -ArgumentList $arguments -WindowStyle Hidden -PassThru
    if (!$setup.WaitForExit(90000) -or $setup.ExitCode -ne 0) {
        throw "Setup failed: $path"
    }
}

function Invoke-Smoke($version) {
    $smoke = Join-Path $root "smoke-$version.json"
    $app = Start-Process -FilePath $original -WorkingDirectory $installed -ArgumentList @(
        '--smoke-test', ('"{0}"' -f $smoke)
    ) -WindowStyle Hidden -PassThru
    if (!$app.WaitForExit(45000) -or $app.ExitCode -ne 0) { throw "App $version did not start." }
    $result = Get-Content -Raw -LiteralPath $smoke | ConvertFrom-Json
    if ($result.version -ne $version -or !$result.frozen -or $result.workspace -ne $workspace -or $result.tabs -ne 8 -or !$result.route_solver) {
        throw "App $version failed its smoke check."
    }
    $result
}

python -m pip install --disable-pip-version-check pyinstaller==6.21.0
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed.' }
$old = Join-Path $root 'TransportNSK-1.2.3-Setup.exe'
Invoke-WebRequest -Uri 'https://github.com/SelfmotivationN1/transport-nsk-releases/releases/download/v1.2.3/TransportNSK-1.2.3-Setup.exe' -OutFile $old
Assert-Hash $old 'dda9a6d0d495834fc1bd96609c42ce5dd285c7142d16e00aedb30bc4d58877fd'
Run-Setup $old ($quiet + @(('/DIR="{0}"' -f $installed), '/TASKS=""'))
$workspace = Get-ItemPropertyValue -LiteralPath $workspaceSetting -Name WorkspaceRoot
$before = Invoke-Smoke '1.2.3'
$uninstaller = Get-ChildItem -File -LiteralPath $installed | Where-Object Name -Match '^unins.*\.(dat|exe)$'
if (@($uninstaller).Count -lt 2) { throw 'The previous uninstaller is missing.' }
$uninstallHashes = $uninstaller | Get-FileHash -Algorithm SHA256
$databaseHash = (Get-FileHash -LiteralPath $before.database -Algorithm SHA256).Hash
$savedFeed = Join-Path (Split-Path -Parent $before.database) 'updates.json'
Set-Content -Encoding utf8 -LiteralPath $savedFeed -Value '{"feed_url":"https://github.com/SelfmotivationN1/transport-nsk-releases/releases/latest/download/appcast.xml"}'
$savedFeedHash = (Get-FileHash -LiteralPath $savedFeed -Algorithm SHA256).Hash

$bundle = Join-Path $root 'bundle'
python -B recovery/transport_recovery.py extract $original $bundle
if ($LASTEXITCODE -ne 0) { throw 'Verified archive extraction failed.' }
python -B recovery/check_transport.py $bundle
if ($LASTEXITCODE -ne 0) { throw 'PDF and calculation regression checks failed.' }
$compiler = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (!$compiler) { $compiler = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe' }
if (!(Test-Path -LiteralPath $compiler)) { throw 'Inno Setup compiler is missing.' }
$script = Join-Path $root 'channel-update.iss'
Get-Content -Raw -Encoding UTF8 -LiteralPath 'recovery/manual-update.iss' | Set-Content -LiteralPath $script -Encoding utf8BOM

$targetVersions = '1.2.5','1.2.6'
$setups = @{}
$appHashes = @{}
foreach ($version in $targetVersions) {
    $payload = Join-Path $root "payload-$version"
    New-Item -ItemType Directory -Path $payload | Out-Null
    $payloadExe = Join-Path $payload $originalName
    python -B recovery/build_portable_preview.py $original $payloadExe --version $version --channel-config $channel
    if ($LASTEXITCODE -ne 0) { throw "Could not build $version." }
    $appHashes[$version] = (Get-FileHash -LiteralPath $payloadExe -Algorithm SHA256).Hash
    $name = "TransportNSK-$version-Setup"
    & $compiler "/DPayloadDir=$payload" "/DUpdateVersion=$version" "/DOutputName=$name" "/O$root" $script
    $setups[$version] = Join-Path $root "$name.exe"
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $setups[$version])) { throw "Could not compile setup $version." }
}

Run-Setup $setups['1.2.5'] $quiet
Assert-Hash $original $appHashes['1.2.5']
Assert-Hash $before.database $databaseHash
$bridge = Invoke-Smoke '1.2.5'
if ($bridge.database -ne $before.database) { throw 'Migration changed the database path.' }

$updateArgs = $quiet + @('/FROMUPDATE=1', ('/DIR="{0}"' -f $installed), ('/WORKSPACE="{0}"' -f $workspace))
Run-Setup $setups['1.2.6'] $updateArgs
Assert-Hash $original $appHashes['1.2.6']
Assert-Hash $before.database $databaseHash
Assert-Hash $savedFeed $savedFeedHash
foreach ($file in $uninstallHashes) { Assert-Hash $file.Path $file.Hash }
if ((Get-ItemPropertyValue -LiteralPath $workspaceSetting -Name WorkspaceRoot) -ne $workspace) {
    throw 'The workspace setting changed.'
}

$restarted = @()
for ($attempt = 0; $attempt -lt 20 -and !$restarted; $attempt++) {
    $restarted = @(Get-Process -ErrorAction SilentlyContinue | Where-Object Path -EQ $original)
    if (!$restarted) { Start-Sleep -Seconds 1 }
}
if (!$restarted) { throw 'The application did not relaunch after the update.' }
foreach ($app in $restarted) {
    if (!$app.HasExited) { Stop-Process -Id $app.Id -ErrorAction SilentlyContinue }
}
$next = Invoke-Smoke '1.2.6'
if ($next.database -ne $before.database) { throw 'The update changed the database path.' }

foreach ($version in $targetVersions) {
    $path = $setups[$version]
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -Encoding ascii -LiteralPath "$path.sha256" -Value "$hash  $(Split-Path -Leaf $path)"
}
