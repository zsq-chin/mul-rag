<#
.SYNOPSIS
    Platform smoke test for SAGE role matrix, multimodal, graph, and health.
.DESCRIPTION
    Validates per-role access against real FastAPI routes. Requires three test
    accounts (superadmin, admin, user) and the running API base URL.

    Works in Windows PowerShell 5.1 and PowerShell 7.
    No third-party modules required.
.PARAMETER BaseUrl
    API base URL, e.g. http://localhost:5050
.PARAMETER SuperadminCredential
    PSCredential for the superadmin account.
.PARAMETER AdminCredential
    PSCredential for the admin account.
.PARAMETER UserCredential
    PSCredential for the ordinary user account.
.PARAMETER RemoteMultimodalBase
    Optional remote multimodal endpoint URL to check reachability.
.EXAMPLE
    $sa = Get-Credential -UserName superadmin
    $ad = Get-Credential -UserName admin
    $us = Get-Credential -UserName tester
    .\smoke_platform.ps1 -BaseUrl http://localhost:5050 `
        -SuperadminCredential $sa -AdminCredential $ad -UserCredential $us
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BaseUrl,

    [Parameter(Mandatory)]
    [System.Management.Automation.PSCredential]$SuperadminCredential,

    [Parameter(Mandatory)]
    [System.Management.Automation.PSCredential]$AdminCredential,

    [Parameter(Mandatory)]
    [System.Management.Automation.PSCredential]$UserCredential,

    [string]$RemoteMultimodalBase
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

$script:passCount = 0
$script:failCount = 0
$script:failures  = [System.Collections.Generic.List[string]]::new()
$script:tempUserId = $null
$script:adHeaders  = $null
$script:cleanupFailed = $false
$script:privilegedTestUserIds = [System.Collections.Generic.List[int]]::new()

function Write-Check {
    param([string]$Name, [int]$Expected, [int]$Actual, [string]$Detail)
    if ($Actual -eq $Expected) {
        Write-Host "  [PASS] $Name  ($Actual)" -ForegroundColor Green
        $script:passCount++
    } else {
        $msg = "  [FAIL] $Name  expected=$Expected actual=$Actual $Detail"
        Write-Host $msg -ForegroundColor Red
        $script:failCount++
        $script:failures.Add($msg)
    }
}

function Invoke-Api {
    <#
    .SYNOPSIS
        Invoke a REST method. Returns @{ StatusCode; Body }.
        Uses Invoke-WebRequest for Windows PowerShell 5.1 compatibility.
        Never echoes credentials or tokens.
        Disposes HTTP responses to avoid resource exhaustion under PS 5.1.
    #>
    param(
        [string]$Method,
        [string]$Path,
        [hashtable]$Headers,
        [object]$Body,
        [switch]$AsForm
    )
    $uri = "$BaseUrl/api$Path"
    $splat = @{
        Method          = $Method
        Uri             = $uri
        Headers         = $Headers
        ErrorAction     = 'Stop'
        UseBasicParsing = $true
        TimeoutSec      = 30
    }
    if ($Body -and $AsForm) {
        $splat.Body = $Body
        $splat.ContentType = 'application/x-www-form-urlencoded'
    } elseif ($Body) {
        $splat.Body = ($Body | ConvertTo-Json -Depth 10)
        $splat.ContentType = 'application/json'
    }
    $responseToDispose = $null
    try {
        $resp = Invoke-WebRequest @splat
        $responseToDispose = $resp
        $parsed = $null
        if ($resp.Content) {
            try { $parsed = $resp.Content | ConvertFrom-Json } catch { }
        }
        return @{ StatusCode = [int]$resp.StatusCode; Body = $parsed }
    } catch {
        $status = 0
        if ($_.Exception.Response) {
            $responseToDispose = $_.Exception.Response
            try { $status = [int]$_.Exception.Response.StatusCode } catch { }
        }
        return @{ StatusCode = $status; Body = $null }
    } finally {
        if ($null -ne $responseToDispose) {
            try { $responseToDispose.Close() } catch { }
            if ($responseToDispose -is [System.IDisposable]) {
                try { $responseToDispose.Dispose() } catch { }
            }
        }
    }
}

function Get-AuthHeaders {
    param([string]$Token)
    return @{ Authorization = "Bearer $Token" }
}

