<#
.SYNOPSIS
  Dung nhanh PDCA tren may ca nhan (Windows): Docker Compose, token, MCP cho Claude Code, mod pdca-tasks.

.DESCRIPTION
  Cac buoc (bo qua buoc nao bang cong tac -No...):
    1. Bat Docker Desktop neu chua chay.
    2. Tao deploy\.env (mat khau ngau nhien, cong DB trong, PDCA_ENV=dev) neu chua co.
    3. docker compose --profile apps up -d --build, doi MCP Server san sang.
    4. Cap token cho nhan vien va truong phong mau (token KHONG in ra man hinh).
    5. Dang ky MCP server "pdca" (nhan vien) va "pdca-head" (truong phong) cho Claude Code.
    6. Dat bien moi truong nguoi dung PDCA_TOKEN, CLAUDE_CODE_PLUGIN_DIRS de mod pdca-tasks tu nap.
  Chay lai an toan: buoc nao da xong thi bo qua, tru khi dung -Force.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev\setup-local.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev\setup-local.ps1 -DryRun
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev\setup-local.ps1 -Reset -Force
#>
[CmdletBinding()]
param(
    [string]$HeadEmail = 'head.a@example.com',
    [string]$StaffEmail = 'staff.a1@example.com',
    [switch]$Down,       # tat stack (giu du lieu) roi thoat
    [switch]$Reset,      # xoa du lieu (volume) truoc khi dung lai tu seed
    [switch]$Force,      # cap lai token va ghi de cau hinh MCP/bien moi truong
    [switch]$NoTokens,   # khong cap token (keo theo bo qua MCP va mod)
    [switch]$NoMcp,      # khong dang ky MCP server cho Claude Code
    [switch]$NoMod,      # khong dat bien moi truong cho mod pdca-tasks
    [switch]$DryRun      # chi in cac buoc, khong thuc hien
)

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ComposeFile = Join-Path $Root 'deploy\docker-compose.yml'
$EnvFile = Join-Path $Root 'deploy\.env'
$ModDir = Join-Path $Root 'mods\pdca-tasks'

function Step([string]$text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Info([string]$text) { Write-Host "    $text" }
function Warn([string]$text) { Write-Host "    ! $text" -ForegroundColor Yellow }

function Invoke-Compose([string[]]$ComposeArgs) {
    & docker compose -f $ComposeFile @ComposeArgs
    if ($LASTEXITCODE -ne 0) { throw "docker compose $($ComposeArgs -join ' ') loi (ma $LASTEXITCODE)" }
}

function New-HexSecret {
    $bytes = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Test-PortInUse([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne(300)) { $client.EndConnect($async); return $true }
        return $false
    } catch { return $false } finally { $client.Close() }
}

function Get-EnvValue([string]$Name, [string]$Default) {
    if (-not (Test-Path $EnvFile)) { return $Default }
    $line = Get-Content $EnvFile | Where-Object { $_ -match "^$Name=" } | Select-Object -First 1
    if ($null -eq $line) { return $Default }
    $value = ($line -split '=', 2)[1].Trim()
    if ($value -eq '') { return $Default }
    return $value
}

# --- 0. Tat stack -----------------------------------------------------------------------
if ($Down) {
    Step 'Tat stack (giu du lieu)'
    if ($DryRun) { Info '[dry-run] docker compose --profile apps down' } else { Invoke-Compose @('--profile', 'apps', 'down') }
    return
}

# --- 1. Docker --------------------------------------------------------------------------
Step 'Kiem tra Docker'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Chua cai Docker Desktop.' }
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path $desktop)) { throw 'Docker chua chay va khong tim thay Docker Desktop.' }
    if ($DryRun) { Info '[dry-run] bat Docker Desktop va doi toi da 120 giay' }
    else {
        Info 'Bat Docker Desktop...'
        Start-Process $desktop
        $deadline = (Get-Date).AddSeconds(120)
        do {
            Start-Sleep -Seconds 3
            & docker info *> $null
        } while ($LASTEXITCODE -ne 0 -and (Get-Date) -lt $deadline)
        if ($LASTEXITCODE -ne 0) { throw 'Docker chua san sang sau 120 giay.' }
    }
}
Info 'Docker san sang.'

