# One-click 浏览器启动本地对战客户端:拉起 clientd sidecar + Vite 前端,打开浏览器。
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\web_client.ps1
#
# 约定端口(与 client/.env.development 一致):
#   HTTP  = 127.0.0.1:17320   (REST 控制面)
#   WS    = 127.0.0.1:17321   (数据面;当前前端仅用作连接状态)
# 若 17320 已被某个 clientd 占用(health 应答),则复用而不重复启动。

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$HttpPort = 17320
$WsPort = 17321
$HealthUrl = "http://127.0.0.1:$HttpPort/health"
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

# 3) 启动 / 复用 clientd
$already = $false
$_clientdPid = $null
try {
    $r = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2
    $already = ($r.StatusCode -eq 200)
} catch {
    $already = $false
}

if ($already) {
    Write-Host "clientd 已在 $HttpPort 运行,直接复用。" -ForegroundColor Green
} else {
    Write-Host "启动 clientd(http=$HttpPort ws=$WsPort)…"
    $proc = Start-Process -FilePath $Py `
        -ArgumentList @("-m", "mj.clientd", "--host", "127.0.0.1",
                        "--http-port", "$HttpPort", "--ws-port", "$WsPort") `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
    $_clientdPid = $proc.Id
    Write-Host "clientd pid=$($_clientdPid)", "stdout=$OutLog" -ForegroundColor DarkGray

    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if ($proc.HasExited) {
            Write-Error "clientd 退出(代码 $($proc.ExitCode))。请查看 $ErrLog"
        }
        try {
            $r = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 1
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) { Write-Error "clientd 在 15s 内未就绪,请查看 $ErrLog" }
    Write-Host "clientd 就绪。" -ForegroundColor Green
}

# 4) Vite 前端(前台,按 Ctrl+C 退出并清理)
Write-Host "启动前端开发服务器并在浏览器打开…"
Push-Location (Join-Path $Root "client")
try {
    & npm run dev -- --open
} finally {
    Pop-Location
    if ((-not $already) -and $_clientdPid) {
        Write-Host "停止 clientd(pid=$_clientdPid)…"
        Stop-Process -Id $_clientdPid -Force -ErrorAction SilentlyContinue
    }
}
