# PDF Unlock Web App v1 Plan (FastAPI, Cross-Platform, Batch Support)

## Summary
Build a cross-platform web application that accepts password-protected PDFs plus a user-provided password, decrypts them server-side, and returns unlocked copies for download.  
v1 is private/internal, Docker-deployable, metadata-logging only, and supports both single-file and batch workflows with partial-success reporting.

## Scope
- In scope:
  - Web UI for single and batch PDF upload.
  - One password input applied to all selected files per request.
  - Server-side decrypt/unlock and password removal.
  - Download unlocked files (single PDF or ZIP for batch).
  - Per-file status reporting for partial batch success.
  - Security controls: no file retention, metadata-only logs, request limits.
- Out of scope (v1):
  - OCR, PDF editing/merging, password recovery/bruteforce.
  - Multi-password-per-file UI.
  - User accounts/SSO.
  - Persistent storage.

## Architecture
- Frontend:
  - Simple SPA/static pages served by FastAPI (`/` for UI).
  - Upload form with drag/drop, password field, file list, progress, and result table.
- Backend:
  - FastAPI app with async endpoints.
  - PDF decryption service layer using a Python PDF library that supports encrypted PDFs and writing unlocked copies.
  - Temporary working directory per request; delete on completion/failure.
- Packaging/Deployment:
  - Docker image (multi-stage optional).
  - Reverse proxy compatible (Nginx/Traefik).
  - Config through env vars.

## Public Interfaces / API Contracts
- `POST /api/unlock`
  - `multipart/form-data`
  - Fields:
    - `password` (string, required)
    - `files` (1..20 PDF files, required)
  - Validation:
    - MIME/type and extension checks for PDF.
    - Max 25MB per file.
    - Max 200MB total request.
  - Response:
    - `200 OK` when at least one success.
    - `422` for validation errors.
    - `400` when all files fail decrypt/open.
    - JSON body:
      - `request_id` (string)
      - `summary` (`total`, `succeeded`, `failed`)
      - `results[]` with `original_name`, `status` (`success|failed`), `error_code` (nullable), `download_token` (nullable)
- `GET /api/download/{request_id}/{download_token}`
  - Returns unlocked PDF for single success.
- `GET /api/download-batch/{request_id}`
  - Returns ZIP containing all unlocked PDFs plus `report.json` with per-file errors/status.
- `GET /healthz`
  - Liveness check.

## Data Flow
1. Client selects files and enters password.
2. Backend validates limits and file types.
3. For each file:
   - Open encrypted PDF.
   - Attempt decrypt with provided password.
   - On success, write unlocked copy without password.
   - On failure, capture structured error code.
4. Persist outputs only in request temp workspace.
5. Return result manifest and download links/tokens.
6. After download window/response finalization, purge temp files immediately.

## Error Model
- Standard error codes for per-file failures:
  - `invalid_pdf`
  - `wrong_password_or_unsupported_encryption`
  - `decrypt_failed`
  - `file_too_large`
  - `internal_error`
- Batch behavior:
  - Partial success allowed.
  - Failures never block successful file downloads.
- User-facing messages:
  - Clear, non-sensitive, no stack traces.

## Security and Privacy Controls
- No retention of uploaded/original/unlocked files beyond request lifecycle.
- Never log passwords, filenames, or PDF content.
- Metadata-only logs:
  - `request_id`, counts, sizes, duration, status codes.
- Enforce request size and file count limits.
- Input sanitization:
  - Ignore client filenames for filesystem writes; generate safe internal names.
- Optional hardening for internal deployment:
  - IP allowlist / VPN boundary at ingress.

## Configuration (Env Vars)
- `MAX_FILES=20`
- `MAX_FILE_SIZE_MB=25`
- `MAX_TOTAL_SIZE_MB=200`
- `TEMP_DIR=/tmp/pdf_unlock`
- `DOWNLOAD_TTL_SECONDS=300`
- `LOG_LEVEL=INFO`

## Implementation Plan
1. Project bootstrap:
   - FastAPI app structure, dependency management, Dockerfile, `.env.example`.
2. Core decryption service:
   - Abstraction `unlock_pdf(input_bytes, password) -> output_bytes | error_code`.
3. Upload + processing endpoint:
   - Multipart parsing, validation, per-file loop, structured response model.
4. Download subsystem:
   - Tokenized temporary artifact registry keyed by `request_id`.
5. Web UI:
   - Single page upload form, batch result table, download actions.
6. Cleanup lifecycle:
   - Ensure temp-file deletion on success, client cancel, and exceptions.
7. Logging + observability:
   - Request correlation IDs and timing metrics.
8. Containerization + run docs:
   - Docker build/run and internal deployment notes.

## Testing and Acceptance Criteria
- Unit tests:
  - Decrypt success for valid encrypted sample.
  - Wrong password handling.
  - Non-PDF rejection.
  - Output PDF is not password-protected.
- API tests:
  - Single-file success path.
  - Batch mixed success/failure path.
  - Limit enforcement (file count, per-file size, total size).
  - Download token validity/expiry.
- Security tests:
  - Confirm no password/filename in logs.
  - Temp files removed after completion/error.
- Manual QA scenarios:
  - Upload one locked PDF with correct password -> unlocked download works.
  - Upload multiple PDFs with one wrong/one right -> partial success + report.
  - Upload corrupted PDF -> friendly error shown.
- Acceptance criteria:
  - Works on Windows/macOS/Linux via Docker.
  - Returns unlocked copies only, never overwrites source files.
  - Handles up to configured conservative limits reliably.
  - Leaves no retained user file content after processing.

## Assumptions and Defaults Chosen
- App type: Web app.
- Platform: Cross-platform.
- Input scope: Single and batch.
- Processing model: Server-side API.
- Retention: No retention.
- Output: Download unlocked copies only.
- Stack: Python + FastAPI.
- Batch behavior: Partial success with explicit per-file report.
- Limits: 20 files, 25MB each, 200MB total.
- Access: Private/internal only.
- Deployment: Docker container.
- Logging: Metadata-only.
