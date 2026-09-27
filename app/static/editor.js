(() => {
  const openForm = document.getElementById("editor-open-form");
  const fileInput = document.getElementById("editor-file");
  const passwordInput = document.getElementById("editor-password");
  const section = document.getElementById("editor-section");
  const workspace = document.getElementById("editor-workspace");
  const errorEl = document.getElementById("editor-error");
  const statusEl = document.getElementById("editor-status");
  const image = document.getElementById("editor-page-image");
  const canvas = document.getElementById("editor-overlay");
  const stage = document.getElementById("page-stage");
  const viewport = document.getElementById("page-viewport");
  const context = canvas.getContext("2d");
  const pageLabel = document.getElementById("page-label");
  const prevButton = document.getElementById("page-prev");
  const nextButton = document.getElementById("page-next");
  const redactButton = document.getElementById("tool-redact");
  const textButton = document.getElementById("tool-text");
  const replaceToolButton = document.getElementById("tool-replace");
  const replaceButton = document.getElementById("replace-text");
  const deletePdfTextButton = document.getElementById("delete-pdf-text");
  const copySelectedTextButton = document.getElementById("copy-selected-text");
  const pasteCopiedTextButton = document.getElementById("paste-copied-text");
  const deleteAddedTextButton = document.getElementById("delete-added-text");
  const textOptions = document.getElementById("text-options");
  const textLabel = document.getElementById("editor-text-label");
  const selectedTextEl = document.getElementById("selected-text");
  const textInput = document.getElementById("editor-text");
  const fontSizeInput = document.getElementById("editor-font-size");
  const fontInput = document.getElementById("editor-font");
  const boldButton = document.getElementById("editor-bold");
  const colorInput = document.getElementById("editor-color");
  const zoomInput = document.getElementById("editor-zoom");
  const undoButton = document.getElementById("edit-undo");
  const clearButton = document.getElementById("edit-clear-page");
  const saveButton = document.getElementById("edit-save");
  const downloadLink = document.getElementById("edit-download");

  let session = null;
  let pageNumber = 0;
  let edits = [];
  let undoStack = [];
  let tool = "redact";
  let dragStart = null;
  let draft = null;
  let pageLoadId = 0;
  let spans = [];
  let selectedSpan = null;
  let selectedAddedText = null;
  let movingText = null;
  let resizingText = null;
  let copiedText = null;
  let pasteMode = false;

  async function apiError(response) {
    const body = await response.json().catch(() => ({}));
    return new Error(typeof body.detail === "string" ? body.detail : "Request failed");
  }

  function setError(error) {
    errorEl.textContent = error.message || String(error);
    statusEl.textContent = "";
  }

  function pageSize() {
    return session.pages[pageNumber];
  }

  function rememberEdits() {
    undoStack.push(edits.map((edit) => ({ ...edit })));
  }

  function selectAddedText(edit) {
    selectedAddedText = edit;
    deleteAddedTextButton.hidden = tool !== "text" || !edit;
    syncCopyPasteButtons();
  }

  function syncCopyPasteButtons() {
    const hasSelectedText = tool === "text" ? Boolean(selectedAddedText)
      : tool === "replace" && Boolean(selectedSpan);
    copySelectedTextButton.hidden = !hasSelectedText;
    pasteCopiedTextButton.hidden = !copiedText;
    pasteCopiedTextButton.textContent = pasteMode ? "Cancel paste" : "Paste copied text";
  }

  function deleteSelectedAddedText() {
    if (tool !== "text" || !selectedAddedText || !edits.includes(selectedAddedText)) return;
    rememberEdits();
    edits = edits.filter((edit) => edit !== selectedAddedText);
    selectAddedText(null);
    downloadLink.style.display = "none";
    statusEl.textContent = "Added text removed from pending edits. Undo to restore it.";
    draw();
  }

  function pagePoint(event) {
    const bounds = canvas.getBoundingClientRect();
    const page = pageSize();
    return {
      x: Math.min(page.width, Math.max(0, (event.clientX - bounds.left) * page.width / bounds.width)),
      y: Math.min(page.height, Math.max(0, (event.clientY - bounds.top) * page.height / bounds.height)),
    };
  }

  function canvasPoint(event) {
    const bounds = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - bounds.left) * canvas.width / bounds.width,
      y: (event.clientY - bounds.top) * canvas.height / bounds.height,
    };
  }

  function fontCss(name, size) {
    const family = name.startsWith("ti") ? "Georgia, serif" : name.startsWith("co") ? "monospace" : "Arial, sans-serif";
    const bold = isBoldFont(name) ? "bold " : "";
    const italic = ["heit", "hebi", "tiit", "tibi", "coit", "cobi"].includes(name) ? "italic " : "";
    return `${italic}${bold}${size}px ${family}`;
  }

  const boldFontPairs = [["helv", "hebo"], ["heit", "hebi"], ["tiro", "tibo"],
    ["tiit", "tibi"], ["cour", "cobo"], ["coit", "cobi"]];

  function isBoldFont(name) {
    return boldFontPairs.some(([, bold]) => name === bold);
  }

  function fontWithBold(name, enabled) {
    const pair = boldFontPairs.find(([regular, bold]) => name === regular || name === bold);
    return pair ? pair[enabled ? 1 : 0] : name;
  }

  function syncBoldButton() {
    boldButton.setAttribute("aria-pressed", String(isBoldFont(fontInput.value)));
  }

  function addedTextBounds(edit) {
    const page = pageSize();
    const sx = canvas.width / page.width;
    const sy = canvas.height / page.height;
    context.font = fontCss(edit.font_name, Math.max(8, edit.font_size * sy));
    const metrics = context.measureText(edit.text);
    const x = edit.x * sx;
    const y = edit.y * sy;
    const scaleX = edit.scale_x || 1;
    const scaleY = edit.scale_y || 1;
    const ascent = (metrics.actualBoundingBoxAscent || edit.font_size * sy) * scaleY;
    const descent = (metrics.actualBoundingBoxDescent || edit.font_size * sy * 0.25) * scaleY;
    return {
      x0: x - 5, y0: y - ascent - 5,
      x1: x + metrics.width * scaleX + 5, y1: y + descent + 5,
      contentWidth: Math.max(1, metrics.width * scaleX),
      contentHeight: Math.max(1, ascent + descent),
    };
  }

  function addedTextAt(event) {
    const { x, y } = canvasPoint(event);
    return edits.filter((edit) => edit.kind === "text" && edit.page === pageNumber).reverse().find((edit) => {
      const box = addedTextBounds(edit);
      return x >= box.x0 && x <= box.x1 && y >= box.y0 && y <= box.y1;
    }) || null;
  }

  function overResizeHandle(event) {
    if (tool !== "text" || !selectedAddedText) return false;
    const { x, y } = canvasPoint(event);
    const box = addedTextBounds(selectedAddedText);
    return Math.abs(x - box.x1) <= 7 && Math.abs(y - box.y1) <= 7;
  }

  function applyZoom() {
    if (!session) return;
    const width = pageSize().width;
    const scale = zoomInput.value === "fit"
      ? Math.min(2.5, Math.max(0.25, (viewport.clientWidth - 42) / width))
      : Number(zoomInput.value);
    stage.style.width = `${Math.round(width * scale)}px`;
    draw();
  }

  function draw() {
    if (!session || !image.complete || !image.naturalWidth) return;
    canvas.width = image.clientWidth;
    canvas.height = image.clientHeight;
    context.clearRect(0, 0, canvas.width, canvas.height);
    const page = pageSize();
    const sx = canvas.width / page.width;
    const sy = canvas.height / page.height;
    if (tool === "replace") {
      for (const span of spans) {
        context.fillStyle = span === selectedSpan ? "rgba(15, 118, 110, 0.18)" : "rgba(15, 118, 110, 0.05)";
        context.strokeStyle = span === selectedSpan ? "#0f766e" : "rgba(15, 118, 110, 0.45)";
        context.lineWidth = span === selectedSpan ? 2 : 1;
        context.fillRect(span.x0 * sx, span.y0 * sy, (span.x1 - span.x0) * sx, (span.y1 - span.y0) * sy);
        context.strokeRect(span.x0 * sx, span.y0 * sy, (span.x1 - span.x0) * sx, (span.y1 - span.y0) * sy);
      }
    }
    const visible = edits.filter((edit) => edit.page === pageNumber);
    if (draft) visible.push(draft);
    for (const edit of visible) {
      if (edit.kind === "redact") {
        context.fillStyle = "rgba(0, 0, 0, 0.8)";
        context.fillRect(edit.x0 * sx, edit.y0 * sy, (edit.x1 - edit.x0) * sx, (edit.y1 - edit.y0) * sy);
        context.strokeStyle = "#fff";
        context.strokeRect(edit.x0 * sx, edit.y0 * sy, (edit.x1 - edit.x0) * sx, (edit.y1 - edit.y0) * sy);
      } else {
        if (edit.kind === "replace") {
          context.fillStyle = "#fff";
          context.fillRect(edit.x0 * sx, edit.y0 * sy, (edit.x1 - edit.x0) * sx, (edit.y1 - edit.y0) * sy);
        }
        if (edit.text) {
          context.fillStyle = edit.color;
          context.font = fontCss(edit.font_name, Math.max(8, edit.font_size * sy));
          context.save();
          context.translate((edit.kind === "replace" ? edit.origin_x : edit.x) * sx,
            (edit.kind === "replace" ? edit.origin_y : edit.y) * sy);
          context.scale(edit.scale_x || 1, edit.scale_y || 1);
          context.fillText(edit.text, 0, 0);
          context.restore();
        }
      }
    }
    if (tool === "text" && selectedAddedText && selectedAddedText.page === pageNumber) {
      const box = addedTextBounds(selectedAddedText);
      context.strokeStyle = "#0f766e";
      context.lineWidth = 1.5;
      context.setLineDash([5, 3]);
      context.strokeRect(box.x0, box.y0, box.x1 - box.x0, box.y1 - box.y0);
      context.setLineDash([]);
      context.fillStyle = "#0f766e";
      context.fillRect(box.x1 - 5, box.y1 - 5, 10, 10);
      context.strokeStyle = "#fff";
      context.lineWidth = 1;
      context.strokeRect(box.x1 - 5, box.y1 - 5, 10, 10);
    }
    if (tool === "replace" && selectedSpan) {
      const pending = edits.find((edit) => edit.kind === "replace" && edit.page === pageNumber && edit.span_id === selectedSpan.span_id);
      context.strokeStyle = pending && !pending.text ? "#b91c1c" : "#0f766e";
      context.lineWidth = 2;
      context.setLineDash([5, 3]);
      context.strokeRect(selectedSpan.x0 * sx, selectedSpan.y0 * sy,
        (selectedSpan.x1 - selectedSpan.x0) * sx, (selectedSpan.y1 - selectedSpan.y0) * sy);
      context.setLineDash([]);
    }
  }

  async function showPage(number) {
    if (!session || number < 0 || number >= session.pages.length) return;
    const loadId = ++pageLoadId;
    pageNumber = number;
    dragStart = null;
    draft = null;
    movingText = null;
    resizingText = null;
    pasteMode = false;
    selectAddedText(null);
    canvas.style.pointerEvents = "none";
    selectedSpan = null;
    syncCopyPasteButtons();
    selectedTextEl.hidden = true;
    replaceButton.hidden = true;
    deletePdfTextButton.hidden = true;
    pageLabel.textContent = `Page ${number + 1} of ${session.pages.length}`;
    prevButton.disabled = number === 0;
    nextButton.disabled = number === session.pages.length - 1;
    statusEl.textContent = "Loading page...";
    const url = `/api/edit/${session.request_id}/${session.source_token}/pages/${number}`;
    const [response, textResponse] = await Promise.all([
      fetch(url, { cache: "no-store" }),
      fetch(`${url}/text`, { cache: "no-store" }),
    ]);
    if (!response.ok) throw await apiError(response);
    if (!textResponse.ok) throw await apiError(textResponse);
    const pageSpans = await textResponse.json();
    const blobUrl = URL.createObjectURL(await response.blob());
    if (loadId !== pageLoadId) {
      URL.revokeObjectURL(blobUrl);
      return;
    }
    const previousUrl = image.dataset.blobUrl;
    image.src = blobUrl;
    image.dataset.blobUrl = blobUrl;
    try {
      await image.decode();
    } finally {
      if (previousUrl) URL.revokeObjectURL(previousUrl);
    }
    if (loadId !== pageLoadId) return;
    spans = pageSpans;
    applyZoom();
    canvas.style.pointerEvents = "auto";
    statusEl.textContent = tool === "replace" && !spans.length
      ? "This page has no selectable text. Use Redact area and Add text instead."
      : tool === "replace" ? "Click highlighted text to replace or delete it."
      : tool === "text" ? "Click to add text. Drag it to move; drag its corner horizontally for width, vertically for height, or diagonally for both."
      : "Drag over content to redact it. Use Zoom for small fields.";
  }

  async function openFile(file, password = "") {
    errorEl.textContent = "";
    statusEl.textContent = "Opening PDF...";
    workspace.hidden = true;
    downloadLink.style.display = "none";
    const form = new FormData();
    form.append("file", file);
    form.append("password", password);
    const response = await fetch("/api/edit/session", { method: "POST", body: form });
    if (!response.ok) throw await apiError(response);
    session = await response.json();
    pageLoadId += 1;
    pageNumber = 0;
    edits = [];
    undoStack = [];
    spans = [];
    selectedSpan = null;
    copiedText = null;
    pasteMode = false;
    selectAddedText(null);
    movingText = null;
    resizingText = null;
    textInput.value = "";
    fontSizeInput.value = "12";
    fontInput.value = "helv";
    syncBoldButton();
    colorInput.value = "#000000";
    setTool("redact");
    workspace.hidden = false;
    section.scrollIntoView({ behavior: "smooth", block: "start" });
    await showPage(0);
  }

  openForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!fileInput.files.length) return;
    try {
      await openFile(fileInput.files[0], passwordInput.value);
    } catch (error) {
      setError(error);
    }
  });

  window.openPdfEditor = async (url, filename) => {
    window.navigateTo("/edit");
    errorEl.textContent = "";
    statusEl.textContent = "Opening unlocked PDF...";
    section.scrollIntoView({ behavior: "smooth", block: "start" });
    try {
      const response = await fetch(url);
      if (!response.ok) throw await apiError(response);
      const file = new File([await response.blob()], filename, { type: "application/pdf" });
      await openFile(file);
    } catch (error) {
      setError(error);
    }
  };

  function setTool(nextTool) {
    if (nextTool !== "text") pasteMode = false;
    tool = nextTool;
    redactButton.setAttribute("aria-pressed", String(tool === "redact"));
    textButton.setAttribute("aria-pressed", String(tool === "text"));
    replaceToolButton.setAttribute("aria-pressed", String(tool === "replace"));
    syncBoldButton();
    deleteAddedTextButton.hidden = tool !== "text" || !selectedAddedText;
    deletePdfTextButton.hidden = tool !== "replace" || !selectedSpan;
    textOptions.hidden = tool === "redact";
    syncCopyPasteButtons();
    textLabel.textContent = tool === "replace" ? "Replacement text" : "Text to add";
    replaceButton.hidden = tool !== "replace" || !selectedSpan;
    selectedTextEl.hidden = tool !== "replace" || !selectedSpan;
    statusEl.textContent = tool === "redact" ? "Drag over content to redact it."
      : tool === "text" ? "Click to add text. Drag it to move; drag its corner horizontally for width, vertically for height, or diagonally for both."
      : spans.length ? "Click highlighted text to replace or delete it." : "This page has no selectable text. Use Redact area and Add text instead.";
    draw();
  }

  redactButton.addEventListener("click", () => setTool("redact"));
  textButton.addEventListener("click", () => setTool("text"));
  replaceToolButton.addEventListener("click", () => setTool("replace"));
  deleteAddedTextButton.addEventListener("click", deleteSelectedAddedText);
  deletePdfTextButton.addEventListener("click", deleteSelectedPdfText);
  copySelectedTextButton.addEventListener("click", copySelectedText);
  pasteCopiedTextButton.addEventListener("click", beginPasteCopiedText);
  canvas.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && pasteMode) {
      event.preventDefault();
      pasteMode = false;
      syncCopyPasteButtons();
      statusEl.textContent = "Paste canceled.";
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c") {
      if ((tool === "text" && selectedAddedText) || (tool === "replace" && selectedSpan)) {
        event.preventDefault();
        copySelectedText();
      }
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v" && copiedText) {
      event.preventDefault();
      beginPasteCopiedText();
      return;
    }
    if (event.key !== "Delete" && event.key !== "Backspace") return;
    if (!(tool === "text" && selectedAddedText) && !(tool === "replace" && selectedSpan)) return;
    event.preventDefault();
    if (tool === "text") deleteSelectedAddedText();
    else deleteSelectedPdfText();
  });
  prevButton.addEventListener("click", () => showPage(pageNumber - 1).catch(setError));
  nextButton.addEventListener("click", () => showPage(pageNumber + 1).catch(setError));
  zoomInput.addEventListener("change", applyZoom);
  window.addEventListener("resize", () => { if (zoomInput.value === "fit") applyZoom(); else draw(); });

  canvas.addEventListener("pointermove", (event) => {
    if (tool === "text" && !movingText && !resizingText) {
      canvas.style.cursor = overResizeHandle(event) ? "nwse-resize" : addedTextAt(event) ? "move" : "crosshair";
    }
  });
  canvas.addEventListener("pointerleave", () => { if (!movingText && !resizingText) canvas.style.cursor = "crosshair"; });

  fontSizeInput.addEventListener("change", () => {
    if (tool !== "text" || !selectedAddedText) return;
    const size = Number(fontSizeInput.value);
    if (!Number.isFinite(size) || size < 6 || size > 72) {
      errorEl.textContent = "Choose a font size between 6 and 72.";
      fontSizeInput.value = String(selectedAddedText.font_size);
      return;
    }
    if (size === selectedAddedText.font_size) return;
    errorEl.textContent = "";
    rememberEdits();
    selectedAddedText.font_size = size;
    downloadLink.style.display = "none";
    draw();
  });

  function updateSelectedAddedStyle(property, value) {
    if (tool !== "text" || !selectedAddedText || !edits.includes(selectedAddedText)) return;
    if (selectedAddedText[property] === value) return;
    rememberEdits();
    selectedAddedText[property] = value;
    downloadLink.style.display = "none";
    statusEl.textContent = "Text style updated. Apply edits to save it.";
    draw();
  }

  colorInput.addEventListener("change", () => updateSelectedAddedStyle("color", colorInput.value));
  fontInput.addEventListener("change", () => {
    syncBoldButton();
    updateSelectedAddedStyle("font_name", fontInput.value);
  });
  boldButton.addEventListener("click", () => {
    fontInput.value = fontWithBold(fontInput.value, !isBoldFont(fontInput.value));
    syncBoldButton();
    updateSelectedAddedStyle("font_name", fontInput.value);
  });

  function currentStyle() {
    const fontSize = Number(fontSizeInput.value);
    if (!textInput.value.trim() || !Number.isFinite(fontSize) || fontSize < 6 || fontSize > 72) {
      errorEl.textContent = "Enter text and a font size between 6 and 72.";
      return null;
    }
    errorEl.textContent = "";
    return { text: textInput.value.trim(), font_size: fontSize, font_name: fontInput.value, color: colorInput.value };
  }

  canvas.addEventListener("pointerdown", (event) => {
    if (!session) return;
    const point = pagePoint(event);
    if (tool === "replace") {
      selectedSpan = spans.filter((span) => point.x >= span.x0 && point.x <= span.x1 && point.y >= span.y0 && point.y <= span.y1)
        .sort((a, b) => (a.x1 - a.x0) * (a.y1 - a.y0) - (b.x1 - b.x0) * (b.y1 - b.y0))[0] || null;
      replaceButton.hidden = !selectedSpan;
      deletePdfTextButton.hidden = !selectedSpan;
      syncCopyPasteButtons();
      selectedTextEl.hidden = !selectedSpan;
      if (selectedSpan) {
        canvas.focus({ preventScroll: true });
        const pending = edits.find((edit) => edit.kind === "replace" && edit.page === pageNumber && edit.span_id === selectedSpan.span_id);
        textInput.value = pending?.text ?? selectedSpan.text;
        fontSizeInput.value = Math.min(72, Math.max(6, pending?.font_size || selectedSpan.font_size)).toFixed(1);
        fontInput.value = pending?.font_name || selectedSpan.font_name;
        syncBoldButton();
        colorInput.value = pending?.color || selectedSpan.color;
        selectedTextEl.textContent = `Selected: "${selectedSpan.text}". Replace, delete, or copy it.`;
        statusEl.textContent = "Selected PDF text is ready to replace, delete, or copy.";
      } else {
        statusEl.textContent = "Click inside a highlighted text run to select it.";
      }
      draw();
      return;
    }
    if (tool === "text") {
      if (pasteMode && copiedText) {
        pasteCopiedTextAt(point);
        return;
      }
      if (overResizeHandle(event)) {
        const box = addedTextBounds(selectedAddedText);
        resizingText = {
          edit: selectedAddedText,
          start: canvasPoint(event),
          width: box.contentWidth,
          height: box.contentHeight,
          scaleX: selectedAddedText.scale_x || 1,
          scaleY: selectedAddedText.scale_y || 1,
          before: edits.map((edit) => ({ ...edit })),
        };
        canvas.setPointerCapture(event.pointerId);
        canvas.style.cursor = "nwse-resize";
        statusEl.textContent = "Drag horizontally to change width, vertically to change height, or diagonally to change both.";
        return;
      }
      const existing = addedTextAt(event);
      if (existing) {
        selectAddedText(existing);
        fontSizeInput.value = String(existing.font_size);
        fontInput.value = existing.font_name;
        syncBoldButton();
        colorInput.value = existing.color;
        canvas.focus({ preventScroll: true });
        movingText = { edit: existing, x: existing.x, y: existing.y,
          before: edits.map((edit) => ({ ...edit })),
          offsetX: existing.x - point.x, offsetY: existing.y - point.y };
        canvas.setPointerCapture(event.pointerId);
        canvas.style.cursor = "move";
        statusEl.textContent = "Drag to move this text. Apply edits to save its position.";
        draw();
        return;
      }
      const style = currentStyle();
      if (!style) return;
      rememberEdits();
      selectAddedText({ kind: "text", page: pageNumber, x: point.x, y: point.y, ...style, scale_x: 1, scale_y: 1 });
      edits.push(selectedAddedText);
      canvas.focus({ preventScroll: true });
      downloadLink.style.display = "none";
      statusEl.textContent = "Text added. Drag it to another position, then apply edits.";
      draw();
      return;
    }
    dragStart = point;
    canvas.setPointerCapture(event.pointerId);
  });

  function copySelectedText() {
    let source = null;
    if (tool === "text" && selectedAddedText) {
      source = selectedAddedText;
    } else if (tool === "replace" && selectedSpan) {
      const pending = edits.find((edit) => edit.kind === "replace" && edit.page === pageNumber && edit.span_id === selectedSpan.span_id);
      const selectedSize = Number(fontSizeInput.value);
      source = {
        text: textInput.value,
        font_size: Number.isFinite(selectedSize) && selectedSize > 0 ? selectedSize : pending?.font_size ?? selectedSpan.font_size,
        font_name: fontInput.value || pending?.font_name || selectedSpan.font_name,
        color: colorInput.value || pending?.color || selectedSpan.color,
        scale_x: 1,
        scale_y: 1,
      };
    }
    if (!source || !source.text.trim()) {
      statusEl.textContent = "There is no text to copy from this selection.";
      return;
    }
    if (source.text.length > 500) {
      statusEl.textContent = "This text is too long to copy into an added text box (500 characters maximum).";
      return;
    }
    copiedText = {
      text: source.text,
      font_size: Math.min(72, Math.max(6, source.font_size)),
      font_name: source.font_name,
      color: source.color,
      scale_x: source.scale_x || 1,
      scale_y: source.scale_y || 1,
    };
    syncCopyPasteButtons();
    statusEl.textContent = "Text copied. Click Paste copied text, then click where you want the copy placed.";
  }

  function beginPasteCopiedText() {
    if (!copiedText) return;
    if (pasteMode) {
      pasteMode = false;
      syncCopyPasteButtons();
      statusEl.textContent = "Paste canceled.";
      return;
    }
    pasteMode = true;
    setTool("text");
    textInput.value = copiedText.text;
    fontSizeInput.value = String(copiedText.font_size);
    fontInput.value = copiedText.font_name;
    syncBoldButton();
    colorInput.value = copiedText.color;
    statusEl.textContent = "Click the page where you want to place the copied text.";
  }

  function pasteCopiedTextAt(point) {
    if (!copiedText) return;
    const pasted = {
      kind: "text", page: pageNumber, x: point.x, y: point.y,
      ...copiedText,
    };
    rememberEdits();
    edits.push(pasted);
    selectAddedText(pasted);
    pasteMode = false;
    fontSizeInput.value = String(pasted.font_size);
    fontInput.value = pasted.font_name;
    syncBoldButton();
    colorInput.value = pasted.color;
    canvas.focus({ preventScroll: true });
    downloadLink.style.display = "none";
    statusEl.textContent = "Copied text pasted. Drag it to move or resize it, then apply edits to save.";
    draw();
  }

  function queueSelectedPdfText(style) {
    rememberEdits();
    edits = edits.filter((edit) => !(edit.kind === "replace" && edit.page === pageNumber && edit.span_id === selectedSpan.span_id));
    edits.push({
      kind: "replace", page: pageNumber, span_id: selectedSpan.span_id,
      x0: selectedSpan.x0, y0: selectedSpan.y0, x1: selectedSpan.x1, y1: selectedSpan.y1,
      origin_x: selectedSpan.origin_x, origin_y: selectedSpan.origin_y, ...style,
    });
    downloadLink.style.display = "none";
    draw();
  }

  replaceButton.addEventListener("click", () => {
    if (!selectedSpan) return;
    const style = currentStyle();
    if (!style) return;
    queueSelectedPdfText(style);
    statusEl.textContent = "Text replacement queued. Apply edits to save it to the PDF.";
  });

  function deleteSelectedPdfText() {
    if (tool !== "replace" || !selectedSpan) return;
    queueSelectedPdfText({ text: "", font_size: null, font_name: null, color: null });
    textInput.value = "";
    statusEl.textContent = "Selected PDF text will be removed when you apply edits. Undo to restore it.";
  }

  canvas.addEventListener("pointermove", (event) => {
    if (resizingText) {
      const position = canvasPoint(event);
      const widthRatio = Math.max(0.25, Math.min(8,
        resizingText.scaleX * (resizingText.width + position.x - resizingText.start.x) / resizingText.width));
      const heightRatio = Math.max(0.25, Math.min(8,
        resizingText.scaleY * (resizingText.height + position.y - resizingText.start.y) / resizingText.height));
      resizingText.edit.scale_x = widthRatio;
      resizingText.edit.scale_y = heightRatio;
      draw();
      return;
    }
    if (movingText) {
      const point = pagePoint(event);
      const page = pageSize();
      movingText.edit.x = Math.max(0, Math.min(page.width, point.x + movingText.offsetX));
      movingText.edit.y = Math.max(0, Math.min(page.height, point.y + movingText.offsetY));
      draw();
      return;
    }
    if (!dragStart) return;
    const point = pagePoint(event);
    draft = {
      kind: "redact", page: pageNumber,
      x0: Math.min(dragStart.x, point.x), y0: Math.min(dragStart.y, point.y),
      x1: Math.max(dragStart.x, point.x), y1: Math.max(dragStart.y, point.y),
    };
    draw();
  });

  canvas.addEventListener("pointerup", (event) => {
    if (resizingText) {
      if (resizingText.edit.scale_x !== resizingText.scaleX || resizingText.edit.scale_y !== resizingText.scaleY) {
        undoStack.push(resizingText.before);
        downloadLink.style.display = "none";
        statusEl.textContent = "Text box resized. Apply edits to save it.";
      }
      resizingText = null;
      canvas.releasePointerCapture(event.pointerId);
      canvas.style.cursor = overResizeHandle(event) ? "nwse-resize" : addedTextAt(event) ? "move" : "crosshair";
      draw();
      return;
    }
    if (movingText) {
      if (movingText.edit.x !== movingText.x || movingText.edit.y !== movingText.y) {
        undoStack.push(movingText.before);
        downloadLink.style.display = "none";
      }
      movingText = null;
      canvas.releasePointerCapture(event.pointerId);
      canvas.style.cursor = addedTextAt(event) ? "move" : "crosshair";
      draw();
      return;
    }
    if (!dragStart) return;
    const point = pagePoint(event);
    const redaction = {
      kind: "redact", page: pageNumber,
      x0: Math.min(dragStart.x, point.x), y0: Math.min(dragStart.y, point.y),
      x1: Math.max(dragStart.x, point.x), y1: Math.max(dragStart.y, point.y),
    };
    if (redaction.x1 - redaction.x0 >= 3 && redaction.y1 - redaction.y0 >= 3) {
      rememberEdits();
      edits.push(redaction);
      downloadLink.style.display = "none";
    }
    dragStart = null;
    draft = null;
    canvas.releasePointerCapture(event.pointerId);
    draw();
  });
  canvas.addEventListener("pointercancel", () => {
    if (resizingText) {
      resizingText.edit.scale_x = resizingText.scaleX;
      resizingText.edit.scale_y = resizingText.scaleY;
      resizingText = null;
    }
    if (movingText) {
      movingText.edit.x = movingText.x;
      movingText.edit.y = movingText.y;
      movingText = null;
    }
    canvas.style.cursor = "crosshair";
    dragStart = null;
    draft = null;
    draw();
  });

  undoButton.addEventListener("click", () => {
    if (!undoStack.length) return;
    const selectedIndex = tool === "text" ? edits.indexOf(selectedAddedText) : -1;
    edits = undoStack.pop();
    const restored = edits[selectedIndex];
    selectAddedText(restored?.kind === "text" && restored.page === pageNumber ? restored : null);
    if (selectedAddedText) {
      fontSizeInput.value = String(selectedAddedText.font_size);
      fontInput.value = selectedAddedText.font_name;
      syncBoldButton();
      colorInput.value = selectedAddedText.color;
    }
    if (tool === "replace" && selectedSpan) {
      const pending = edits.find((edit) => edit.kind === "replace" && edit.page === pageNumber && edit.span_id === selectedSpan.span_id);
      textInput.value = pending?.text ?? selectedSpan.text;
    }
    draw();
    downloadLink.style.display = "none";
  });
  clearButton.addEventListener("click", () => {
    if (!edits.some((edit) => edit.page === pageNumber)) return;
    rememberEdits();
    edits = edits.filter((edit) => edit.page !== pageNumber);
    selectAddedText(null);
    draw();
    downloadLink.style.display = "none";
  });

  saveButton.addEventListener("click", async () => {
    if (!session || !edits.length) {
      errorEl.textContent = "Add a redaction, add text, or replace text before saving.";
      return;
    }
    saveButton.disabled = true;
    errorEl.textContent = "";
    statusEl.textContent = "Applying edits...";
    try {
      const body = {
        redactions: edits.filter((edit) => edit.kind === "redact").map(({ kind, ...edit }) => edit),
        texts: edits.filter((edit) => edit.kind === "text").map(({ kind, ...edit }) => edit),
        replacements: edits.filter((edit) => edit.kind === "replace").map((edit) => ({
          page: edit.page, span_id: edit.span_id, text: edit.text,
          font_size: edit.font_size, font_name: edit.font_name, color: edit.color,
        })),
      };
      const response = await fetch(`/api/edit/${session.request_id}/${session.source_token}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!response.ok) throw await apiError(response);
      const result = await response.json();
      downloadLink.href = `/api/download/${result.request_id}/${result.download_token}`;
      downloadLink.style.display = "inline";
      statusEl.textContent = "Edited PDF is ready. Download it to review the result.";
    } catch (error) {
      setError(error);
    } finally {
      saveButton.disabled = false;
    }
  });
})();
