# Bootstrap an isolated WCOW builder; leave the runner's Docker service intact.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$taskRoot = Join-Path $env:RUNNER_TEMP 'docker-v2-buildkit'
$null = New-Item -ItemType Directory -Force -Path $taskRoot

function Install-Archive {
    param([string] $Uri, [string] $Destination)
    $archivePath = Join-Path $taskRoot ([IO.Path]::GetFileName($Uri))
    Invoke-WebRequest -Uri $Uri -OutFile $archivePath
    $null = New-Item -ItemType Directory -Force -Path $Destination
    & tar.exe -xf $archivePath -C $Destination
    if ($LASTEXITCODE -ne 0) { throw "Cannot extract $Uri" }
}

Install-Archive 'https://github.com/containerd/containerd/releases/download/v2.3.6/containerd-2.3.6-windows-amd64.tar.gz' $taskRoot
Install-Archive 'https://github.com/moby/buildkit/releases/download/v0.34.0/buildkit-v0.34.0.windows-amd64.tar.gz' $taskRoot
$binaryPath = Join-Path $taskRoot 'bin'
$env:PATH = "$binaryPath;$env:PATH"
$binaryPath | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append

if (-not (Get-Command Get-HnsNetwork -ErrorAction SilentlyContinue)) {
    $hnsModule = Join-Path $taskRoot 'hns.psm1'
    Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/microsoft/SDN/32df9732ef6dfab07dba205440e30bdfbd8e579a/Kubernetes/windows/hns.psm1' -OutFile $hnsModule
    Import-Module $hnsModule
}
$network = @(Get-HnsNetwork | Where-Object { $_.Name -eq 'nat' })
if ($network.Count -ne 1) { throw 'A single existing Docker NAT network is required for WCOW BuildKit' }
$cniDirectory = Join-Path $taskRoot 'cni'
Install-Archive 'https://github.com/microsoft/windows-container-networking/releases/download/v0.3.1/windows-container-networking-cni-amd64-v0.3.1.zip' $cniDirectory
$cniConfig = Join-Path $taskRoot 'nat.conf'
@{
    cniVersion = '1.0.0'
    name = 'nat'
    type = 'nat'
    master = 'Ethernet'
    ipam = @{
        subnet = $network[0].Subnets[0].AddressPrefix
        routes = @(@{ gateway = $network[0].Subnets[0].GatewayAddress })
    }
    capabilities = @{ portMappings = $true; dns = $true }
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $cniConfig -Encoding utf8

$containerdConfig = Join-Path $taskRoot 'containerd.toml'
$containerdRoot = (Join-Path $taskRoot 'containerd-root').Replace('\', '\\')
$containerdState = (Join-Path $taskRoot 'containerd-state').Replace('\', '\\')
@"
version = 2
root = "$containerdRoot"
state = "$containerdState"
disabled_plugins = ["io.containerd.grpc.v1.cri"]
[grpc]
  address = "\\\\.\\pipe\\docker-v2-containerd"
"@ | Set-Content -LiteralPath $containerdConfig -Encoding utf8
# Services survive GitHub's per-step process cleanup; child processes do not.
& (Join-Path $binaryPath 'containerd.exe') --register-service --service-name docker-v2-containerd --config $containerdConfig --log-file (Join-Path $taskRoot 'containerd.log')
if ($LASTEXITCODE -ne 0) { throw 'Cannot register isolated containerd service' }
Start-Service docker-v2-containerd

$buildkitConfig = Join-Path $taskRoot 'buildkitd.toml'
$buildkitRoot = (Join-Path $taskRoot 'buildkit-root').Replace('\', '\\')
$tomlCniConfig = $cniConfig.Replace('\', '\\')
$tomlCniDirectory = $cniDirectory.Replace('\', '\\')
@"
root = "$buildkitRoot"
[grpc]
  address = ["npipe:////./pipe/docker-v2-buildkitd"]
[worker.containerd]
  enabled = true
  address = "npipe:////./pipe/docker-v2-containerd"
  namespace = "docker-v2-buildkit"
  snapshotter = "windows"
  hypervIsolation = true
  networkMode = "cni"
  cniConfigPath = "$tomlCniConfig"
  cniBinaryPath = "$tomlCniDirectory"
  max-parallelism = 2
"@ | Set-Content -LiteralPath $buildkitConfig -Encoding utf8

for ($attempt = 1; $attempt -le 30; $attempt++) {
    & (Join-Path $binaryPath 'ctr.exe') --address '\\.\pipe\docker-v2-containerd' version 2>$null
    if ($LASTEXITCODE -eq 0) { break }
    if ((Get-Service docker-v2-containerd).Status -ne 'Running' -or $attempt -eq 30) {
        Get-Content (Join-Path $taskRoot 'containerd.log')
        throw 'Isolated containerd failed to start'
    }
    Start-Sleep -Seconds 2
}
& (Join-Path $binaryPath 'buildkitd.exe') --register-service --service-name docker-v2-buildkitd --config $buildkitConfig --log-file (Join-Path $taskRoot 'buildkit.log')
if ($LASTEXITCODE -ne 0) { throw 'Cannot register WCOW BuildKit service' }
Start-Service docker-v2-buildkitd
for ($attempt = 1; $attempt -le 30; $attempt++) {
    & (Join-Path $binaryPath 'buildctl.exe') --addr 'npipe:////./pipe/docker-v2-buildkitd' debug workers 2>$null
    if ($LASTEXITCODE -eq 0) { break }
    if ((Get-Service docker-v2-buildkitd).Status -ne 'Running' -or $attempt -eq 30) {
        Get-Content (Join-Path $taskRoot 'buildkit.log')
        throw 'WCOW BuildKit failed to start'
    }
    Start-Sleep -Seconds 2
}
