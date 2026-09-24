# One-click 浏览器启动本地对战客户端:拉起 clientd sidecar + Vite 前端,打开浏览器。
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\web_client.ps1
#
# 默认端口(与 client/src/service/config.ts 回退一致):
#   HTTP  = 127.0.0.1:17320   (REST 控制面)
#   WS    = 127.0.0.1:17321   (数据面;当前前端仅用作连接状态)
# 默认端口被其他程序占用时,按偶数 HTTP + 后继 WS 端口向上寻找空闲端口对。

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "正在启动本地对战客户端(浏览器)…"

$BaseHttpPort = 17320
$MaxHttpPort = 17418
$HttpPort = $null
$WsPort = $null
$HealthUrl = $null
$LogDir = Join-Path $Root "local"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$OutLog = Join-Path $LogDir "web-clientd.out.log"
$ErrLog = Join-Path $LogDir "web-clientd.err.log"

# 1) 选 Python(优先仓库 .venv)
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py)) {
    $Py = "python"
    if (-not (Get-Command $Py -ErrorAction SilentlyContinue)) {
        Write-Error "未找到 Python。请先安装并放到 PATH,或在仓库根使用 .venv\Scripts\python.exe。"
    }
}

# 1.5) clientd 运行依赖自检(mj/clientd:纯标准库 + numpy + websockets)
# .venv 存在但为空(缺依赖)时,给出可执行的修复命令,而不是让 clientd 抛 traceback。
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
$missingDeps = @()
foreach ($mod in @("numpy", "websockets")) {
    & $Py -c "import $mod" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { $missingDeps += $mod }
}
$ErrorActionPreference = $prevEap
if ($missingDeps.Count -gt 0) {
    $depList = $missingDeps -join " "
    Write-Error "运行依赖缺失:$Py 缺少 $depList。修复:& '$Py' -m pip install $depList"
    exit 1
}

# 2) 前端依赖检查
if (-not (Test-Path -LiteralPath (Join-Path $Root "client\node_modules"))) {
    Write-Warning "client\node_modules 缺失,正在 npm install(首次会较慢)…"
    Push-Location (Join-Path $Root "client")
    try { npm install } finally { Pop-Location }
}

