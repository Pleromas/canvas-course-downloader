/**
 * Canvas Vault — Content Script (Entry Point)
 *
 * Initializes the extension on Canvas pages: injects buttons,
 * handles SPA navigation, and listens for messages from the
 * popup and background service worker.
 *
 * Module load order (all share the global scope):
 *   helpers.js → detector.js → canvas-api.js → ui.js → downloader.js → content.js
 */

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------

injectButton();

// Re-inject after Canvas SPA navigations
let lastUrl = location.href;
new MutationObserver(() => {
  if (location.href !== lastUrl) {
    lastUrl = location.href;
    setTimeout(injectButton, 1000);
  }
}).observe(document, { subtree: true, childList: true });

// Listen for messages from the popup and background
chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.type === "DOWNLOAD_STATUS") {
    updateDownloadPanel(request.payload);
    if (request.payload.done) pruneIncrementalInventory(request.payload.failedFiles || []);
    return;
  }
  if (request.action === "trigger_download") {
    downloadCurrentCourse();
    sendResponse({ status: "started" });
  } else if (request.action === "open_course_selector") {
    openCourseSelector();
    sendResponse({ status: "opened" });
  } else if (request.action === "get_status") {
    sendResponse({ isCanvas: isCanvas(), courseId: getCourseId(), isHomepage: isCanvasHomepage(), courseName: getCourseId() ? getCourseName() : null });
  }
});

// Check if downloads are already in progress (e.g. after SPA navigation)
chrome.runtime.sendMessage({ type: "GET_DOWNLOAD_STATUS" }, (status) => {
  if (chrome.runtime.lastError || !status) return;
  if (status.total > 0 && !status.done) updateDownloadPanel(status);
});

// Per-file mode records the incremental inventory before the background has
// downloaded anything. When a job fails, drop it from the inventory again so
// the next incremental run fetches it instead of skipping it forever.
const _prunedFailedJobIds = new Set();
function pruneIncrementalInventory(failedFiles) {
  const byCourse = new Map();
  for (const f of failedFiles) {
    if (!f.courseId || _prunedFailedJobIds.has(f.id)) continue;
    _prunedFailedJobIds.add(f.id);
    const key = `incremental_${f.courseId}`;
    if (!byCourse.has(key)) byCourse.set(key, []);
    byCourse.get(key).push(`${f.relPath || ""}${f.filename}`);
  }
  for (const [key, paths] of byCourse) {
    chrome.storage.local.get(key, (data) => {
      const record = data[key];
      if (!record) return;
      let changed = false;
      for (const p of paths) if (p in record) { delete record[p]; changed = true; }
      if (changed) chrome.storage.local.set({ [key]: record });
    });
  }
}
