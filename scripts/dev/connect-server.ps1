<#
.SYNOPSIS
  Ket noi Claude Code toi MCP Server PDCA tren may chu (mac dinh https://pdca.ppsvietnam.edu.vn/mcp).

.DESCRIPTION
  Hai che do:
    Plugin (mac dinh, danh cho nguoi dung thuong): cai plugin "pdca" tu marketplace cua repo, gom MCP,
      rule cong ty/phong ban va cac lenh /pdca:... ; token luu trong kho bi mat cua may (userConfig).
    Mcp (danh cho nguoi phat trien): chi dang ky MCP server bang "claude mcp add".
  Them -Mod de mod pdca-tasks (pane theo doi task) cung tro toi may chu qua bien moi truong nguoi dung.

  Token (pdca_...) do quan tri cap; script hoi bang o nhap an (khong hien ra man hinh, khong vao lich su
  lenh). Truoc khi dang ky, script goi thu whoami de chac token dung.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev\connect-server.ps1 -CheckOnly
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev\connect-server.ps1 -Mode Plugin
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev\connect-server.ps1 -Mode Mcp -Name pdca-server -Mod
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev\connect-server.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$Url = 'https://pdca.ppsvietnam.edu.vn/mcp',
    [ValidateSet('Plugin', 'Mcp')][string]$Mode = 'Plugin',
    [string]$Name = 'pdca-server',                  # ten MCP server (che do Mcp)
    [ValidateSet('local', 'user', 'project')][string]$Scope = 'user',  # pham vi "claude mcp add"
    [string]$Department = 'thu-nghiem-a',           # rule phong ban nap khi mo phien (che do Plugin)
    [string]$Marketplace = 'langtuananh2424/pdca-system',
    [switch]$Mod,         # dat PDCA_TOKEN, PDCA_SERVER_URL, CLAUDE_CODE_PLUGIN_DIRS cho mod pdca-tasks
    [switch]$CheckOnly,   # chi kiem tra may chu tra loi, khong dang ky gi
    [switch]$Remove,      # go dang ky (MCP, plugin, bien moi truong cua mod)
    [switch]$Force,       # ghi de ma khong hoi
    [switch]$DryRun       # in cac buoc, khong thuc hien
)

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ModDir = Join-Path $Root 'mods\pdca-tasks'
$PluginId = 'pdca@pdca-system'

function Step([string]$text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Info([string]$text) { Write-Host "    $text" }
function Warn([string]$text) { Write-Host "    ! $text" -ForegroundColor Yellow }

function Invoke-Mcp([string]$Token) {
    $body = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"whoami","arguments":{}}}'
    $headers = @{ Accept = 'application/json, text/event-stream' }
    if ($Token) { $headers['Authorization'] = "Bearer $Token" }
    try {
        return Invoke-RestMethod -Uri $Url -Method Post -ContentType 'application/json' -Headers $headers -Body $body -TimeoutSec 20
    } catch {
        throw "Khong goi duoc $Url : $($_.Exception.Message)"
    }
}

function Invoke-Claude([string[]]$ClaudeArgs, [string]$StdIn = $null) {
    if ($StdIn) { $StdIn | & claude @ClaudeArgs 2>&1 | Out-Null } else { & claude @ClaudeArgs 2>&1 | Out-Null }
    return $LASTEXITCODE
}

# --- 1. May chu co tra loi khong --------------------------------------------------------
Step "Kiem tra may chu $Url"
$probe = Invoke-Mcp ''
$text = [string]$probe.result.content[0].text
if ($text -notmatch 'unauthorized') { throw "May chu tra loi bat thuong (mong doi 'unauthorized'): $text" }
Info 'May chu tra loi dung (tu choi vi chua co token).'
if ($CheckOnly) { return }

$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) { throw 'Khong tim thay lenh claude (Claude Code CLI).' }

# --- 2. Go dang ky ----------------------------------------------------------------------
if ($Remove) {
    Step 'Go dang ky'
    if ($DryRun) { Info "[dry-run] claude mcp remove $Name -s $Scope; claude plugin uninstall $PluginId; xoa bien moi truong mod" }
    else {
        [void](Invoke-Claude @('mcp', 'remove', $Name, '-s', $Scope))
        [void](Invoke-Claude @('plugin', 'uninstall', $PluginId))
        foreach ($var in 'PDCA_TOKEN', 'PDCA_SERVER_URL') { [Environment]::SetEnvironmentVariable($var, $null, 'User') }
        $dirs = [Environment]::GetEnvironmentVariable('CLAUDE_CODE_PLUGIN_DIRS', 'User')
        if ($dirs) {
            $kept = @($dirs -split ';' | Where-Object { $_ -ne '' -and $_ -ne $ModDir })
            [Environment]::SetEnvironmentVariable('CLAUDE_CODE_PLUGIN_DIRS', ($(if ($kept.Count) { $kept -join ';' } else { $null })), 'User')
        }
        Info 'Da go (bo qua muc khong ton tai). Tat han app Claude roi mo lai de nhan bien moi truong.'
    }
    return
}

