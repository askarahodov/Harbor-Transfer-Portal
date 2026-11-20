# Windows offline lifecycle — clean-host acceptance

This is a controlled release-acceptance procedure, not a production maintenance procedure. Run it only on a disposable Windows 10/11 host or VM with no existing Harbor Transfer Portal installation or data. Do not point it at a production Docker context.

## 1. Build a candidate kit

On a connected Linux build host, check out the exact CI-tested commit from the pull request. Build the two prebuilt images and package them without credentials:

    git checkout <CI-tested-commit-sha>
    VERSION="$(sed -n 's/^__version__ = "\([A-Za-z0-9._-][A-Za-z0-9._-]*\)"$/\1/p' backend/app/__init__.py)"
    ./deploy/build-release-images.sh "$VERSION"
    ./deploy/build-offline-kit.sh "$VERSION" "$PWD/dist"
    (cd dist && sha256sum -c "harbor-transfer-portal-v${VERSION}-offline-install.tar.gz.sha256")

Transfer the archive and its .sha256 sidecar together to the disposable Windows host. Record the commit SHA, version, archive SHA-256, and image architecture. Disconnect the Windows host from external networks after transfer; Docker's local bridge network remains available to the application.

## 2. Clean-host preflight

Open Windows PowerShell 5.1+ in an elevated or otherwise Docker-authorized session. Confirm Docker Desktop is in Linux containers mode and Compose v2 is available:

    docker info --format '{{.OSType}}'
    docker info --format '{{.Architecture}}'
    docker compose version
    docker compose ls --all
    docker volume ls --filter name=harbor-transfer-portal_portal-data

Expected OS type is linux; architecture must match release-arch.txt. There must be no existing harbor-transfer-portal Compose project or harbor-transfer-portal_portal-data volume. If either exists, stop: this is not a clean acceptance host.

Verify the external archive checksum before extraction:

    $archive = (Resolve-Path '.\harbor-transfer-portal-v1.0.0-offline-install.tar.gz').Path
    $sidecar = Get-Content "$archive.sha256" -Raw
    $expected = ($sidecar.Trim() -split '\s+')[0].ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw 'Offline kit SHA-256 mismatch' }
    tar.exe -xzf $archive

Use the extracted kit directory as the working directory for all following commands.

## 3. Install, rerun, stop and start

    $env:PORTAL_CONTOUR = 'SOURCE'
    .\install.cmd
    $envHashBefore = (Get-FileHash .\.env -Algorithm SHA256).Hash
    .\install.cmd
    $envHashAfter = (Get-FileHash .\.env -Algorithm SHA256).Hash
    if ($envHashBefore -ne $envHashAfter) { throw '.env changed on same-version rerun' }
    Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/health
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\stop.ps1
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
    Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/health

Expected: both installs complete without build/pull, the .env hash is unchanged on rerun, stop preserves the volume and config, and start returns a healthy Portal.

