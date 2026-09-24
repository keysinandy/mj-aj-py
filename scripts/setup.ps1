# setup 引导(Windows):保证存在 Python >= 3.10,再执行 scripts\setup.py。
# 用法:powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [--install] [--venv] ...
#
# 顺序:
#   1. 按 $candidates 的顺序探测,取第一个「满足 >= 3.10 且不超出 Rust 内核
#      支持上限」的解释器;带版本号的候选排在前面,好命中已装的旧版,
#      py -3 / python3 / python 是兜底(通常指向最新版);
#   2. 只有「能跑但超出内核上限」的解释器(例如只装了 3.14)→ 不询问,
#      直接安装 Python 3.11 后再用它;
#   3. 全都低于 3.10 → 同样直接安装 Python 3.11;
#   4. 用选中的解释器运行 scripts\setup.py(参数透传)。
#
# 版本判据只在 scripts\setup.py 的 RUST_KERNEL_MAX_PY 一处维护:本脚本读
# --interpreter-check 的退出码,不要在这里再抄一份版本号。
#
# 不想让本脚本自动安装 Python、或不想让 setup.py 自动重建 .venv:
#   设 MJ_SETUP_NO_AUTO_INSTALL=1
#
# 本文件必须存成 UTF-8 with BOM:PowerShell 5.1 读无 BOM 的 .ps1 会按
# ANSI(本机 936)解码,中文乱码之外,错位的双字节还会吞掉后面的 ASCII
# 字符(含收尾引号),报出一堆与真实病因无关的语法错误。

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SetupPy = Join-Path $Root "scripts\setup.py"
$NoAutoInstall = ($env:MJ_SETUP_NO_AUTO_INSTALL -and
    $env:MJ_SETUP_NO_AUTO_INSTALL -notin @("0", "false", "no"))

# PS 5.1 调用原生命令有两个坑,统一在这两个函数里绕开:
#   1) py -3.11 在「该版本未安装」时会往 stderr 写 [ERROR] No runtime
#      installed...,而 $ErrorActionPreference = "Stop" 会把原生命令的 stderr
#      升级成终止错误(2>$null 也挡不住)→ 这里临时降级为 Continue;
#   2) $LASTEXITCODE 必须在同一作用域内紧接着读。先给它赋初值会创建函数
#      局部变量并遮蔽它,原生命令的更新落不到被读的那个变量上,实测会让
#      所有候选恒判 False。
# 另外别用 -c 'print("...")' 这类含字符串字面量的程序:PS 5.1 传参给原生
# 命令会剥掉内嵌双引号,Python 收到裸标识符会报 NameError。

function Invoke-PyCapture([string]$exe, [string[]]$preArgs) {
    # 只要退出码和 stdout;stderr(如 py 的版本未安装提示)丢弃。
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $exe @preArgs 2>$null
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    return @{ Code = $code; Out = ($out -join [Environment]::NewLine) }
}

function Invoke-PyVisible([string]$exe, [string[]]$preArgs) {
    # 输出直接落到控制台(安装过程要看得见),只把退出码带回来。
    # 必须用 Out-Host 把原生命令的 stdout 送出函数体:否则它会留在本函数的
    # 输出流里,调用方 `$code = Invoke-PyVisible ...` 拿到的是「全部 stdout
    # 文本 + 退出码」拼成的数组,和 0 比较永远为假 —— 实测把 py install /
    # winget 两次成功的安装都判成了失败(警告里打印的"退出码"其实是
    # winget 自己的输出文字)。
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $exe @preArgs 2>&1 | Out-Host
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    return $code
}

# 0 = 可用且支持 Rust 内核;2 = 可用但超出内核上限;1 = 版本过低;
# -1 = 这个候选根本不是能用的 Python(例如 Microsoft Store 的占位 exe)。
function Get-PyVerdict($candidate) {
    if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) {
        return -1
    }
    $pyArgs = @($candidate.PreArgs) + @($SetupPy, "--interpreter-check")
    $result = Invoke-PyCapture $candidate.Exe $pyArgs
    if ($result.Code -in @(0, 1, 2)) { return $result.Code }
    return -1
}

function Get-PyVerdictText($candidate) {
    $pyArgs = @($candidate.PreArgs) + @($SetupPy, "--interpreter-check")
    $result = Invoke-PyCapture $candidate.Exe $pyArgs
    if ($result.Out) { return $result.Out }
    return "(拿不到版本信息)"
}

function Find-Python311 {
    # 装完之后重新找:先走启动器,再兜 winget 的固定安装位置。
    # py 启动器靠自己扫描目录/注册表,不依赖 PATH,所以 py install 装完
    # 本会话就能用;winget 装完新 PATH 只在新会话生效,故留显式路径兜底
    # (与 setup.sh 里 /opt/homebrew/bin/python3.11 的写法对应)。
    foreach ($candidate in @(
            @{ Exe = "py"; PreArgs = @("-3.11") },
            @{ Exe = "python3.11"; PreArgs = @() })) {
        if ((Get-PyVerdict $candidate) -eq 0) { return $candidate }
    }
    $explicit = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
    if (Test-Path -LiteralPath $explicit) {
        $candidate = @{ Exe = $explicit; PreArgs = @() }
        if ((Get-PyVerdict $candidate) -eq 0) { return $candidate }
    }
    return $null
}

