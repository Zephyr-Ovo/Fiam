# Pack channels/atrium/browser-extension into a Firefox-installable .xpi.
# Firefox requires manifest.json at the ZIP root — this script enforces that.
# Output: build/atrium-browser-extension.xpi

[CmdletBinding()]
param(
    [string]$Source,
    [string]$OutDir
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Source) { $Source = Join-Path $root "..\channels\atrium\browser-extension" }
if (-not $OutDir) { $OutDir = Join-Path $root "..\build" }

$Source = (Resolve-Path $Source).Path
if (-not (Test-Path (Join-Path $Source "manifest.json"))) {
    throw "manifest.json not found at root of $Source"
}

if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir | Out-Null
}
$OutDir = (Resolve-Path $OutDir).Path

$xpi = Join-Path $OutDir "atrium-browser-extension.xpi"
if (Test-Path $xpi) { Remove-Item $xpi -Force }

# Use .NET ZipFile to build the archive with paths relative to $Source root.
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$zipStream = [System.IO.File]::Open($xpi, [System.IO.FileMode]::Create)
$zip = New-Object System.IO.Compression.ZipArchive($zipStream, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    $files = Get-ChildItem -Path $Source -Recurse -File
    foreach ($f in $files) {
        $relative = $f.FullName.Substring($Source.Length).TrimStart('\','/')
        $relative = $relative -replace '\\','/'
        # Skip the chromium-only manifest variant when packing for Firefox.
        if ($relative -ieq "manifest.chromium.json") { continue }

        # If manifest.firefox.json exists, prefer it and emit it as manifest.json.
        # Always inject browser_specific_settings.gecko.id (required for permanent install).
        if ($relative -ieq "manifest.json") {
            $firefoxManifest = Join-Path $Source "manifest.firefox.json"
            $sourceManifest = if (Test-Path $firefoxManifest) { $firefoxManifest } else { $f.FullName }
            $manifestObj = Get-Content -Raw -LiteralPath $sourceManifest | ConvertFrom-Json
            if (-not $manifestObj.browser_specific_settings) {
                $manifestObj | Add-Member -NotePropertyName "browser_specific_settings" -NotePropertyValue ([pscustomobject]@{
                    gecko = [pscustomobject]@{
                        id                = "atrium-browser-bridge@fiam.local"
                        strict_min_version = "109.0"
                    }
                })
            }
            $entry = $zip.CreateEntry("manifest.json", [System.IO.Compression.CompressionLevel]::Optimal)
            $stream = $entry.Open()
            try {
                $bytes = [System.Text.Encoding]::UTF8.GetBytes(($manifestObj | ConvertTo-Json -Depth 20))
                $stream.Write($bytes, 0, $bytes.Length)
            } finally { $stream.Dispose() }
            continue
        }
        if ($relative -ieq "manifest.firefox.json") { continue }

        $entry = $zip.CreateEntry($relative, [System.IO.Compression.CompressionLevel]::Optimal)
        $stream = $entry.Open()
        try {
            $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
            $stream.Write($bytes, 0, $bytes.Length)
        } finally {
            $stream.Dispose()
        }
    }
} finally {
    $zip.Dispose()
    $zipStream.Dispose()
}

Write-Host "Wrote $xpi"
Write-Host "Install in Firefox Developer Edition:"
Write-Host "  1. about:config -> xpinstall.signatures.required = false"
Write-Host "  2. about:addons -> gear -> Install Add-on From File -> select the .xpi"