## 4. Backup and matching-version restore

    $backupDir = Join-Path $env:TEMP 'htp-windows-acceptance-backups'
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $backupOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backup.ps1 -BackupDir $backupDir
    $backup = $backupOutput | Where-Object { $_ -is [string] -and $_.Trim() -match '\.zip
    if (-not (Test-Path "$backup.sha256")) { throw 'Backup sidecar missing' }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\restore.ps1 -BackupArchive $backup -ConfirmRestore
    if ($LASTEXITCODE -ne 0) { throw 'Matching-version restore failed' }
    Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/health

Expected: the backup ZIP and sidecar exist, restore requires explicit confirmation, the matching kit restores the volume and .env, and health returns successfully. Treat the ZIP and .env as secret-bearing; never attach them to CI artifacts or issue comments.

## 5. Uninstall preservation and final cleanup

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
    docker volume inspect harbor-transfer-portal_portal-data
    if (-not (Test-Path .\.env)) { throw '.env was not preserved by default uninstall' }
    .\start.ps1
    .\uninstall.ps1 -PurgeData -ConfirmPurge DELETE_PORTAL_DATA

Expected: default uninstall leaves the volume and .env intact and the kit can start again. The final purge is destructive and is allowed only on this disposable host after the backup has been verified.

## 6. Upgrade acceptance

Upgrade requires a second, strictly newer stable-version kit of the same architecture (for example 1.0.0 to 1.1.0). Do not manufacture a version by editing release-version.txt; the image labels and checksums must match the real candidate release.

On a disposable host with the previous kit installed, unpack the newer kit side-by-side and run from the newer directory:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\upgrade.ps1 -NewKitDir $newKitDir -InstallDir $oldKitDir

Record the pre-upgrade backup path and verify the following:
- old workload is paused immediately after the backup snapshot;
- the new version starts and passes Compose health before the upgrade reports UPGRADE_OK;
- .env settings other than PORTAL_VERSION and persistent runtime state survive.

A separate negative run must use an intentionally unhealthy but correctly versioned candidate image/kit. Confirm the upgrade exits non-zero, reports ROLLBACK_OK, restores the matching previous-version backup and returns the old Portal to healthy state. If it reports ROLLBACK_FAILED, preserve both kits and the backup and stop; do not attempt another startup against the volume.

## 7. Evidence to retain

Attach only non-secret evidence to the PR/issue:
- Windows edition/build, PowerShell version, Docker Desktop/Engine and Compose versions;
- tested commit SHA, kit version, architecture, outer archive SHA-256;
- command transcript with secret values redacted;
- docker compose ps and health-check output before/after restart and restore;
- .env hash before/after rerun (never .env contents);
- confirmation that default uninstall preserved the volume and explicit purge removed it.

Never attach .env, backup ZIPs, private keys, Harbor credentials, or full environment dumps. } | Select-Object -Last 1
    if (-not (Test-Path "$backup.sha256")) { throw 'Backup sidecar missing' }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\restore.ps1 -BackupArchive $backup -ConfirmRestore
    if ($LASTEXITCODE -ne 0) { throw 'Matching-version restore failed' }
    Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/api/health

Expected: the backup ZIP and sidecar exist, restore requires explicit confirmation, the matching kit restores the volume and .env, and health returns successfully. Treat the ZIP and .env as secret-bearing; never attach them to CI artifacts or issue comments.

## 5. Uninstall preservation and final cleanup

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
    docker volume inspect harbor-transfer-portal_portal-data
    if (-not (Test-Path .\.env)) { throw '.env was not preserved by default uninstall' }
    .\start.ps1
    .\uninstall.ps1 -PurgeData -ConfirmPurge DELETE_PORTAL_DATA

Expected: default uninstall leaves the volume and .env intact and the kit can start again. The final purge is destructive and is allowed only on this disposable host after the backup has been verified.

## 6. Upgrade acceptance

Upgrade requires a second, strictly newer stable-version kit of the same architecture (for example 1.0.0 to 1.1.0). Do not manufacture a version by editing release-version.txt; the image labels and checksums must match the real candidate release.

On a disposable host with the previous kit installed, unpack the newer kit side-by-side and run from the newer directory:

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\upgrade.ps1 -NewKitDir $newKitDir -InstallDir $oldKitDir

Record the pre-upgrade backup path and verify the following:
- old workload is paused immediately after the backup snapshot;
- the new version starts and passes Compose health before the upgrade reports UPGRADE_OK;
- .env settings other than PORTAL_VERSION and persistent runtime state survive.

A separate negative run must use an intentionally unhealthy but correctly versioned candidate image/kit. Confirm the upgrade exits non-zero, reports ROLLBACK_OK, restores the matching previous-version backup and returns the old Portal to healthy state. If it reports ROLLBACK_FAILED, preserve both kits and the backup and stop; do not attempt another startup against the volume.

## 7. Evidence to retain

Attach only non-secret evidence to the PR/issue:
- Windows edition/build, PowerShell version, Docker Desktop/Engine and Compose versions;
- tested commit SHA, kit version, architecture, outer archive SHA-256;
- command transcript with secret values redacted;
- docker compose ps and health-check output before/after restart and restore;
- .env hash before/after rerun (never .env contents);
- confirmation that default uninstall preserved the volume and explicit purge removed it.

Never attach .env, backup ZIPs, private keys, Harbor credentials, or full environment dumps.