# --- 2. deploy\.env ---------------------------------------------------------------------
Step 'Tao deploy\.env neu chua co'
if (Test-Path $EnvFile) {
    Info 'Da co deploy\.env, giu nguyen.'
} else {
    $dbPort = 5432
    while (Test-PortInUse $dbPort) { $dbPort++ }
    $mcpPort = 8010
    while (Test-PortInUse $mcpPort) { $mcpPort++ }
    Info "Cong DB $dbPort, cong MCP $mcpPort"
    if ($DryRun) { Info '[dry-run] ghi deploy\.env tu deploy\.env.example' }
    else {
        $text = [System.IO.File]::ReadAllText((Join-Path $Root 'deploy\.env.example'))
        $text = $text -replace '(?m)^POSTGRES_PASSWORD=.*$', ('POSTGRES_PASSWORD=' + (New-HexSecret))
        $text = $text -replace '(?m)^FLYWAY_PASSWORD=.*$', ('FLYWAY_PASSWORD=' + (New-HexSecret))
        $text = $text -replace '(?m)^PDCA_APP_PASSWORD=.*$', ('PDCA_APP_PASSWORD=' + (New-HexSecret))
        $text = $text -replace '(?m)^PDCA_READONLY_PASSWORD=.*$', ('PDCA_READONLY_PASSWORD=' + (New-HexSecret))
        $text = $text -replace '(?m)^PDCA_ENV=.*$', 'PDCA_ENV=dev'
        $text = $text -replace '(?m)^PDCA_DOMAIN=.*$', 'PDCA_DOMAIN=localhost'
        $text = $text -replace '(?m)^DB_PORT=.*$', "DB_PORT=$dbPort"
        $text = $text -replace '(?m)^MCP_HTTP_PORT=.*$', "MCP_HTTP_PORT=$mcpPort"
        # Ghi UTF-8 khong BOM, LF: docker compose doc .env nghiem ngat.
        [System.IO.File]::WriteAllText($EnvFile, ($text -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))
        Info 'Da tao deploy\.env (mat khau ngau nhien, khong commit).'
    }
}
$McpPort = [int](Get-EnvValue 'MCP_HTTP_PORT' '8010')
$McpUrl = "http://localhost:$McpPort/mcp"