function Install-Python311 {
    # 优先 Python installation manager:装进它自己的目录并注册到启动器,
    # 本会话 py -3.11 立即可用,不需要重开终端。回退 winget。
    #
    # 成败一律以「重探得到 verdict 0」为准,不拿安装器的退出码当判据:
    # 实测 py install 3.11.9 已经装好、日志也打了 "will be launched by
    # python3.11[-64].exe",退出码却不保证是 0。退出码只写进警告供排查。
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $help = Invoke-PyCapture "py" @("help")
        if ($help.Code -eq 0 -and $help.Out -match 'py install') {
            Write-Host "安装:py install 3.11"
            $code = Invoke-PyVisible "py" @("install", "-y", "3.11")
            if (Find-Python311) { return $true }
            Write-Warning "py install 3.11(退出码 $code)后仍探不到 3.11,改试 winget。"
        }
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Warning "既没有可用的 py install 也没有 winget,无法自动安装 Python 3.11。"
        return $false
    }
    # --source winget 必须带:存在多个源时 winget 会因歧义什么都不做却返回 0。
    Write-Host "安装:winget install --id Python.Python.3.11 -e --scope user --source winget"
    $code = Invoke-PyVisible "winget" @(
        "install", "--id", "Python.Python.3.11", "-e",
        "--scope", "user", "--source", "winget")
    if (Find-Python311) { return $true }
    Write-Warning "winget(退出码 $code)后仍探不到 3.11,安装可能未成功。"
    return $false
}

$candidates = @(
    @{ Exe = "py";         PreArgs = @("-3.11") },
    @{ Exe = "py";         PreArgs = @("-3.12") },
    @{ Exe = "py";         PreArgs = @("-3.13") },
    @{ Exe = "py";         PreArgs = @("-3.10") },
    @{ Exe = "python3.11"; PreArgs = @() },
    @{ Exe = "python3.12"; PreArgs = @() },
    @{ Exe = "python3.13"; PreArgs = @() },
    @{ Exe = "python3.10"; PreArgs = @() },
    @{ Exe = "py";         PreArgs = @("-3") },
    @{ Exe = "python3";    PreArgs = @() },
    @{ Exe = "python";     PreArgs = @() }
)

$usable = $null    # verdict 0:可用且支持 Rust 内核
$degraded = $null  # verdict 2:可用但超出内核上限,仅作兜底
foreach ($candidate in $candidates) {
    $verdict = Get-PyVerdict $candidate
    if ($verdict -eq 0) {
        $usable = $candidate
        break
    }
    if ($verdict -eq 2 -and $null -eq $degraded) {
        $degraded = $candidate
    }
}

$chosen = $usable

if ($null -eq $chosen -and $null -ne $degraded) {
    $label = "$($degraded.Exe) $($degraded.PreArgs -join ' ')"
    Write-Host "当前解释器 $label :$(Get-PyVerdictText $degraded)" -ForegroundColor Yellow
    $installed = $false
    if ($NoAutoInstall) {
        Write-Warning "已设 MJ_SETUP_NO_AUTO_INSTALL,不自动安装 Python 3.11。"
    } else {
        Write-Host "自动安装 Python 3.11(Rust 内核需要更低的版本)…"
        $installed = Install-Python311
        if ($installed) { $chosen = Find-Python311 }
    }
    if ($null -eq $chosen) {
        if ($installed) {
            Write-Warning "3.11 已安装但本会话找不到它;请重开终端后重跑。"
        }
        Write-Warning "Rust 内核装不上,shanten/ukeire 将走纯 Python(可跑但稍慢)。"
        $chosen = $degraded
    }
}

if ($null -eq $chosen) {
    $hasPythonCommand = $false
    foreach ($candidate in $candidates) {
        if (Get-Command $candidate.Exe -ErrorAction SilentlyContinue) {
            $hasPythonCommand = $true
            break
        }
    }
    if ($hasPythonCommand) {
        Write-Warning "找到了 python/py 命令,但 $SetupPy --interpreter-check 无法正常返回;先检查该脚本是否被改坏。"
    }
    Write-Host "未找到满足版本的 Python(需要 >= 3.10)。" -ForegroundColor Red
    if (-not $NoAutoInstall) {
        if (Install-Python311) { $chosen = Find-Python311 }
    }
    if ($null -eq $chosen) {
        Write-Host "请关闭并重新打开终端(新装的 Python 需要新 PATH),然后重新执行本脚本。" -ForegroundColor Yellow
        Write-Host "手动安装任选其一:" -ForegroundColor Yellow
        Write-Host "  1) py install 3.11" -ForegroundColor Yellow
        Write-Host "  2) winget install --id Python.Python.3.11 -e --source winget" -ForegroundColor Yellow
        Write-Host "  3) https://www.python.org/downloads/ 下载安装(勾选 'Add python.exe to PATH')" -ForegroundColor Yellow
        exit 1
    }
}

& $chosen.Exe @($chosen.PreArgs + @($SetupPy) + $Args)
exit $LASTEXITCODE
