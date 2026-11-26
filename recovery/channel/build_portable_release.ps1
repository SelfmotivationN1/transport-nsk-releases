# Build and test the release in an isolated Windows GitHub Actions runner.
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
$root = Join-Path $env:RUNNER_TEMP 'transport-nsk-127'
$installed = Join-Path $root 'Обычная установка'
$portable = Join-Path $root 'Переносная копия'
$portableData = Join-Path $root 'Отдельная рабочая папка'
$payload = Join-Path $root 'payload'
$bundle = Join-Path $root 'bundle'
$appName = 'Транспорт НСК.exe'
$oldApp = Join-Path $installed $appName
$portableApp = Join-Path $portable $appName
$newApp = Join-Path $payload $appName
$channel = 'recovery/channel/channel-v2.json'
$quiet = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-')
$releases = 'https://github.com/SelfmotivationN1/transport-nsk-releases/releases/download'
New-Item -ItemType Directory -Path $root,$portable,$portableData,$payload | Out-Null

function Assert-Hash($path, $hash) {
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $hash) { throw "Checksum mismatch: $path" }
}

function Run-Setup($path, $arguments, $expectSuccess = $true) {
    $process = Start-Process -FilePath $path -ArgumentList $arguments -PassThru -WindowStyle Hidden
    if (!$process.WaitForExit(90000)) { throw "Setup timed out: $path" }
    if (($process.ExitCode -eq 0) -ne $expectSuccess) { throw "Unexpected setup exit code $($process.ExitCode): $path" }
}

function Read-Smoke($path, $tag, $version, $workspace) {
    $smoke = Join-Path $root "$tag.json"
    $process = Start-Process -FilePath $path -ArgumentList @('--smoke-test', ('"{0}"' -f $smoke)) -PassThru -WindowStyle Hidden
    if (!$process.WaitForExit(45000) -or $process.ExitCode -ne 0) { throw "App failed to start: $tag" }
    $info = Get-Content -Raw -LiteralPath $smoke | ConvertFrom-Json
    if ($info.version -ne $version -or $info.workspace -ne $workspace -or !$info.frozen -or !$info.route_solver) {
        throw "Unexpected version or workspace: $tag"
    }
    $info
}

function Check-Relaunch($path) {
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $running = @(Get-Process -ErrorAction SilentlyContinue | Where-Object Path -EQ $path)
        if ($running) { break }
        Start-Sleep -Seconds 1
    }
    if (!$running) { throw "App was not relaunched: $path" }
    foreach ($process in $running) {
        if (!$process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
    }
}

python -m pip install --disable-pip-version-check pyinstaller==6.21.0
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed.' }
$oldSetup = Join-Path $root 'old-setup.exe'
Invoke-WebRequest -Uri "$releases/v1.2.3/TransportNSK-1.2.3-Setup.exe" -OutFile $oldSetup
Assert-Hash $oldSetup 'dda9a6d0d495834fc1bd96609c42ce5dd285c7142d16e00aedb30bc4d58877fd'
Run-Setup $oldSetup ($quiet + @(('/DIR="{0}"' -f $installed), '/TASKS=""'))
$installedData = Get-ItemPropertyValue -LiteralPath 'HKCU:\Software\TransportNSK' -Name WorkspaceRoot
python -B recovery/transport_recovery.py extract $oldApp $bundle
if ($LASTEXITCODE -ne 0) { throw 'The verified original could not be extracted.' }
python -B recovery/check_transport.py $bundle
if ($LASTEXITCODE -ne 0) { throw 'Regression checks failed.' }
python -B recovery/build_portable_preview.py $oldApp $newApp --version 1.2.7 --channel-config $channel
if ($LASTEXITCODE -ne 0) { throw 'Release build failed.' }
python -B recovery/build_portable_preview.py $oldApp $portableApp --version 1.2.6 --channel-config $channel
if ($LASTEXITCODE -ne 0) { throw 'Portable test build failed.' }

$compiler = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (!$compiler) { $compiler = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe' }
$script = Join-Path $root 'portable-update.iss'
Get-Content -Raw -Encoding UTF8 -LiteralPath 'recovery/manual-update.iss' | Set-Content -LiteralPath $script -Encoding utf8BOM
$setupName = 'TransportNSK-1.2.7-Setup'
$installer = Join-Path $root "$setupName.exe"
& $compiler "/DPayloadDir=$payload" '/DUpdateVersion=1.2.7' "/DOutputName=$setupName" "/O$root" $script
if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $installer)) { throw 'Setup compilation failed.' }
$appHash = (Get-FileHash -LiteralPath $newApp -Algorithm SHA256).Hash
$oldHash = (Get-FileHash -LiteralPath $oldApp -Algorithm SHA256).Hash

$env:TRANSPORT_NSK_WORKSPACE = $portableData
$portableBefore = Read-Smoke $portableApp 'portable-before' '1.2.6' $portableData
$portableHash = (Get-FileHash -LiteralPath $portableBefore.database -Algorithm SHA256).Hash
$arguments = python -B recovery/channel/test_update_target.py $bundle $portableApp $portableData $installer | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot obtain portable updater arguments.' }
Run-Setup $installer ($quiet + @('/FROMUPDATE=1', ('/WORKSPACE="{0}"' -f $portableData))) $false
Assert-Hash $oldApp $oldHash
$quoted = @($arguments | ForEach-Object { if ($_ -match '\s') { '"{0}"' -f $_ } else { $_ } })
Run-Setup $installer $quoted
Assert-Hash $portableApp $appHash
Assert-Hash $oldApp $oldHash
Assert-Hash $portableBefore.database $portableHash
Check-Relaunch $portableApp
$portableAfter = Read-Smoke $portableApp 'portable-after' '1.2.7' $portableData
if ($portableAfter.database -ne $portableBefore.database) { throw 'Portable database path changed.' }

Remove-Item Env:TRANSPORT_NSK_WORKSPACE
$bridge = Join-Path $root 'bridge-126.exe'
$bridgeUrl = "$releases/v1.2.6-channel-v2/TransportNSK-1.2.6-Setup.exe"
Invoke-WebRequest -Uri $bridgeUrl -OutFile $bridge
Assert-Hash $bridge '4fdf52f8bf37f0845844b92e8b7b3492af6b8ebcf7b117bcfaefad8be076263b'
Run-Setup $bridge $quiet
$before = Read-Smoke $oldApp 'installed-before' '1.2.6' $installedData
$databaseHash = (Get-FileHash -LiteralPath $before.database -Algorithm SHA256).Hash
Run-Setup $installer ($quiet + @('/FROMUPDATE=1', ('/DIR="{0}"' -f $installed), ('/WORKSPACE="{0}"' -f $installedData)))
Assert-Hash $oldApp $appHash
Assert-Hash $before.database $databaseHash
Check-Relaunch $oldApp
$after = Read-Smoke $oldApp 'installed-after' '1.2.7' $installedData
if ($after.database -ne $before.database) { throw 'Installed database path changed.' }
Copy-Item -LiteralPath (Join-Path $installed 'licenses') -Destination $payload -Recurse
$portableZip = Join-Path $root 'TransportNSK-1.2.7-Portable.zip'
Compress-Archive -Path (Join-Path $payload '*') -DestinationPath $portableZip
foreach ($file in $installer,$portableZip) {
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -Encoding ascii -LiteralPath "$file.sha256" -Value "$hash  $(Split-Path -Leaf $file)"
}
