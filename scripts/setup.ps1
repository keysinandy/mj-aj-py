# setup 引导(Windows):保证存在 Python >= 3.10,再执行 scripts\setup.py。
# 用法:powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [--install] [--venv] ...
#
# 顺序:
#   1. 依次探测 py -3 / python3 / python,取第一个满足版本的;
#   2. 都没有 → 有 winget 就征求同意后 winget install Python 3.11
#      (装完新终端才有 PATH,提示重开终端再跑一次);
#   3. 用找到的解释器运行 scripts\setup.py(参数透传)。

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SetupPy = Join-Path $Root "scripts\setup.py"

function Test-PyVersion([string]$exe, [string[]]$preArgs) {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $false }
    & $exe @preArgs -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
    return ($LASTEXITCODE -eq 0)
}

$chosen = $null
foreach ($cand in @(
        , @("py", @("-3"))
        , @("python3", @())
        , @("python", @()))) {
    if (Test-PyVersion $cand[0] $cand[1]) {
        $chosen = $cand
        break
    }
}

if ($null -ne $chosen) {
    & $chosen[0] @($chosen[1] + @($SetupPy) + $Args)
    exit $LASTEXITCODE
}

Write-Host "未找到满足版本的 Python(需要 >= 3.10)。" -ForegroundColor Red

if (Get-Command winget -ErrorAction SilentlyContinue) {
    $ans = Read-Host "是否用 winget install Python.Python.3.11(可能需数分钟)?[y/N]"
    if ($ans -match '^[yY]') {
        winget install --id Python.Python.3.11 -e --scope user
        Write-Host "安装完成。请关闭并重新打开终端(新装的 Python 需要新 PATH)," -ForegroundColor Yellow
        Write-Host "然后重新执行本脚本。" -ForegroundColor Yellow
        exit 1
    }
} else {
    Write-Host "未检测到 winget。" -ForegroundColor Yellow
}

Write-Host "手动安装任选其一:" -ForegroundColor Yellow
Write-Host "  1) winget install --id Python.Python.3.11 -e" -ForegroundColor Yellow
Write-Host "  2) https://www.python.org/downloads/ 下载安装(勾选 'Add python.exe to PATH')" -ForegroundColor Yellow
exit 1
