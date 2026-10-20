# V3 端到端验证：重启服务 → 建 sample 项目 → 触发分析 → 读回结果
$ErrorActionPreference = 'Continue'
$out = 'd:\python_code\AITest\outputs\v3-check.txt'
$lines = @()

# 1) 重启前后端
Get-Process node,python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$env:NODE_ENV = 'development'
Start-Process -FilePath "d:\python_code\AITest\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","app.main:app","--port","8100" -WorkingDirectory "d:\python_code\AITest" -WindowStyle Hidden
Start-Process -FilePath "D:\node\node.exe" -ArgumentList "d:\python_code\AITest\frontend\node_modules\vite\bin\vite.js" -WorkingDirectory "d:\python_code\AITest\frontend" -WindowStyle Hidden
Start-Sleep -Seconds 8

$sample = 'D:/python_code/AITest/scripts/sample_src'

try {
  # 2) 建项目（若已存在同名则复用）
  $projs = Invoke-RestMethod "http://127.0.0.1:5173/api/projects?keyword=V3演示订单系统"
  $proj = $projs.data | Where-Object { $_.name -eq 'V3演示订单系统' } | Select-Object -First 1
  if (-not $proj) {
    $body = @{ name = '订单系统示例'; sourceType = 'local_path'; sourcePath = $sample; targetUrl = 'http://127.0.0.1:8200' } | ConvertTo-Json
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    $proj = (Invoke-RestMethod "http://127.0.0.1:5173/api/projects" -Method Post -Body $bodyBytes -ContentType 'application/json; charset=utf-8').data
  }
  $projId = $proj.id
  $lines += "PROJECT_ID=" + $projId

  # 3) 触发分析
  $tr = Invoke-RestMethod "http://127.0.0.1:5173/api/projects/$projId/analyze" -Method Post
  $lines += "ANALYZE_TRIGGERED=" + $tr.code
  Start-Sleep -Seconds 3

  # 4) 读回结果
  $res = Invoke-RestMethod "http://127.0.0.1:5173/api/projects/$projId/analysis"
  $d = $res.data
  $lines += "STATUS=" + $d.status
  $lines += "ENDPOINTS=" + ($d.backendResult.endpoints | Measure-Object).Count
  $lines += "ENTITIES=" + ($d.backendResult.entities | Measure-Object).Count
  $lines += "RULES=" + ($d.backendResult.businessRules | Measure-Object).Count
  $lines += "STATEMACHINES=" + ($d.stateMachines | Measure-Object).Count
  if ($d.stateMachines.Count -gt 0) {
    $lines += "TRANSITIONS=" + ($d.stateMachines[0].transitions | Measure-Object).Count
  }
  $lines += "PAGES=" + ($d.frontendResult.pages | Measure-Object).Count
  $lines += "RESULT=" + $(if ($d.status -eq 'success') { 'PASS' } else { 'FAIL' })
} catch {
  $lines += "FAIL=" + $_.Exception.Message
  $lines += "RESULT=FAIL"
}
$lines | Set-Content -Path $out -Encoding UTF8
