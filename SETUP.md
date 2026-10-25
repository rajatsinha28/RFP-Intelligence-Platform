# Advance RAG setup guide

This guide explains how to run, test, and operate the local Advance RAG project on Windows. The supported runtime is Docker Compose. The application is available at http://localhost:8000.

## 1. What you are running

Compose starts these services:

| Service | Purpose | Local exposure |
| --- | --- | --- |
| api | FastAPI API and compiled React UI | http://localhost:8000 |
| worker | Celery ingestion and evaluation jobs | internal only |
| postgres | Documents, versions, jobs, logs, feedback, and evaluations | internal only |
| redis | Exact, semantic, embedding, rate-limit, and Celery state | internal only |
| migrate | One-shot Alembic migration job | exits with code 0 when complete |

OpenAI is the model and embedding provider. Pinecone is the managed vector store. Both require credentials for ingestion, retrieval, generation, and live evaluations.

## 2. Prerequisites

Install or enable the following on Windows:

1. Docker Desktop with Linux containers and Docker Compose v2.
2. An OpenAI API key with access to the configured chat and embedding models.
3. A Pinecone API key. The default configuration creates a serverless index named advance-rag.
4. Git, if you are checking the project out or updating it.
5. Optional for host-side development: Python 3.11+ and Node.js/npm.

Verify Docker from PowerShell:

~~~powershell
docker version
docker compose version
~~~

If Docker cannot connect to its engine, open Docker Desktop and wait until it reports that Docker is running. Docker is the only required runtime; host Python and Node are optional when using Compose.

## 3. Open the project

Open PowerShell and select the project directory:

~~~powershell
Set-Location -LiteralPath "<path-to-the-cloned-repository>"
~~~

Confirm the expected files are present:

~~~powershell
Get-ChildItem
~~~

Important files include docker-compose.yml, Dockerfile, .env.example, README.md, app, frontend, alembic, tests, and evaluation.

## 4. Create the private environment file

The .env file is intentionally ignored by Git. Create it once from the template:

~~~powershell
Copy-Item -LiteralPath .env.example -Destination .env
notepad .env
~~~

Set at least these values for a fully functional RAG system:

~~~dotenv
OPENAI_API_KEY=your_openai_api_key
PINECONE_API_KEY=your_pinecone_api_key
~~~

Keep these container service URLs unchanged when running through Compose:

~~~dotenv
POSTGRES_URL=postgresql+asyncpg://advance_rag:advance_rag@postgres:5432/advance_rag
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2
STORAGE_PATH=/data/documents
~~~

Recommended security settings for a shared machine are:

~~~dotenv
APP_API_KEY=choose_a_long_random_api_key
ALLOWED_ORIGINS=http://localhost:8000
~~~

When APP_API_KEY is set, send it as an X-API-Key header on protected API calls. Never commit .env, paste keys into source files, or include keys in screenshots or logs.

### Model and Pinecone settings

The template defaults are examples. Change them to models available to your account:

~~~dotenv
FAST_MODEL=gpt-6-luna
ADVANCED_MODEL=gpt-6.1-sol
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIMENSIONS=3072
PINECONE_INDEX=advance-rag
PINECONE_NAMESPACE=kb-default
PINECONE_CLOUD=aws
PINECONE_REGION=us-east-1
PINECONE_AUTO_CREATE=true
~~~

If you change the embedding model or dimensions, use a new compatible Pinecone index name. The readiness check rejects a dimension mismatch to prevent invalid vectors from being written.

## 5. Start the complete stack

Build the API/worker image and start all services in the background:

~~~powershell
docker compose up --build -d
~~~

The first build can take several minutes because it installs CPU PyTorch, OCR tools, Poppler, local reranker dependencies, and evaluation libraries. Later source-only rebuilds reuse the dependency layer.

Check the service state:

~~~powershell
docker compose ps -a
~~~

Expected state:

- postgres: Up (healthy)
- redis: Up (healthy)
- api: Up (healthy)
- worker: Up
- migrate: Exited (0)

Follow application logs when needed:

~~~powershell
docker compose logs -f api worker
~~~

Stop following logs with Ctrl+C; this does not stop the services.

## 6. Verify health and readiness

Liveness does not call external providers and should return HTTP 200:

~~~powershell
curl.exe -i http://localhost:8000/health
~~~

