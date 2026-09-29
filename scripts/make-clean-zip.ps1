<#
.SYNOPSIS
    Gera um ZIP limpo do projeto, sem dependencias, caches, vetores ou segredos.

.DESCRIPTION
    O script NUNCA apaga nada do diretorio de trabalho. Ele apenas COPIA os
    arquivos permitidos para uma pasta temporaria e compacta essa copia.

    Sempre excluidos:
      .venv, node_modules, .angular, dist, .pytest_cache, __pycache__,
      data/ (inclui data/chroma e data/uploads), .git, *.zip e qualquer
      variante de .env exceto .env.example

    Modelos do Ollama nunca entram: eles vivem no volume Docker `ollama_data`,
    fora do diretorio do projeto.

.PARAMETER OutputPath
    Caminho do ZIP a ser gerado. Padrao: ..\RAG-Saude-Local-clean-<data>.zip

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\make-clean-zip.ps1
#>
[CmdletBinding()]
param(
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$projectName = Split-Path -Leaf $projectRoot

if (-not $OutputPath) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $OutputPath = Join-Path (Split-Path -Parent $projectRoot) "$projectName-clean-$stamp.zip"
}

# Diretorios que nunca sao copiados (comparados por segmento de caminho).
$excludedDirs = @(
    '.venv', 'venv', 'env',
    'node_modules', '.angular', 'dist', '.nx',
    '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache',
    '.git', '.idea', '.vscode',
    'data', 'htmlcov'
)

# Arquivos que nunca sao copiados.
$excludedFiles = @('.coverage')
$excludedExtensions = @('.zip', '.pyc', '.pyo', '.gguf', '.bin', '.safetensors', '.sqlite3')

function Test-Excluded {
    param([string]$RelativePath)

    $segments = $RelativePath -split '[\\/]'
    $fileName = $segments[-1]

    foreach ($segment in $segments[0..($segments.Length - 2)]) {
        if ($excludedDirs -contains $segment) { return $true }
    }
    if ($excludedFiles -contains $fileName) { return $true }
    if ($excludedExtensions -contains ([System.IO.Path]::GetExtension($fileName).ToLower())) { return $true }

    # Qualquer variante de .env fica de fora (.env, .env.local, .env.bak-...),
    # com a unica excecao do modelo versionado.
    if ($fileName -like '.env*' -and $fileName -ne '.env.example') { return $true }

    return $false
}

$staging = Join-Path ([System.IO.Path]::GetTempPath()) "$projectName-clean-$([guid]::NewGuid().ToString('N').Substring(0,8))"
$stagingProject = Join-Path $staging $projectName
New-Item -ItemType Directory -Path $stagingProject -Force | Out-Null

Write-Host "Origem : $projectRoot"
Write-Host "Staging: $stagingProject"
Write-Host ''

$copied = 0
$skipped = 0

Get-ChildItem -Path $projectRoot -Recurse -File -Force | ForEach-Object {
    $relative = $_.FullName.Substring($projectRoot.Length).TrimStart('\', '/')

    if (Test-Excluded -RelativePath $relative) {
        $script:skipped++
        return
    }

    $destination = Join-Path $stagingProject $relative
    $destinationDir = Split-Path -Parent $destination
    if (-not (Test-Path $destinationDir)) {
        New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
    $script:copied++
}

if (Test-Path $OutputPath) {
    Remove-Item -LiteralPath $OutputPath -Force
}

Compress-Archive -Path $stagingProject -DestinationPath $OutputPath -CompressionLevel Optimal
Remove-Item -LiteralPath $staging -Recurse -Force

$sizeMb = [math]::Round((Get-Item $OutputPath).Length / 1MB, 2)

Write-Host "Arquivos copiados : $copied"
Write-Host "Arquivos ignorados: $skipped"
Write-Host ''
Write-Host "ZIP gerado: $OutputPath ($sizeMb MB)" -ForegroundColor Green
Write-Host 'Nenhum arquivo do projeto original foi alterado ou removido.'
