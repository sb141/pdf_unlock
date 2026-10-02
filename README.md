# PDF Toolkit

A web app for unlocking password-protected PDFs and editing their pages. The home page links to **Unlock PDFs** and **Edit & redact**.

## Features

- Unlock one PDF or a batch with the correct password. View the result for each file and download successful files individually or as a ZIP.
- Zoom and scroll through PDF pages while editing.
- Add text, then move it, resize its width and height separately, recolor it, change its font, or delete it before saving.
- Replace or delete selectable PDF text.
- Redact a selected area and download a new PDF with the page content in that area removed.
- Unlock AES-encrypted PDFs while preserving bookmarks and document metadata.

## Run locally

Python 3.12 is used by the Docker image. From the project root, create a virtual environment and install the dependencies:

```bash
python -m venv .venv
```

Activate it with the command for your shell:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS or Linux
source .venv/bin/activate
```

On Windows, copy `.env.example` to `.env` and set `TEMP_DIR=./tmp/pdf_unlock` before starting the app. The default temporary path is intended for Unix systems.

Then start the app:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`. The tools are also available directly at `/unlock` and `/edit`.

## Unlock PDFs

Open **Unlock PDFs**, enter the password, and choose one or more PDF files. The results show which files were unlocked. Download each successful file, or download the batch ZIP when at least two files succeed. An unlocked result also has an **Edit** button that opens it in the editor.

The default limits are 20 files per request, 25 MB per file, and 200 MB per request. Change these in `.env` if needed.

## Edit and redact a PDF

Open **Edit & redact** and choose a PDF. If it is locked, enter its password. You can also click **Edit** beside a successful unlock result.

1. Use **Zoom** to enlarge the page and scroll to the content you want to change.
2. Choose **Redact area** and drag a rectangle over the content to remove. The saved area is filled black.
3. Choose **Add text**, enter the text and its size, font, and color, and use **Bold** if needed. Click the page to place it. Click placed text to select it. Drag it to move it. Drag its corner horizontally to change width, vertically to change height, or diagonally to change both. You can also change its size, font, color, or bold setting, or click **Delete added text**.
4. Choose **Edit text** and click a highlighted text run. Enter new text or change its style, including **Bold**, then click **Replace selected text**. To duplicate the text elsewhere, click **Copy selected text**, then **Paste copied text**, and click the destination on the page. The original stays in place. Click **Delete selected PDF text** to remove it without a replacement.
5. Use **Undo last edit** or **Clear this page** to revise pending changes. Press Delete or Backspace while the page is focused to remove selected text. Click **Apply edits and download** to create and download the edited PDF.

The source PDF remains unchanged. Editing works on up to 200 pages per PDF. Edit sessions expire after one hour by default; generated downloads expire after five minutes by default.

### Editing limits

- **Edit text** works on selectable PDF text. Scanned pages have no selectable text; use redaction and added text for those pages.
- Replacing or deleting a text run fills its old area white. Replacement fonts are approximated with standard PDF fonts. Review the downloaded file when the original uses a complex font or a colored background.
- Area redaction removes page content in the selected rectangle. It does not remove matching text from metadata, attachments, or other parts of the document. Review the downloaded PDF before sharing it.
- Area redactions take precedence over added or replacement text that overlaps them in the saved PDF.

## Configuration

Copy `.env.example` to `.env` to change these defaults:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAX_FILES` | `20` | Maximum files in one unlock request |
| `MAX_FILE_SIZE_MB` | `25` | Maximum size of each PDF |
| `MAX_TOTAL_SIZE_MB` | `200` | Maximum total size of an unlock request |
| `TEMP_DIR` | `/tmp/pdf_unlock` | Directory for temporary PDFs and ZIPs |
| `DOWNLOAD_TTL_SECONDS` | `300` | Lifetime of generated downloads |
| `EDIT_SESSION_TTL_SECONDS` | `3600` | Lifetime of an editing session |
| `LOG_LEVEL` | `INFO` | Application log level |

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/unlock` | Unlock one or more uploaded PDFs |
| `GET /api/download/{request_id}/{download_token}` | Download an unlocked or edited PDF |
| `GET /api/download-batch/{request_id}` | Download a batch ZIP |
| `POST /api/edit/session` | Open a PDF for editing |
| `GET /api/edit/{request_id}/{source_token}/pages/{page_number}` | Render a page preview |
| `GET /api/edit/{request_id}/{source_token}/pages/{page_number}/text` | List selectable text runs |
| `POST /api/edit/{request_id}/{source_token}` | Apply redactions, added text, and text replacements or deletions |
| `GET /healthz` | Health check |

## Tests

```bash
pytest
```

## Docker

```bash
docker build -t pdf-toolkit .
docker run --rm -p 8000:8000 pdf-toolkit
```