Readiness validates PostgreSQL, migrations, Redis, Pinecone, OpenAI embeddings, and configured models:

~~~powershell
curl.exe -i http://localhost:8000/ready
~~~

With valid OpenAI and Pinecone keys, readiness should return HTTP 200 and status ready. With blank keys, HTTP 503 and missing_key for the external services is expected; the API and UI can still be used for infrastructure-only checks.

Useful endpoints:

~~~text
http://localhost:8000/             React application
http://localhost:8000/docs        Swagger/OpenAPI UI
http://localhost:8000/redoc       ReDoc API documentation
http://localhost:8000/metrics     Prometheus metrics
~~~

## 7. Provision and validate Pinecone

After adding both credentials to .env, restart the stack so the API and worker receive them:

~~~powershell
docker compose up -d
~~~

Explicitly create or validate the configured Pinecone index:

~~~powershell
docker compose run --rm api python -m scripts.bootstrap
~~~

The command checks Pinecone, OpenAI embeddings, and Redis. It exits non-zero if a key is invalid, a provider is unavailable, or the Pinecone dimension does not match EMBEDDING_DIMENSIONS.

## 8. Use the web interface

Open http://localhost:8000. The interface includes:

- Documents: list current documents, versions, statuses, and deletion actions.
- Upload: upload PDF, scanned PDF, TXT/Markdown, DOCX, CSV, JSON, HTML, PNG, JPEG, TIFF, or WebP files.
- URL ingestion: fetch a public HTTP/HTTPS page after SSRF validation.
- Chat: ask grounded questions and inspect citations.
- Evaluations: start and inspect an evaluation report.
- Health and performance: view readiness, latency, token, cache, and request metrics.

If `APP_API_KEY` is set, copy only that application key from `.env` into **Local API key** in the left sidebar and select **Connect**. It is kept in browser session storage, sent only to this application's protected endpoints, and cleared when the browser session ends. Never enter the OpenAI or Pinecone keys in the browser.

Ingestion is asynchronous. After uploading, use the job ID shown by the UI or query the job endpoint until it reaches completed or failed.

## 9. Use the REST API

If you set `APP_API_KEY`, run this once in the same PowerShell session before the protected examples below:

~~~powershell
$appKey = ((Get-Content .env | Where-Object { $_ -match '^APP_API_KEY=' }) -replace '^APP_API_KEY=', '').Trim()
$headers = @{ 'X-API-Key' = $appKey }
~~~

### Upload a document

The example uses the included refund policy:

~~~powershell
curl.exe -X POST "http://localhost:8000/api/v1/documents/upload?knowledge_base_id=default" `
  -H "X-API-Key: $appKey" `
  -F "file=@examples/documents/refund_policy.txt"
~~~

The response contains document_id and job_id. Check the job:

~~~powershell
curl.exe -H "X-API-Key: $appKey" http://localhost:8000/api/v1/documents/jobs/YOUR_JOB_ID
curl.exe -H "X-API-Key: $appKey" http://localhost:8000/api/v1/documents
~~~

### Ingest a public URL

Only public HTTP/HTTPS URLs are accepted. Localhost, private networks, link-local addresses, unsafe redirects, credential-bearing URLs, and unsupported content are rejected.

~~~powershell
$body = @{ url = "https://example.com"; title = "Example page"; knowledge_base_id = "default" } | ConvertTo-Json
$headers = @{ 'X-API-Key' = $appKey }
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/documents/ingest `
  -Headers $headers `
  -ContentType application/json `
  -Body $body
~~~

### Ask a grounded question

~~~powershell
$body = @{ query = "What is the refund period?"; knowledge_base_id = "default" } | ConvertTo-Json
$headers = @{ 'X-API-Key' = $appKey }
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/query `
  -Headers $headers `
  -ContentType application/json `
  -Body $body
~~~

On Windows PowerShell, build the request body with `ConvertTo-Json` as shown. Do not paste a PowerShell hashtable such as `@{ query = ... }` directly into `curl.exe`; that produces the `JSON decode error` response because it is not JSON. The API key header is required whenever `APP_API_KEY` is non-empty.

The response includes the answer, status, confidence, citations, route, model, cache state, retrieval counts, latency, token usage, estimated cost, and request ID. If evidence is insufficient, the service returns a deterministic no-answer response without fabricating a claim.

