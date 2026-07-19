# PDF Unlock

Web application that removes password protection from PDF files when the correct password is provided.

## Features
- Single and batch PDF upload.
- Server-side unlock using `pypdf`.
- Per-file success/failure reporting.
- Download unlocked files individually.
- Download batch ZIP when multiple files are unlocked.
- Configurable limits via environment variables.

## Run Locally
```bash
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Environment Variables
Copy `.env.example` to `.env` and adjust:
- `MAX_FILES`
- `MAX_FILE_SIZE_MB`
- `MAX_TOTAL_SIZE_MB`
- `TEMP_DIR`
- `DOWNLOAD_TTL_SECONDS`
- `LOG_LEVEL`

## API Endpoints
- `POST /api/unlock`
- `GET /api/download/{request_id}/{download_token}`
- `GET /api/download-batch/{request_id}`
- `GET /healthz`

## Tests
```bash
pytest
```

## Docker
```bash
docker build -t pdf-unlock .
docker run --rm -p 8000:8000 pdf-unlock
```
