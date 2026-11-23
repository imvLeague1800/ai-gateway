param(
    [switch]$CalibrationOnly,
    [string]$OutputRoot = '.verification/dual-qwen/retest',
    [string]$BudgetFile = 'benchmarks/router/budgets.json'
)
$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
$router = 'dgx@10.169.25.16'
$remote = '/home/dgx/spark-vllm/reports/dual-qwen/run-router.sh'
function Invoke-Replay($profile, $group, $policy, $namespace) {
    $existing = Get-ChildItem $OutputRoot -Directory -Filter "$profile-$group-$policy-*" -ErrorAction SilentlyContinue |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'summary.json') }
    if ($existing) { return }
    ssh -o BatchMode=yes $router "$remote $policy --health-check-timeout-secs 3"
    if ($LASTEXITCODE) { throw 'Router restart failed' }
    $ready = $false
    for ($i = 0; $i -lt 90; $i++) {
        try {
            $response = Invoke-WebRequest -Uri 'http://127.0.0.1:18100/v1/models' -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200 -and $response.Content -match 'qwen3.6-35b-a3b-nvfp4') {
                $ready = $true
                break
            }
        } catch {
            if ($i -eq 89) { throw }
        }
        Start-Sleep -Seconds 1
    }
    if (!$ready) { throw 'Router readiness check failed' }
    $arguments = @($profile, '--group', $group, '--policy', $policy, '--namespace', $namespace, '--output', $OutputRoot)
    if ($profile -ne 'calibration') { $arguments += @('--budgets', $BudgetFile) }
    uv run --with httpx==0.28.1 python benchmarks/router/replay.py @arguments
    $exit = $LASTEXITCODE
    $run = Get-ChildItem $OutputRoot -Directory -Filter "$profile-$group-$policy-*" |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($run) { ssh -o BatchMode=yes $router 'docker logs vllm-router-test 2>&1' > (Join-Path $run.FullName 'router.log') }
    if ($exit) { throw "Replay failed for $profile / $group / $policy" }
}

if ($CalibrationOnly) {
    Invoke-Replay calibration 0 power_of_two 'Amber Birch'
    exit
}
$nonce = Get-Date -Format 'MMddHHmmss'
foreach ($profile in @('high', 'light', 'stable')) {
    foreach ($group in 1..3) {
        $policies = @('power_of_two', 'cache_aware')
        if ($group % 2 -eq 0) { [array]::Reverse($policies) }
        Invoke-Replay $profile $group $policies[0] "Blue $nonce ${profile} ${group}"
        Invoke-Replay $profile $group $policies[1] "Gold $nonce ${profile} ${group}"
    }
}
