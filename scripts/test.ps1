$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
docker compose run --rm api pytest --cov=app --cov-report=term-missing
docker compose run --rm api ruff check app tests evaluation
docker compose run --rm api mypy app evaluation