### Stream a validated answer

~~~powershell
$body = '{"query":"What is the refund period?","knowledge_base_id":"default"}'
curl.exe -N -X POST http://localhost:8000/api/v1/query/stream `
  -H "Content-Type: application/json" `
  -H "X-API-Key: $appKey" `
  --data $body
~~~

SSE events are status, answer_delta, citation, done, and error. Answer deltas are emitted only after structured output and grounding validation succeed.

### Delete a document

~~~powershell
curl.exe -i -X DELETE http://localhost:8000/api/v1/documents/YOUR_DOCUMENT_ID -H "X-API-Key: $appKey"
~~~

Deletion removes the document from PostgreSQL lifecycle views, deletes its Pinecone vectors, invalidates the knowledge-base cache version, and removes its persisted source files.

## 10. Run evaluations

The included dataset is evaluation/datasets/example.json. Ingest examples/documents/refund_policy.txt first, wait for its job to complete, and then run:

~~~powershell
docker compose run --rm api python -m evaluation.run `
  --dataset evaluation/datasets/example.json `
  --knowledge-base default `
  --output reports
~~~

The command writes JSON and HTML reports to the persistent reports volume. Native reports include hit rate, MRR, NDCG, token-F1, fallback rate, cache rate, latency, and token totals. RAGAS and DeepEval run when their judge-model credentials and provider access are available; otherwise they are recorded as unavailable without preventing native metrics.

For the RFP retrieval audit, use the manually verified dataset covering both bid packages:

~~~powershell
docker compose run --rm api python -m evaluation.retrieval_benchmark `
  --dataset evaluation/datasets/rfp_retrieval.json `
  --output outputs/reports/rfp-retrieval-comparison.json
~~~

The benchmark compares dense-only, hybrid, and hybrid-plus-reranking search. Acceptance requires Recall@5 of at least 0.85, MRR of at least 0.75, reported nDCG, and no regression of both primary metrics after reranking.

After Bid1 and Bid2 extraction completes, run the field and API contract audits:

~~~powershell
docker compose run --rm api python -m evaluation.extraction_benchmark
docker compose run --rm api python -m scripts.rfp_sample_qa `
  --limit 10 `
  --output outputs/reports/rfp-sample-qa.json
docker compose run --rm api python -m scripts.rfp_sample_qa `
  --negative-only --limit 1 --max-attempts 1 `
  --output outputs/reports/rfp-negative-control.json
~~~

The positive audit requires answered responses, citations, and identical synchronous/SSE results. The negative control requires a deterministic no-answer with no citations. The worker uses Celery's `solo` pool so native local reranking libraries are not loaded into forked child processes.

Import the assignment folder from the host. The CLI uploads every supported file from each immediate subfolder and ignores the assignment PDF at the root:

~~~powershell
.\.venv\Scripts\python.exe -m app.cli ingest-folder `
  --path "C:\Users\sinha\Downloads\Statements (AI Engineer-Emplay Inc)\Assignment-Data-Statements (AI Engineer-Emplay Inc)"
~~~

Wait for all nine documents to become `active`, then open **RFP bids** to run each 20-field extraction and inspect citations. Output is stored under `outputs\bids`. Use Grounded chat with a bid scope for bid-specific Q&A.

The evaluation API can also start the same work asynchronously:

~~~powershell
$body = @{ dataset_path = "evaluation/datasets/example.json"; knowledge_base_id = "default" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/evaluation/run `
  -ContentType application/json `
  -Body $body
~~~

## 11. Run backend quality checks on the host

Host testing is optional and useful for fast iteration without rebuilding Docker.

Create a virtual environment from the project directory:

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,evaluation]"
~~~

If PowerShell blocks activation, run the interpreter directly as .\.venv\Scripts\python.exe without activating it.

Run unit and security tests:

~~~powershell
python -m pytest -m "not live and not integration" --cov=app --cov=evaluation --cov-report=term-missing
~~~

Run the local-stack integration check after Compose is running:

~~~powershell
$env:RUN_STACK_TESTS = "1"
python -m pytest tests\integration\test_stack.py -q
Remove-Item Env:RUN_STACK_TESTS
~~~

Run static checks:

~~~powershell
python -m ruff format --check app tests evaluation
python -m ruff check app tests evaluation
python -m mypy app evaluation
~~~

