$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created .env from .env.example. Add OPENAI_API_KEY and PINECONE_API_KEY, then run this script again.'
    exit 1
}
$Content = Get-Content -Raw -LiteralPath '.env'
if ($Content -notmatch '(?m)^OPENAI_API_KEY=.+$' -or $Content -notmatch '(?m)^PINECONE_API_KEY=.+$') {
    Write-Host 'Add OPENAI_API_KEY and PINECONE_API_KEY to .env before starting.'
    exit 1
}
docker compose up --build

