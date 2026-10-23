# 启动开发环境：后端 8100 + 前端 5173，供浏览器体验
$env:NODE_ENV = 'development'
Start-Process -FilePath "d:\python_code\AITest\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","app.main:app","--port","8100","--reload","--reload-dir","app" -WorkingDirectory "d:\python_code\AITest" -WindowStyle Hidden
Start-Process -FilePath "D:\node\node.exe" -ArgumentList "d:\python_code\AITest\frontend\node_modules\vite\bin\vite.js" -WorkingDirectory "d:\python_code\AITest\frontend" -WindowStyle Hidden
Write-Output "后端: http://127.0.0.1:8100/docs"
Write-Output "前端: http://127.0.0.1:5173/"