function Get-LoginResult {
    <# Logs in and returns @{ Token; StatusCode }. Never prints credentials.
       Disposes HTTP responses to avoid resource exhaustion under PS 5.1. #>
    param([System.Management.Automation.PSCredential]$Cred)
    $username = $Cred.UserName
    $password = $Cred.GetNetworkCredential().Password
    $formBody = "username=$([uri]::EscapeDataString($username))&password=$([uri]::EscapeDataString($password))"
    $responseToDispose = $null
    try {
        $resp = Invoke-WebRequest -Method Post -Uri "$BaseUrl/api/auth/token" `
            -Body $formBody -ContentType 'application/x-www-form-urlencoded' `
            -ErrorAction Stop -UseBasicParsing -TimeoutSec 30
        $responseToDispose = $resp
        $parsed = $resp.Content | ConvertFrom-Json
        return @{ Token = $parsed.access_token; StatusCode = [int]$resp.StatusCode }
    } catch {
        $status = 0
        if ($_.Exception.Response) {
            $responseToDispose = $_.Exception.Response
            try { $status = [int]$_.Exception.Response.StatusCode } catch { }
        }
        return @{ Token = $null; StatusCode = $status }
    } finally {
        if ($null -ne $responseToDispose) {
            try { $responseToDispose.Close() } catch { }
            if ($responseToDispose -is [System.IDisposable]) {
                try { $responseToDispose.Dispose() } catch { }
            }
        }
        $password = $null
        $formBody = $null
    }
}

function Test-RoleRoute {
    <# Data-driven route checker. $ExpectAllowed=$true means "not 401/403 and not a transport failure"; $false means exact 403. #>
    param(
        [array]$Routes,
        [hashtable]$Headers,
        [string]$RoleName,
        [bool]$ExpectAllowed
    )
    foreach ($route in $Routes) {
        $r = Invoke-Api -Method $route.Method -Path $route.Path -Headers $Headers
        $tag = "$($route.Method) /api$($route.Path) [$RoleName]"
        if ($ExpectAllowed) {
            # Status 0 is a transport failure (connection error, timeout) -- never PASS.
            # 2xx and upstream 5xx prove the role passed authorization.
            $ok = ($r.StatusCode -gt 0 -and $r.StatusCode -notin @(401, 403))
            Write-Check -Name $tag -Expected $true -Actual $ok -Detail "status=$($r.StatusCode)"
        } else {
            Write-Check -Name "$tag -> 403" -Expected 403 -Actual $r.StatusCode
        }
    }
}

# ---------------------------------------------------------------------------
# Cleanup helper
# ---------------------------------------------------------------------------

function Remove-TempUser {
    if ($null -ne $script:tempUserId -and $null -ne $script:adHeaders) {
        try {
            $r = Invoke-Api -Method Delete -Path "/auth/users/$script:tempUserId" -Headers $script:adHeaders
            if ($r.StatusCode -notin @(200, 204)) {
                $script:cleanupFailed = $true
                Write-Host "  [WARN] Cleanup returned status=$($r.StatusCode) for temp user id=$script:tempUserId" -ForegroundColor DarkYellow
            } else {
                Write-Host "  [INFO] Cleaned up temporary user id=$script:tempUserId" -ForegroundColor Yellow
            }
        } catch {
            $script:cleanupFailed = $true
            Write-Host "  [WARN] Failed to clean up temp user id=$script:tempUserId" -ForegroundColor DarkYellow
        }
    }
}