Credential-gated tests are opt-in:

~~~powershell
python -m pytest -m live -v
~~~

## 12. Run frontend checks

From the project directory:

~~~powershell
Set-Location frontend
npm ci
npm run lint
npm test
npm run build
~~~

Run Playwright against the Docker-served application:

~~~powershell
npx playwright install chromium
$env:PLAYWRIGHT_BASE_URL = "http://localhost:8000"
npm run test:e2e
Remove-Item Env:PLAYWRIGHT_BASE_URL
~~~

For frontend development with hot reload, start the API/worker stack first, then run npm run dev. Open http://localhost:5173. Vite proxies /api, /health, and /ready to http://localhost:8000.

## 13. Optional Make targets

If make is installed, the project provides:

~~~text
make dev        Build and start Compose
make down       Stop and remove Compose containers, preserving named volumes
make logs       Follow API and worker logs
make test       Run backend tests with coverage
make test-live  Run credential-gated tests
make lint       Run Ruff checks
make typecheck  Run MyPy
make eval       Run the example evaluation
~~~

On Windows, the PowerShell and Docker commands in this file are the supported equivalents.

## 14. Stop, restart, and preserve data

Stop containers while preserving PostgreSQL, Redis, uploaded files, reports, and the model cache:

~~~powershell
docker compose stop
docker compose start
~~~

Remove containers and networks while preserving named volumes:

~~~powershell
docker compose down
docker compose up -d
~~~

Redis is configured with append-only persistence. Do not delete named volumes if you need local cache and metadata to survive a restart.

## 15. Reset local service data

The following is destructive for local data. It removes PostgreSQL records, Redis state, uploaded files, reports, and the downloaded model cache. It does not delete the managed Pinecone index:

~~~powershell
docker compose down --volumes
docker compose up --build -d
~~~

To remove Pinecone vectors, use the document deletion API or the Pinecone console deliberately; local volume reset does not remove managed vectors.

## 16. Troubleshooting

### Docker command cannot connect

Start Docker Desktop, wait for the engine to become ready, and rerun docker version. If another application owns port 8000, change the left side of the api port mapping in docker-compose.yml and open the new port.

### Readiness returns missing_key

Add non-empty OPENAI_API_KEY and PINECONE_API_KEY to .env, then recreate services:

~~~powershell
docker compose up -d --force-recreate api worker
~~~

### Readiness reports an unavailable provider

Check the key, model names, account permissions, provider status, and outbound internet access. Inspect logs:

~~~powershell
docker compose logs --tail 200 api worker
~~~

### Pinecone dimension mismatch

The configured EMBEDDING_DIMENSIONS must match the Pinecone index dimension. Create a new index name when changing dimensions, update .env, and run docker compose run --rm api python -m scripts.bootstrap.

### Jobs remain queued or fail

Inspect the worker and Redis logs:

~~~powershell
docker compose logs --tail 200 worker redis
~~~

Confirm that the worker uses the same .env, Redis is healthy, and the source file type is supported. A failed job preserves its error in the job endpoint and marks the document as failed.

### OCR or PDF parsing fails

The production image includes Tesseract and Poppler. Verify them inside the API image:

~~~powershell
docker compose exec api tesseract --version
docker compose exec api pdftotext -v
~~~

For non-English OCR, install the required language package in the Dockerfile and update OCR_LANGUAGES.

### The UI shows no answer

Wait for ingestion to reach completed, verify the document appears in Documents, and ask a question explicitly supported by the document. Low-confidence or unsupported questions intentionally return a deterministic insufficient-evidence response.

### Host frontend commands cannot find npm

Install Node.js, reopen PowerShell, and verify node --version and npm --version. Docker Compose does not require host Node.js because the Dockerfile builds the frontend in its Node stage.

## 17. Security checklist before sharing the application

- Set a long random APP_API_KEY.
- Restrict ALLOWED_ORIGINS to trusted origins.
- Keep .env outside version control.
- Use HTTPS and a reverse proxy for any non-local access.
- Use a secret manager instead of plain environment files in hosted environments.
- Enable Pinecone deletion protection after provisioning if accidental deletion is a concern.
- Replace local Docker file storage with an object-store adapter for multi-host deployments.
- Review rate limits, upload limits, OCR languages, model names, and cost rates before accepting real traffic.
