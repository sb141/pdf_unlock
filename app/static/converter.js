(() => {
  const form = document.getElementById("convert-form");
  const fileInput = document.getElementById("convert-file");
  const submitButton = document.getElementById("convert-submit");
  const clearButton = document.getElementById("convert-clear");
  const status = document.getElementById("convert-status");
  const error = document.getElementById("convert-error");
  const result = document.getElementById("convert-result");
  const filename = document.getElementById("convert-filename");
  const download = document.getElementById("convert-download");
  const editButton = document.getElementById("convert-edit");

  function resetResult() {
    result.hidden = true;
    status.textContent = "";
    error.textContent = "";
    download.removeAttribute("href");
  }

  fileInput.addEventListener("change", resetResult);
  clearButton.addEventListener("click", () => {
    form.reset();
    resetResult();
  });
  editButton.addEventListener("click", () => window.openPdfEditor(download.href, filename.textContent));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = fileInput.files[0];
    if (!file) return;
    resetResult();
    if (!/\.(doc|docx)$/i.test(file.name)) {
      error.textContent = "Choose a Word document (.doc or .docx).";
      return;
    }
    const body = new FormData();
    body.append("file", file);
    submitButton.disabled = true;
    clearButton.disabled = true;
    fileInput.disabled = true;
    submitButton.textContent = "Converting...";
    status.textContent = "Converting your document to PDF...";
    try {
      const response = await fetch("/api/convert/word", { method: "POST", body });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(typeof payload.detail === "string" ? payload.detail : "Conversion failed. Please try again.");
      }
      filename.textContent = payload.filename;
      download.href = `/api/download/${payload.request_id}/${payload.download_token}`;
      result.hidden = false;
      status.textContent = "Your PDF is ready. Download it to review the result.";
    } catch (failure) {
      status.textContent = "";
      error.textContent = failure.message || "Conversion failed. Please try again.";
    } finally {
      submitButton.disabled = false;
      clearButton.disabled = false;
      fileInput.disabled = false;
      submitButton.textContent = "Convert to PDF";
    }
  });
})();