function Remove-PrivilegedTestUsers {
    <# Deletes any privileged test accounts created unexpectedly during role checks. #>
    if ($script:privilegedTestUserIds.Count -gt 0 -and $saHeaders) {
        foreach ($uid in $script:privilegedTestUserIds) {
            try {
                $r = Invoke-Api -Method Delete -Path "/auth/users/$uid" -Headers $saHeaders
                if ($r.StatusCode -notin @(200, 204)) {
                    $script:cleanupFailed = $true
                    Write-Host "  [WARN] Cleanup returned status=$($r.StatusCode) for privileged test user id=$uid" -ForegroundColor DarkYellow
                } else {
                    Write-Host "  [INFO] Cleaned up privileged test user id=$uid" -ForegroundColor Yellow
                }
            } catch {
                $script:cleanupFailed = $true
                Write-Host "  [WARN] Failed to clean up privileged test user id=$uid" -ForegroundColor DarkYellow
            }
        }
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

Write-Host "`n=== SAGE Platform Smoke Test ===" -ForegroundColor Cyan
Write-Host "Base URL: $BaseUrl`n"

# ---- Health ----
Write-Host "[1] Health check" -ForegroundColor Cyan
$h = Invoke-Api -Method Get -Path "/health"
Write-Check -Name "GET /api/health" -Expected 200 -Actual $h.StatusCode

# ---- Login ----
Write-Host "`n[2] Login (three roles)" -ForegroundColor Cyan
$saLogin = Get-LoginResult -Cred $SuperadminCredential
Write-Check -Name "POST /api/auth/token [superadmin]" -Expected 200 -Actual $saLogin.StatusCode

$adLogin = Get-LoginResult -Cred $AdminCredential
Write-Check -Name "POST /api/auth/token [admin]" -Expected 200 -Actual $adLogin.StatusCode

$usLogin = Get-LoginResult -Cred $UserCredential
Write-Check -Name "POST /api/auth/token [user]" -Expected 200 -Actual $usLogin.StatusCode

if ($saLogin.Token -and $adLogin.Token -and $usLogin.Token) {
    $saHeaders = Get-AuthHeaders -Token $saLogin.Token
    $script:adHeaders = Get-AuthHeaders -Token $adLogin.Token
    $usHeaders = Get-AuthHeaders -Token $usLogin.Token
} else {
    Write-Host "`nABORT: One or more logins failed. Cannot continue." -ForegroundColor Red
    exit 1
}

# ---- /auth/me (verify role for all three accounts) ----
Write-Host "`n[3] /auth/me" -ForegroundColor Cyan
$roleChecks = @(
    @{ Name = 'superadmin'; Headers = $saHeaders; ExpectedRole = 'superadmin' }
    @{ Name = 'admin';      Headers = $script:adHeaders; ExpectedRole = 'admin' }
    @{ Name = 'user';       Headers = $usHeaders; ExpectedRole = 'user' }
)
foreach ($rc in $roleChecks) {
    $r = Invoke-Api -Method Get -Path "/auth/me" -Headers $rc.Headers
    Write-Check -Name "GET /api/auth/me [$($rc.Name)]" -Expected 200 -Actual $r.StatusCode
    if ($r.Body -and $r.Body.role) {
        $role = $r.Body.role
        if ($role -eq $rc.ExpectedRole) {
            $matchVal = 1
        } else {
            $matchVal = 0
        }
        Write-Check -Name "  role==$($rc.ExpectedRole)" -Expected 1 -Actual $matchVal -Detail "role=$role"
    } else {
        Write-Check -Name "  role==$($rc.ExpectedRole) (response body)" -Expected 1 -Actual 0 -Detail "empty or malformed /auth/me body"
    }
}

# ---- Role route matrix (data-driven) ----
Write-Host "`n[4] Role route matrix" -ForegroundColor Cyan

# Routes any authenticated role can access
$anyRoleRoutes = @(
    @{ Method = 'Get'; Path = '/chat/' }
    @{ Method = 'Get'; Path = '/chat/agent' }
    @{ Method = 'Get'; Path = '/chat/default_agent' }
    @{ Method = 'Get'; Path = '/data/' }
    @{ Method = 'Get'; Path = '/chat/multimodal/kbs' }
    @{ Method = 'Get'; Path = '/chat/records' }
    @{ Method = 'Get'; Path = '/chat/threads' }
    @{ Method = 'Get'; Path = '/chat/user-models' }
)

foreach ($roleInfo in @(
    @{ Name = 'superadmin'; Headers = $saHeaders }
    @{ Name = 'admin';      Headers = $script:adHeaders }
    @{ Name = 'user';       Headers = $usHeaders }
)) {
    Test-RoleRoute -Routes $anyRoleRoutes `
        -Headers $roleInfo.Headers -RoleName $roleInfo.Name -ExpectAllowed $true
}

# Routes only superadmin can access
$superadminOnlyRoutes = @(
    @{ Method = 'Get'; Path = '/config' }
    @{ Method = 'Get'; Path = '/log' }
    @{ Method = 'Get'; Path = '/data/graph' }
    @{ Method = 'Get'; Path = '/data/graph/nodes?kgdb_name=default&num=1' }
    @{ Method = 'Get'; Path = '/statistics/top-questions?limit=1' }
    @{ Method = 'Get'; Path = '/multimodal/kb/list' }
    @{ Method = 'Get'; Path = '/k80/users' }
    @{ Method = 'Get'; Path = '/chat/models?model_provider=openai' }
    @{ Method = 'Get'; Path = '/chat/tools' }
)

# Superadmin allowed
Test-RoleRoute -Routes $superadminOnlyRoutes `
    -Headers $saHeaders -RoleName "superadmin" -ExpectAllowed $true

# Admin forbidden
Test-RoleRoute -Routes $superadminOnlyRoutes `
    -Headers $script:adHeaders -RoleName "admin" -ExpectAllowed $false

# User forbidden
Test-RoleRoute -Routes $superadminOnlyRoutes `
    -Headers $usHeaders -RoleName "user" -ExpectAllowed $false

# ---- User management ----
Write-Host "`n[5] User management" -ForegroundColor Cyan

$tempName = "smoke_test_$(Get-Random)"
$tempPass = "Smoke!$(Get-Random)Aa1"

try {
    # Admin creates ordinary user
    $r = Invoke-Api -Method Post -Path "/auth/users" -Headers $script:adHeaders -Body @{
        username = $tempName
        password = $tempPass
        role     = 'user'
    }
    Write-Check -Name "POST /api/auth/users [admin creates user]" -Expected 200 -Actual $r.StatusCode
    if ($r.Body -and $r.Body.id) {
        $script:tempUserId = $r.Body.id
        Write-Check -Name "  created user id" -Expected $true -Actual ($null -ne $script:tempUserId)
    }

    # Admin cannot create admin
    $r = Invoke-Api -Method Post -Path "/auth/users" -Headers $script:adHeaders -Body @{
        username = "smoke_bad_$(Get-Random)"
        password = $tempPass
        role     = 'admin'
    }
    Write-Check -Name "POST /api/auth/users [admin cannot create admin -> 403]" `
        -Expected 403 -Actual $r.StatusCode
    if ($r.StatusCode -eq 200 -and $r.Body -and $r.Body.id) {
        $script:privilegedTestUserIds.Add([int]$r.Body.id)
    }

    # Admin cannot create superadmin
    $r = Invoke-Api -Method Post -Path "/auth/users" -Headers $script:adHeaders -Body @{
        username = "smoke_bad2_$(Get-Random)"
        password = $tempPass
        role     = 'superadmin'
    }
    Write-Check -Name "POST /api/auth/users [admin cannot create superadmin -> 403]" `
        -Expected 403 -Actual $r.StatusCode
    if ($r.StatusCode -eq 200 -and $r.Body -and $r.Body.id) {
        $script:privilegedTestUserIds.Add([int]$r.Body.id)
    }

    # Admin lists users (sees only 'user' role in visible scope)
    $r = Invoke-Api -Method Get -Path "/auth/users" -Headers $script:adHeaders
    Write-Check -Name "GET /api/auth/users [admin lists users]" -Expected 200 -Actual $r.StatusCode
    if ($r.Body) {
        $onlyUserRole = (@($r.Body | Where-Object { $_.role -ne 'user' })).Count -eq 0
        Write-Check -Name "  admin sees only 'user' role" -Expected $true -Actual $onlyUserRole
    }
} finally {
    Remove-TempUser
    Remove-PrivilegedTestUsers
    $tempPass = $null
    $tempName = $null
}

if ($script:cleanupFailed) {
    $msg = "  [FAIL] Cleanup of temporary user failed"
    Write-Host $msg -ForegroundColor Red
    $script:failCount++
    $script:failures.Add($msg)
}

# ---- Remote multimodal reachability (no secrets) ----
if ($RemoteMultimodalBase) {
    Write-Host "`n[8] Remote multimodal endpoint reachability" -ForegroundColor Cyan
    try {
        $probe = Invoke-WebRequest -Uri $RemoteMultimodalBase -Method Head `
            -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
        Write-Check -Name "HEAD $RemoteMultimodalBase" -Expected 200 -Actual $probe.StatusCode
    } catch {
        $status = 0
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
        }
        # Accept any non-zero status as "reachable" (endpoint exists)
        $ok = ($status -gt 0)
        Write-Check -Name "HEAD $RemoteMultimodalBase [reachable]" `
            -Expected $true -Actual $ok -Detail "status=$status"
    }
}

# ---- Summary ----
Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "  Passed: $script:passCount" -ForegroundColor Green
Write-Host "  Failed: $script:failCount" -ForegroundColor $(if ($script:failCount -gt 0) { 'Red' } else { 'Green' })

if ($script:failures.Count -gt 0) {
    Write-Host "`nFailed checks:" -ForegroundColor Red
    foreach ($f in $script:failures) {
        Write-Host $f -ForegroundColor Red
    }
    exit 1
}

Write-Host "`nAll checks passed." -ForegroundColor Green
exit 0
