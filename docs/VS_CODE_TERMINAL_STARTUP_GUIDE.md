# Start Advance RAG from the VS Code terminal

This is the shortest reliable Windows workflow for starting and checking the complete local application. The project runs through Docker Compose; Python and Node do not need to be started separately.

## 1. Start Docker Desktop

Open **Docker Desktop** from the Windows Start menu. Wait until it reports that the Docker engine is running.

In VS Code, select **File → Open Folder** and open:

```text
C:\Users\sinha\OneDrive\Documents\ChatGPT\Rajat\Advance RAG
```

Open the integrated terminal with **Terminal → New Terminal**. Confirm that the terminal is PowerShell and is positioned in the project directory:

```powershell
Get-Location
docker version
docker compose version
```

If `docker version` reports a missing `dockerDesktopLinuxEngine` pipe, Docker Desktop is not ready yet.

## 2. Check the private configuration

The project needs a local `.env` file. Create it only if it does not already exist:

```powershell
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}
notepad .env
```

Confirm that `.env` contains valid values for:

```dotenv
APP_API_KEY=your_private_application_key
OPENAI_API_KEY=your_openai_key
PINECONE_API_KEY=your_pinecone_key
```

Keep the provided PostgreSQL, Redis, storage, embedding-dimension, and Pinecone index settings unless you intentionally reconfigure the system. Never commit `.env` or display its secrets while recording a demo.

## 3. Start the application

For the first run, after dependency changes, or after pulling new source code, build and start everything:

```powershell
docker compose up -d --build
```

The first build is slow because the image installs PyTorch, OCR, PDF, parsing, and evaluation dependencies. For normal later starts, use:

```powershell
docker compose up -d
```

Do not repeatedly restart the stack while a query is running.

## 4. Verify every service

Check container state:

```powershell
docker compose ps -a
```

Expected state:

- `api`: Up and healthy
- `worker`: Up
- `postgres`: Up and healthy
- `redis`: Up and healthy
- `migrate`: Exited with code 0

Check lightweight API liveness:

```powershell
curl.exe -sS http://localhost:8000/health
```

Check the database, migrations, Redis, Pinecone, embeddings, and models:

```powershell
curl.exe -sS --max-time 60 http://localhost:8000/ready
```

Do not open the UI until `/ready` returns `"status":"ready"`. Immediately after a cold start, external readiness checks and client initialization can take several seconds.

## 5. Open and connect the UI

Open this address in Edge:

```text
http://localhost:8000
```

Copy only the `APP_API_KEY` value from `.env`, paste it into **Local API key**, and select **Connect**. Never paste the OpenAI or Pinecone key into the browser.

Open **RFP Bids** and confirm that Bid1 and Bid2 show active, indexed documents. Then open **Chat** and select the intended bid in **Bid scope** before asking a question.

## 6. Run two smoke tests

For Bid1, ask:

```text
What device-provisioning work is required for Windows devices and for Chromebooks?
```

Expected answer: Windows devices require Autopilot; Chromebooks require enrollment in Google Management. The citation should reference Addendum 1, page 4.

For Bid2, ask:

```text
What warranty proof is due upon award, and when does the three-year coverage begin and end?
```

Expected answer: a warranty certificate or affidavit is due upon award; coverage runs from delivery through three years after delivery. The citation should reference the PORFP, page 3.

An uncached question normally takes longer because it performs embeddings, dense and sparse Pinecone searches, fusion, reranking, generation, grounding, and citation validation. A validated repeated question should return from cache. Submit once and wait; do not click **Ask** repeatedly.

## 7. Run the finalized ten-question verification

The versioned dataset contains five previously unseen Bid1 questions and five previously unseen Bid2 questions:

```powershell
docker compose exec -T api python -m evaluation.unseen_audit `
  --dataset evaluation/datasets/rfp_unseen_round2.json `
  --knowledge-base rfp-intelligence `
  --base-url http://api:8000 `
  --output outputs/reports/rfp-unseen-round2-manual.json `
  --allow-seen
```

The output file must not already exist because audit reports are immutable. Use a new filename for each run. The finalized reference report is:

```text
outputs/reports/rfp-unseen-round2-final-v3.json
```

Its required result is 10/10 supported questions passed, 100% sync/SSE parity, and 100% cache consistency.

## 8. Troubleshoot from the terminal

If the UI says systems are offline:

```powershell
docker compose ps -a
docker compose logs --tail 150 api worker
curl.exe -sS --max-time 60 http://localhost:8000/ready
```

If only a query is stuck, avoid starting duplicate requests. Inspect the API logs:

```powershell
docker compose logs --since 5m api
```

Restart only the stateless API and worker when necessary:

```powershell
docker compose restart api worker
```

Then wait for `/ready` before retrying once. Pinecone searches are bounded by `PINECONE_QUERY_TIMEOUT_SECONDS`, which defaults to 20 seconds, and the retriever can continue when one hybrid branch succeeds.

If an ingestion job remains queued:

```powershell
docker compose logs --tail 150 worker redis
```

## 9. Stop and restart later

Stop containers without removing them:

```powershell
docker compose stop
```

Start the same containers again:

```powershell
docker compose start
```

For a clean container restart while preserving all volumes and indexed data:

```powershell
docker compose down
docker compose up -d
```

Do **not** add `--volumes` unless you intentionally want to delete PostgreSQL, Redis, and managed local data.