# --- 3. Token ---------------------------------------------------------------------------
Step 'Doc token'
$token = ''
if ($DryRun) { Info '[dry-run] hoi token bang o nhap an, goi thu whoami' }
else {
    $secure = Read-Host 'Nhap token PDCA (pdca_...), khong hien ra man hinh' -AsSecureString
    $token = [System.Net.NetworkCredential]::new('', $secure).Password
    if ($token -notmatch '^pdca_[A-Za-z0-9_-]{43}$') { throw 'Token sai dinh dang (phai bat dau bang pdca_ va dai 48 ky tu).' }
    $who = Invoke-Mcp $token
    if ($who.result.isError) { throw "Token khong dung hoac da het han: $([string]$who.result.content[0].text)" }
    $me = ([string]$who.result.content[0].text) | ConvertFrom-Json
    Info "Dang nhap duoc: $($me.name) (vai tro $($me.role))."
}

# --- 4. Dang ky -------------------------------------------------------------------------
if ($Mode -eq 'Plugin') {
    Step "Cai plugin $PluginId"
    $marketplaces = (& claude plugin marketplace list 2>&1 | Out-String)
    if ($marketplaces -notmatch 'pdca-system') {
        if ($DryRun) { Info "[dry-run] claude plugin marketplace add $Marketplace" }
        elseif ((Invoke-Claude @('plugin', 'marketplace', 'add', $Marketplace)) -ne 0) { throw "Khong them duoc marketplace $Marketplace." }
        else { Info "Da them marketplace $Marketplace." }
    } else { Info 'Marketplace pdca-system da co.' }

    $installed = (& claude plugin list 2>&1 | Out-String)
    if ($installed -match 'pdca@pdca-system' -and -not $Force) { Info 'Plugin pdca da cai.' }
    elseif ($DryRun) { Info "[dry-run] claude plugin install $PluginId --config server_url=$Url --config department=$Department" }
    else {
        $code = Invoke-Claude @('plugin', 'install', $PluginId, '--config', "server_url=$Url", '--config', "department=$Department")
        if ($code -ne 0) { throw "Khong cai duoc plugin $PluginId (ma $code)." }
        Info 'Da cai plugin pdca.'
    }

    if ($DryRun) { Info '[dry-run] luu token vao cau hinh plugin bang stdin (khong nam tren dong lenh)' }
    else {
        $values = @{ server_url = $Url; api_token = $token; department = $Department } | ConvertTo-Json -Compress
        $code = Invoke-Claude @('plugin', 'configure', $PluginId, '--values-stdin') $values
        if ($code -ne 0) { throw "Khong luu duoc cau hinh plugin (ma $code). Chay 'claude plugin configure $PluginId' de xem." }
        Info 'Da luu server_url, department va token (kho bi mat cua may).'
    }
} else {
    Step "Dang ky MCP server '$Name' (pham vi $Scope)"
    if ($DryRun) { Info "[dry-run] claude mcp add --transport http $Name $Url --header (Authorization) -s $Scope" }
    else {
        [void](Invoke-Claude @('mcp', 'remove', $Name, '-s', $Scope))
        $code = Invoke-Claude @('mcp', 'add', '--transport', 'http', '-s', $Scope, $Name, $Url, '--header', "Authorization: Bearer $token")
        if ($code -ne 0) { throw "Khong them duoc MCP server (ma $code)." }
        Info "Da them '$Name'. Luu y: token nam trong ~/.claude.json dang chu thuong; nguoi dung thuong nen dung -Mode Plugin."
    }
}

# --- 5. Mod pdca-tasks ------------------------------------------------------------------
if ($Mod) {
    Step 'Tro mod pdca-tasks toi may chu (bien moi truong nguoi dung)'
    if (-not (Test-Path (Join-Path $ModDir '.claude-plugin\plugin.json'))) { Warn "Khong thay $ModDir; bo qua mod." }
    else {
        $current = [Environment]::GetEnvironmentVariable('PDCA_TOKEN', 'User')
        if ($current -and -not $Force -and -not $DryRun) {
            $answer = Read-Host 'PDCA_TOKEN hien co (co the la token local). Ghi de bang token may chu? (y/N)'
            if ($answer -notmatch '^[yY]') { Info 'Giu nguyen bien moi truong.'; $Mod = $false }
        }
        if ($Mod) {
            if ($DryRun) { Info '[dry-run] dat PDCA_TOKEN, PDCA_SERVER_URL; them mod vao CLAUDE_CODE_PLUGIN_DIRS' }
            else {
                [Environment]::SetEnvironmentVariable('PDCA_TOKEN', $token, 'User')
                [Environment]::SetEnvironmentVariable('PDCA_SERVER_URL', $Url, 'User')
                $dirs = [Environment]::GetEnvironmentVariable('CLAUDE_CODE_PLUGIN_DIRS', 'User')
                $list = @(); if ($dirs) { $list = @($dirs -split ';' | Where-Object { $_ -ne '' }) }
                if ($list -notcontains $ModDir) { $list += $ModDir }
                [Environment]::SetEnvironmentVariable('CLAUDE_CODE_PLUGIN_DIRS', ($list -join ';'), 'User')
                Info 'Da dat bien moi truong cho mod.'
            }
        }
    }
}

Step 'Xong'
Info "May chu: $Url"
Info 'Tat han app Claude roi mo lai; trong phien moi kiem tra bang /mcp (hoac go /pdca:viec-cua-toi).'
Info 'Go dang ky: scripts\dev\connect-server.ps1 -Remove'
