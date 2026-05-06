# setup-agents.ps1
# Run this ONCE to connect Agrostar analyst agents to your Claude Code.
# After this, any updates to the agents sync automatically via Google Drive.
#
# HOW TO RUN:
#   1. Open PowerShell (search "PowerShell" in Start menu)
#   2. Paste this command and press Enter:
#      irm https://raw.githubusercontent.com/rudra124/agrostar-analyst-agents/main/setup-agents.ps1 | iex

Write-Host ""
Write-Host "Agrostar Analyst Agents - Setup" -ForegroundColor Cyan
Write-Host "--------------------------------" -ForegroundColor Cyan

# ── Step 1: Find Google Drive path ──────────────────────────────────────────
Write-Host ""
Write-Host "Looking for your Google Drive..." -ForegroundColor Yellow

$possiblePaths = @(
    "$env:USERPROFILE\Google Drive\My Drive\Agrostar Analyst Agents",
    "$env:USERPROFILE\My Drive\Agrostar Analyst Agents",
    "G:\My Drive\Agrostar Analyst Agents",
    "G:\Agrostar Analyst Agents"
)

# Also scan CloudStorage style paths
$localAppData = $env:LOCALAPPDATA
$cloudStoragePath = "$localAppData\Google\DriveFS"
if (Test-Path $cloudStoragePath) {
    Get-ChildItem $cloudStoragePath -Directory | ForEach-Object {
        $possiblePaths += "$($_.FullName)\root\Agrostar Analyst Agents"
    }
}

$drivePath = $null
foreach ($path in $possiblePaths) {
    if (Test-Path $path) {
        $drivePath = $path
        Write-Host "   Found Drive at: $path" -ForegroundColor Green
        break
    }
}

if (-not $drivePath) {
    Write-Host ""
    Write-Host "   Could not find the 'Agrostar Analyst Agents' folder." -ForegroundColor Red
    Write-Host ""
    Write-Host "   Make sure you have:" -ForegroundColor White
    Write-Host "   1. Google Drive for Desktop installed and signed in with your Agrostar account"
    Write-Host "      -> https://www.google.com/drive/download"
    Write-Host "   2. Opened the shared Google Drive link Darpan sent you"
    Write-Host "   3. Right-clicked 'Agrostar Analyst Agents' -> 'Add shortcut to My Drive'"
    Write-Host "   4. Waited ~60 seconds for it to sync to your computer"
    Write-Host ""
    Write-Host "   Then run this script again." -ForegroundColor Yellow
    exit 1
}

# ── Step 2: Create ~/.claude/commands/ if needed ────────────────────────────
Write-Host ""
Write-Host "Setting up Claude commands folder..." -ForegroundColor Yellow

$claudeCommandsDir = "$env:USERPROFILE\.claude\commands"
if (-not (Test-Path $claudeCommandsDir)) {
    New-Item -ItemType Directory -Path $claudeCommandsDir -Force | Out-Null
}
Write-Host "   ~/.claude/commands/ ready" -ForegroundColor Green

# ── Step 3: Copy agent files (Windows symlinks need admin; copy is simpler) ──
Write-Host ""
Write-Host "Installing agents..." -ForegroundColor Yellow

$commandsDir = "$drivePath\commands"
if (-not (Test-Path $commandsDir)) {
    Write-Host "   No 'commands' subfolder found. Contact Darpan." -ForegroundColor Red
    exit 1
}

$linked = 0
Get-ChildItem "$commandsDir\*.md" | ForEach-Object {
    $dest = "$claudeCommandsDir\$($_.Name)"
    Copy-Item $_.FullName $dest -Force
    Write-Host "   Installed: $($_.Name)" -ForegroundColor Green
    $linked++
}

if ($linked -eq 0) {
    Write-Host "   No .md files found in $commandsDir" -ForegroundColor Red
    exit 1
}

# ── Step 4: Done ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Setup complete! $linked agent(s) installed." -ForegroundColor Green
Write-Host ""
Write-Host "To use in Claude Code:" -ForegroundColor White
Write-Host "   1. Open Claude Code"
Write-Host "   2. Type a slash command to activate an agent, e.g.:"

Get-ChildItem "$commandsDir\*.md" | ForEach-Object {
    $name = $_.BaseName
    Write-Host "      /$name"
}

Write-Host ""
Write-Host "NOTE (Windows): Unlike Mac, files are copied not linked." -ForegroundColor Yellow
Write-Host "When Darpan pushes updates, re-run this script to get the latest:" -ForegroundColor Yellow
Write-Host "   irm https://raw.githubusercontent.com/rudra124/agrostar-analyst-agents/main/setup-agents.ps1 | iex" -ForegroundColor Cyan
Write-Host ""