# 3) 选可用端口对;目标端口已有旧 clientd 时重启它
function Get-ClientdListenerPids {
    param([int[]]$Ports)
    # 这几个端口上没有监听时,Get-NetTCPConnection 会把「找不到任何匹配的
    # MSFT_NetTCPConnection 对象」当成错误抛出,配合 -ErrorAction Stop 直接
    # 终止脚本 —— 实测 Win10 + PS 5.1 必现,而"端口空闲"恰恰是首次启动的
    # 正常状态,等于这条一键链路只在已有 clientd 在跑时才可能成功。
    try {
        $listeners = @(Get-NetTCPConnection -State Listen `
            -LocalPort $Ports -ErrorAction Stop)
    } catch {
        # 复核一次:不带端口过滤还能查出监听,说明 CIM 是好的,只是这几个端口
        # 没人监听,按空结果返回;连这都查不出来才是真的查不了,该报错。
        $anyListener = @()
        try {
            $anyListener = @(Get-NetTCPConnection -State Listen -ErrorAction Stop)
        } catch {
            throw "无法查询本机监听端口: $($_.Exception.Message)"
        }
        if ($anyListener.Count -eq 0) {
            throw "本机监听端口查询返回空结果,无法判断端口是否被占用。"
        }
        return @()
    }
    return @($listeners | Select-Object -ExpandProperty OwningProcess |
        Sort-Object -Unique)
}

function Stop-ClientdListeners {
    param([int[]]$ProcessIds)
    foreach ($processId in $ProcessIds) {
        Write-Host "发现旧 clientd(pid=$processId),正在关闭…"
        Stop-Process -Id $processId -Force -ErrorAction Stop
    }
}

$portsSelected = $false
for ($candidateHttp = $BaseHttpPort; $candidateHttp -le $MaxHttpPort; $candidateHttp += 2) {
    $candidateWs = $candidateHttp + 1
    $candidatePorts = @($candidateHttp, $candidateWs)
    $candidatePids = @(Get-ClientdListenerPids -Ports $candidatePorts)
    $stalePids = @()

    foreach ($processId in $candidatePids) {
        $oldProcess = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
        if ($oldProcess -and
            $oldProcess.CommandLine -match '(?:^|\s)-m\s+"?mj\.clientd"?(?:\s|$)') {
            $stalePids += [int]$processId
        } else {
            Write-Warning "端口对 $candidateHttp/$candidateWs 被其他程序占用(pid=$processId),不会终止该进程。"
        }
    }

    if ($stalePids.Count -gt 0) {
        Stop-ClientdListeners -ProcessIds $stalePids
        for ($i = 0; $i -lt 40; $i++) {
            $remainingPids = @(Get-ClientdListenerPids -Ports $candidatePorts)
            if ($remainingPids.Count -eq 0) { break }
            Start-Sleep -Milliseconds 250
        }
    }

    $remainingPids = @(Get-ClientdListenerPids -Ports $candidatePorts)
    if ($remainingPids.Count -gt 0) {
        Write-Warning "端口对 $candidateHttp/$candidateWs 不可用,尝试下一组。"
        continue
    }

    $HttpPort = $candidateHttp
    $WsPort = $candidateWs
    $portsSelected = $true
    break
}

if (-not $portsSelected) {
    throw "端口范围 $BaseHttpPort-$($MaxHttpPort + 1) 内没有可用的 HTTP/WS 端口对。"
}
$HealthUrl = "http://127.0.0.1:$HttpPort/health"
Write-Host "使用端口 http=$HttpPort ws=$WsPort。"

Write-Host "启动 clientd(http=$HttpPort ws=$WsPort)…"
$proc = Start-Process -FilePath $Py `
    -ArgumentList @("-m", "mj.clientd", "--host", "127.0.0.1",
                    "--http-port", "$HttpPort", "--ws-port", "$WsPort") `
    -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
$StartedPid = $proc.Id
Write-Host "clientd pid=$StartedPid", "stdout=$OutLog" -ForegroundColor DarkGray

try {
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        $proc.Refresh()
        if ($proc.HasExited) {
            throw "clientd 退出(代码 $($proc.ExitCode))。请查看 $ErrLog"
        }
        try {
            $r = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 1
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) { throw "clientd 在 15s 内未就绪,请查看 $ErrLog" }
    Write-Host "clientd 就绪。" -ForegroundColor Green
} catch {
    Stop-Process -Id $StartedPid -Force -ErrorAction SilentlyContinue
    throw
}

# 4) Vite 前端(前台,按 Ctrl+C 退出并清理)
Write-Host "启动前端开发服务器并在浏览器打开…"
$PreviousViteHttpBase = $env:VITE_HTTP_BASE
$PreviousViteWsEndpoint = $env:VITE_WS_ENDPOINT
Push-Location (Join-Path $Root "client")
try {
    $env:VITE_HTTP_BASE = "http://127.0.0.1:$HttpPort"
    $env:VITE_WS_ENDPOINT = "ws://127.0.0.1:$WsPort/ws"
    & npm run dev -- --open
} finally {
    Pop-Location
    if ($null -eq $PreviousViteHttpBase) {
        Remove-Item Env:VITE_HTTP_BASE -ErrorAction SilentlyContinue
    } else {
        $env:VITE_HTTP_BASE = $PreviousViteHttpBase
    }
    if ($null -eq $PreviousViteWsEndpoint) {
        Remove-Item Env:VITE_WS_ENDPOINT -ErrorAction SilentlyContinue
    } else {
        $env:VITE_WS_ENDPOINT = $PreviousViteWsEndpoint
    }
    if ($StartedPid) {
        $startedProcess = Get-Process -Id $StartedPid -ErrorAction SilentlyContinue
        if ($startedProcess) {
            Write-Host "停止 clientd(pid=$StartedPid)…"
            Stop-Process -Id $StartedPid -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "web_client 退出时会停止本次启动的 clientd；下次运行会重启旧实例。"