# --- 3. Compose -------------------------------------------------------------------------
Step 'Dung stack (db, flyway, mcp, scheduler)'
if ($DryRun) {
    if ($Reset) { Info '[dry-run] docker compose --profile apps down -v' }
    Info '[dry-run] docker compose --profile apps up -d --build; doi MCP san sang'
} else {
    if ($Reset) { Invoke-Compose @('--profile', 'apps', 'down', '-v') }
    Invoke-Compose @('--profile', 'apps', 'up', '-d', '--build')
    Info "Doi MCP Server tai $McpUrl ..."
    $ready = $false
    $deadline = (Get-Date).AddSeconds(120)
    $body = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"whoami","arguments":{}}}'
    while (-not $ready -and (Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $McpUrl -Method Post -Body $body -ContentType 'application/json' `
                -Headers @{ Accept = 'application/json, text/event-stream' } -UseBasicParsing -TimeoutSec 5
            if ($resp.Content -match 'unauthorized') { $ready = $true }
        } catch {
            $text = ''
            try { $text = [string]$_.ErrorDetails.Message } catch { }
            if ($text -match 'unauthorized') { $ready = $true }
        }
        if (-not $ready) { Start-Sleep -Seconds 2 }
    }
    if (-not $ready) { throw 'MCP Server chua tra loi sau 120 giay: xem docker compose logs mcp.' }
    Info 'MCP Server san sang.'
}

if ($NoTokens) { Step 'Bo qua token, MCP, mod (-NoTokens)'; return }

# --- 4. Token ---------------------------------------------------------------------------
function New-PdcaToken([string]$Email, [string]$Label) {
    $out = & docker compose -f $ComposeFile exec -T mcp pdca-admin token issue --email $Email --label $Label 2>&1 | Out-String
    $m = [regex]::Match($out, 'pdca_[A-Za-z0-9_-]{43}')
    if (-not $m.Success) { throw "Khong cap duoc token cho $Email (dung seed mau chua?). Ket qua: $out" }
    return $m.Value
}

$claude = Get-Command claude -ErrorAction SilentlyContinue
$registered = ''
if ($claude) {
    Push-Location $Root
    try { $registered = (& claude mcp list 2>&1 | Out-String) } finally { Pop-Location }
}
$needStaffMcp = (-not $NoMcp) -and ($Force -or $registered -notmatch '(?m)^pdca:')
$needHeadMcp = (-not $NoMcp) -and ($Force -or $registered -notmatch '(?m)^pdca-head:')
$currentToken = [Environment]::GetEnvironmentVariable('PDCA_TOKEN', 'User')
$needModToken = (-not $NoMod) -and ($Force -or [string]::IsNullOrEmpty($currentToken))

Step 'Cap token (khong in ra man hinh)'
$staffToken = $null
$headToken = $null
if ($needStaffMcp) {
    if ($DryRun) { Info "[dry-run] cap token $StaffEmail" } else { $staffToken = New-PdcaToken $StaffEmail 'setup-local-staff' }
}
if ($needHeadMcp -or $needModToken) {
    if ($DryRun) { Info "[dry-run] cap token $HeadEmail" } else { $headToken = New-PdcaToken $HeadEmail 'setup-local-head' }
}
if (-not ($needStaffMcp -or $needHeadMcp -or $needModToken)) { Info 'Khong can cap token moi (dung -Force de cap lai).' }

# --- 5. MCP cho Claude Code -------------------------------------------------------------
Step 'Dang ky MCP server cho Claude Code (pham vi project nay)'
if ($NoMcp) { Info 'Bo qua (-NoMcp).' }
elseif (-not $claude) { Warn 'Khong tim thay lenh claude; bo qua. Tu them bang: claude mcp add --transport http ...' }
else {
    foreach ($item in @(@{ Name = 'pdca'; Need = $needStaffMcp; Token = $staffToken }, @{ Name = 'pdca-head'; Need = $needHeadMcp; Token = $headToken })) {
        if (-not $item.Need) { Info "$($item.Name): da co, giu nguyen."; continue }
        if ($DryRun) { Info "[dry-run] claude mcp add $($item.Name) $McpUrl"; continue }
        Push-Location $Root
        try {
            & claude mcp remove $item.Name 2>&1 | Out-Null
            & claude mcp add --transport http $item.Name $McpUrl --header "Authorization: Bearer $($item.Token)" 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { Warn "Khong them duoc $($item.Name)." } else { Info "$($item.Name): da them." }
        } finally { Pop-Location }
    }
}

# --- 6. Mod pdca-tasks ------------------------------------------------------------------
Step 'Cau hinh mod pdca-tasks (bien moi truong nguoi dung)'
if ($NoMod) { Info 'Bo qua (-NoMod).' }
elseif (-not (Test-Path (Join-Path $ModDir '.claude-plugin\plugin.json'))) { Warn "Khong thay $ModDir (nhanh chua co mod?)." }
else {
    if ($needModToken) {
        if ($DryRun) { Info '[dry-run] dat PDCA_TOKEN (nguoi dung)' }
        else { [Environment]::SetEnvironmentVariable('PDCA_TOKEN', $headToken, 'User'); Info 'Da dat PDCA_TOKEN.' }
    } else { Info 'PDCA_TOKEN da co, giu nguyen.' }
    if ($McpPort -ne 8010) {
        if ($DryRun) { Info "[dry-run] dat PDCA_SERVER_URL=$McpUrl" }
        else { [Environment]::SetEnvironmentVariable('PDCA_SERVER_URL', $McpUrl, 'User'); Info 'Da dat PDCA_SERVER_URL.' }
    }
    $dirs = [Environment]::GetEnvironmentVariable('CLAUDE_CODE_PLUGIN_DIRS', 'User')
    $list = @()
    if (-not [string]::IsNullOrEmpty($dirs)) { $list = @($dirs -split ';' | Where-Object { $_ -ne '' }) }
    if ($list -contains $ModDir) { Info 'CLAUDE_CODE_PLUGIN_DIRS da tro toi mod.' }
    elseif ($DryRun) { Info "[dry-run] them $ModDir vao CLAUDE_CODE_PLUGIN_DIRS" }
    else {
        [Environment]::SetEnvironmentVariable('CLAUDE_CODE_PLUGIN_DIRS', (($list + $ModDir) -join ';'), 'User')
        Info 'Da them mod vao CLAUDE_CODE_PLUGIN_DIRS.'
    }
}

# --- Tong ket ---------------------------------------------------------------------------
Step 'Xong'
Info "MCP Server: $McpUrl"
Info "Tai khoan mau: nhan vien $StaffEmail (server 'pdca'), truong phong $HeadEmail (server 'pdca-head')"
Info 'Tat han app Claude roi mo lai de nhan bien moi truong; trong phien moi go /pdca-tasks.'
Info 'Tat stack: scripts\dev\setup-local.ps1 -Down   |   Lam lai tu seed: -Reset'
