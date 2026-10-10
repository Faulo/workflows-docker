const { execFileSync } = require('node:child_process');
execFileSync('pwsh', ['-NoProfile', '-Command', `
$ErrorActionPreference = 'Stop'
foreach ($serviceName in 'docker-v2-buildkitd', 'docker-v2-containerd') {
  if (Get-Service $serviceName -ErrorAction SilentlyContinue) {
    Stop-Service $serviceName
    sc.exe delete $serviceName
    if ($LASTEXITCODE -ne 0) { throw "Cannot remove $serviceName" }
  }
}
`], { stdio: 'inherit' });
