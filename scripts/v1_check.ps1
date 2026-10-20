# V1 联调验收脚本 v2：不依赖旧日志文件，记录每步状态
$ErrorActionPreference = 'Stop'
$out = 'd:\python_code\AITest\outputs\v1-check.txt'
$lines = @()
try {
  $env:NODE_ENV = 'development'
  $be = Start-Process -FilePath "d:\python_code\AITest\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","app.main:app","--port","8100" -WorkingDirectory "d:\python_code\AITest" -PassThru -WindowStyle Hidden
  $lines += "BE_PID=" + $be.Id
  $fe = Start-Process -FilePath "D:\node\node.exe" -ArgumentList "D:\python_code\AITest\frontend\node_modules\vite\bin\vite.js" -WorkingDirectory "d:\python_code\AITest\frontend" -PassThru -WindowStyle Hidden -RedirectStandardOutput "d:\python_code\AITest\outputs\vite-dev2.log" -RedirectStandardError "d:\python_code\AITest\outputs\vite-dev2.err.log"
  $lines += "FE_PID=" + $fe.Id
  Start-Sleep -Seconds 12
  $feHome = Invoke-WebRequest http://127.0.0.1:5173/ -UseBasicParsing
  $lines += "FE_HOME_STATUS=" + $feHome.StatusCode
  $h = Invoke-RestMethod http://127.0.0.1:5173/api/health
  $lines += "PROXY_HEALTH=" + ($h | ConvertTo-Json -Compress)
  $lines += "RESULT=PASS"
} catch {
  $lines += "FAIL=" + $_.Exception.Message
  $lines += "RESULT=FAIL"
} finally {
  if ($be) { Stop-Process -Id $be.Id -Force -ErrorAction SilentlyContinue }
  if ($fe) { Stop-Process -Id $fe.Id -Force -ErrorAction SilentlyContinue }
}
$lines | Set-Content -Path $out -Encoding UTF8
