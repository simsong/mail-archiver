/* Drive the read-only pywebview mail browser while rejecting stale async responses. */
"use strict";

const state = {
  query: "",
  offset: 0,
  sortBy: "date",
  sortDirection: "descending",
  searchAttachments: false,
  results: [],
  resultPreviews: new Map(),
  resultPreviewPending: new Set(),
  resultPreviewBatch: new Set(),
  resultPreviewTimer: null,
  resultSelection: new Set(),
  resultTable: null,
  resultRangeAnchor: null,
  resultRangeSelection: [],
  resultRangeActive: false,
  suppressResultClick: false,
  highlightTerms: [],
  messageFindQuery: "",
  messageFindTargets: [],
  messageFindIndex: -1,
  messageFindRender: Promise.resolve(false),
  messageFindRenderRequest: 0,
  messageFindTimer: null,
  messageFindMarker: 0,
  commandAContext: "",
  highlightBackground: "transparent",
  selected: null,
  selectionRequest: null,
  searchRequest: 0,
  partRequest: 0,
  remoteContentAuthorizedMessage: null,
  remoteContentAuthorizedPart: null,
  view: null,
  dragExports: new Map(),
  dragPreparing: new Set(),
  previewUrl: null,
  ingestStatusText: "",
  linkDestination: "",
  pendingLink: "",
  showTree: false,
  showVolumes: false,
  mailboxTree: [],
  mailboxSelections: new Set(),
  filterSets: [],
  activeFilterSet: "",
  treeRequest: 0,
  searchFilters: [],
  suggestionRequest: 0,
  suggestionTimer: null,
  suggestionItems: [],
  suggestionIndex: -1,
};

const elements = {};
const byId = id => document.getElementById(id);
let initialized = false;
let previewQueue = Promise.resolve();
const SUGGESTION_MINIMUM = 3;
const SUGGESTION_DELAY_MS = 120;
const MESSAGE_FIND_DELAY_MS = 120;
const FRAME_LOAD_TIMEOUT_MS = 10_000;
const SUGGESTION_LIMIT = 20;
const INGEST_REFRESH_MS = 1000;
const RESULT_INITIAL_LIMIT = 2_000;

window.addEventListener("resize", () => {
  const frame = elements["body-view"]?.querySelector(".html-frame");
  if (frame) window.setTimeout(() => refreshHtmlFrameLayout(frame), 0);
});
window.addEventListener("pywebviewready", initialize);
window.setTimeout(() => {
  if (window.pywebview?.api?.status) initialize();
  else if (!initialized) showBridgeFailure();
}, 1500);

async function initialize() {
  if (initialized) return;
  initialized = true;
  const parameters = new URLSearchParams(window.location.search);
  if (parameters.get("native-smoke") === "1") {
    await runNativeSmoke();
    return;
  }
  for (const id of ["choose-archive", "search-form", "search", "search-filters", "search-suggestions", "search-help-template", "archive-label", "result-status", "results-pane", "result-list",
    "result-help",
    "sort-by", "sort-direction", "search-attachments", "show-original-folders", "mailbox-browser", "mailbox-tree", "show-source-volumes", "filter-set", "manage-filter-sets",
    "save-filter-dialog", "save-filter-form", "filter-set-name", "cancel-save-filter", "manage-filter-dialog", "filter-set-list", "close-filter-manager",
    "message-pane", "message-content", "message-well", "computed-date-banner", "message-selection-summary", "message-file-well", "message-file-name", "message-subject", "message-headers", "part-select", "message-find", "message-find-query", "message-find-status", "message-find-previous", "message-find-next", "message-find-close", "remote-content",
    "copy-message-text", "save-message", "print-message", "body-view", "attachment-section", "attachment-list", "attachment-preview", "provenance-section", "message-locations", "ingest-status-line", "link-dialog", "link-destination", "link-ignore", "link-copy", "link-open", "error"]) {
    elements[id] = byId(id);
  }
  initializeResultTable();
  renderSearchHelp();
  elements["choose-archive"].addEventListener("click", async () => {
    await chooseArchive();
    elements["choose-archive"].dataset.completed = String(Number(elements["choose-archive"].dataset.completed || 0) + 1);
  });
  elements["search-form"].addEventListener("submit", event => {
    event.preventDefault();
    if (state.suggestionIndex >= 0) acceptSuggestion(state.suggestionIndex);
    else { closeSuggestions(); runSearch(); }
  });
  elements.search.addEventListener("input", scheduleSuggestions);
  elements.search.addEventListener("keydown", navigateSuggestions);
  elements.search.addEventListener("blur", () => window.setTimeout(() => {
    if (document.activeElement !== elements.search) closeSuggestions();
  }, 150));
  elements["sort-by"].addEventListener("change", () => runSearch());
  elements["sort-direction"].addEventListener("click", toggleSortDirection);
  elements["search-attachments"].addEventListener("change", () => runSearch());
  elements["show-original-folders"].addEventListener("change", toggleMailboxTree);
  elements["show-source-volumes"].addEventListener("change", toggleSourceVolumes);
  elements["filter-set"].addEventListener("change", selectFilterSet);
  elements["manage-filter-sets"].addEventListener("click", openFilterManager);
  elements["save-filter-form"].addEventListener("submit", saveFilterSet);
  elements["cancel-save-filter"].addEventListener("click", () => elements["save-filter-dialog"].close());
  elements["close-filter-manager"].addEventListener("click", () => elements["manage-filter-dialog"].close());
  elements["result-list"].addEventListener("keydown", navigateResults);
  elements["result-list"].addEventListener("mousedown", () => { state.commandAContext = "results"; });
  elements["result-list"].addEventListener("focusin", () => { state.commandAContext = "results"; });
  elements["result-list"].addEventListener("selectstart", event => event.preventDefault());
  document.addEventListener("mouseup", finishResultRange);
  elements["message-pane"].addEventListener("mousedown", () => { state.commandAContext = "message"; });
  elements["part-select"].addEventListener("change", () => void selectMessagePart(Number(elements["part-select"].value), false));
  elements["message-find"].addEventListener("submit", event => { event.preventDefault(); void moveMessageFind(1); });
  elements["message-find-query"].addEventListener("input", scheduleMessageFindUpdate);
  elements["message-find-previous"].addEventListener("click", () => void moveMessageFind(-1));
  elements["message-find-close"].addEventListener("click", () => void closeMessageFind());
  elements["remote-content"].addEventListener("click", () => void selectMessagePart(Number(elements["part-select"].value), true));
  elements["copy-message-text"].addEventListener("click", () => copyVisibleMessageText());
  elements["save-message"].addEventListener("click", () => call(() => window.pywebview.api.save_message(state.selected)));
  elements["print-message"].addEventListener("click", () => window.print());
  elements["ingest-status-line"].addEventListener("click", openIngestWindow);
  elements["link-dialog"].addEventListener("close", dismissExternalLink);
  elements["link-copy"].addEventListener("click", () => void copyExternalLink());
  elements["link-open"].addEventListener("click", () => void openExternalLink());
  installDrag(elements["message-file-well"], selectedDragMessagePks);
  document.addEventListener("keydown", handleCommandShortcut);

  if (parameters.get("standalone") === "1") document.body.classList.add("standalone");
  const status = await call(() => window.pywebview.api.status());
  if (!status) return;
  state.highlightTerms = parameters.getAll("highlight");
  await loadFilterSets();
  applyStatus(status);
  await refreshIngestOverview();
  window.setInterval(refreshIngestOverview, INGEST_REFRESH_MS);
  const message = Number(parameters.get("message"));
  if (message) await selectMessage(message);
}

async function runNativeSmoke() {
  let passed = false;
  let error = null;
  try {
    const status = await window.pywebview.api.status();
    if (!status?.ready) throw new Error("smoke archive is not ready");
    const page = await window.pywebview.api.search('"message viewer"', 0, "date", "descending", false, [], false);
    const validPage = page && typeof page === "object"
      && Array.isArray(page.results)
      && Array.isArray(page.highlight_terms);
    if (!validPage || page.results.length !== 1 || !page.highlight_terms.includes("message viewer")) {
      throw new Error("native bridge search returned an invalid page");
    }
    if (new URLSearchParams(window.location.search).get("native-html-find-smoke") === "1") {
      await verifyNativeHtmlFindHighlight();
    }
    passed = true;
  } catch (failure) {
    const message = String(failure?.message || failure);
    error = failure?.stack && !String(failure.stack).includes(message) ? `${message}\n${failure.stack}` : message;
  }
  await window.pywebview.api.native_smoke_complete(passed, error);
  window.__mailarchiveNativeSmoke = {passed, error};
}

function frameDocumentIsReady(frame) {
  const document = frame.contentDocument;
  return Boolean(document?.body && frame.dataset.messageFindToken &&
    document.documentElement?.dataset.mailarchiverFrameToken === frame.dataset.messageFindToken);
}

function waitForFrameDocument(frame) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer = null;
    const deadline = Date.now() + FRAME_LOAD_TIMEOUT_MS;
    const complete = (callback, value) => {
      if (settled) return;
      settled = true;
      if (timer !== null) window.clearTimeout(timer);
      frame.removeEventListener("load", check);
      callback(value);
    };
    const schedule = () => {
      if (timer === null) timer = window.setTimeout(() => { timer = null; check(); }, 10);
    };
    const check = () => {
      if (frameDocumentIsReady(frame)) { complete(resolve); return; }
      if (!frame.isConnected) { complete(reject, new Error("message HTML iframe was removed before it initialized")); return; }
      if (Date.now() >= deadline) { complete(reject, new Error("message HTML iframe did not initialize")); return; }
      schedule();
    };
    frame.addEventListener("load", check, {once: true});
    check();
  });
}

function waitForFrameLayout() {
  return new Promise(resolve => window.setTimeout(resolve, 75));
}

function installFrameFindShortcuts(frame) {
  const frameDocument = frame.contentDocument;
  if (!frameDocument) return false;
  frameDocument.addEventListener("keydown", event => {
    if (!event.metaKey || event.altKey || !state.view) return;
    if (event.key.toLowerCase() === "f") {
      event.preventDefault();
      void openMessageFind();
    } else if (event.key.toLowerCase() === "g") {
      event.preventDefault();
      void moveMessageFind(event.shiftKey ? -1 : 1);
    }
  });
  return true;
}

function frameLink(event) {
  return event.target?.closest?.("a[href]") || null;
}

function approvedFrameLink(link) {
  try {
    const destination = new URL(link.href).href;
    return ["http:", "https:", "mailto:"].includes(new URL(destination).protocol) ? destination : "";
  } catch (_) {
    return "";
  }
}

function installFrameLinkHandlers(frame) {
  const document = frame.contentDocument;
  if (!document) return;
  const hover = event => {
    const link = frameLink(event);
    const destination = link && approvedFrameLink(link);
    if (destination) showLinkDestination(destination);
  };
  const leave = event => {
    const link = frameLink(event);
    if (link && !link.contains(event.relatedTarget)) clearLinkDestination(approvedFrameLink(link));
  };
  document.addEventListener("pointerover", hover);
  document.addEventListener("focusin", hover);
  document.addEventListener("pointerout", leave);
  document.addEventListener("focusout", leave);
  document.addEventListener("click", event => {
    const link = frameLink(event);
    const destination = link && approvedFrameLink(link);
    if (!destination) return;
    event.preventDefault();
    event.stopPropagation();
    presentExternalLink(destination);
  });
}

function presentExternalLink(destination) {
  state.pendingLink = destination;
  elements["link-destination"].textContent = destination;
  elements["link-dialog"].showModal();
}

function dismissExternalLink() {
  state.pendingLink = "";
  clearLinkDestination();
}

async function copyExternalLink() {
  const destination = state.pendingLink;
  if (!destination) return;
  await call(() => window.pywebview.api.copy_link(destination));
  elements["link-dialog"].close();
}

async function openExternalLink() {
  const destination = state.pendingLink;
  if (!destination) return;
  elements["link-dialog"].close();
  await call(() => window.pywebview.api.open_link(destination));
}

async function verifyNativeHtmlFindHighlight() {
  const pane = byId("message-pane");
  const content = byId("message-content");
  const well = byId("message-well");
  const headers = byId("message-headers");
  const body = byId("body-view");
  const status = byId("message-find-status");
  const query = byId("message-find-query");
  if (!pane || !content || !well || !headers || !body || !status || !query) throw new Error("native HTML find fixture is incomplete");
  Object.assign(elements, {"message-pane": pane, "message-well": well, "message-headers": headers,
    "body-view": body, "message-find-status": status, "message-find-query": query});
  const wasHidden = content.hidden;
  content.hidden = false;
  pane.scrollTop = 0;
  body.replaceChildren();
  headers.replaceChildren();
  state.highlightTerms = [];
  const previousView = state.view;
  state.view = {};
  state.messageFindQuery = "arden";
  query.value = state.messageFindQuery;
  state.messageFindTargets = [];
  state.messageFindIndex = -1;
  const header = document.createElement("dd");
  setHighlightedText(header, "Arden <arden@example.test>");
  headers.append(header);
  const frame = document.createElement("iframe");
  frame.className = "html-frame";
  frame.setAttribute("sandbox", "allow-popups allow-same-origin");
  let loads = 0;
  frame.addEventListener("load", () => { loads += 1; });
  const highlighted = highlightedHtml(
    '<style>mark { background: yellow !important; }</style>'
    + '<mark class="message-find-match message-find-current" data-mailarchiver-find-target="outer">decoy</mark>'
    + '<span id="message-find-0">decoy</span>'
    + `<p>Hi Arden.</p>${"<br>".repeat(180)}<p>Arden at the end.</p>`,
  );
  frame.srcdoc = highlighted.content;
  frame.dataset.messageFindToken = highlighted.marker;
  body.append(frame);
  try {
    await waitForFrameDocument(frame);
    const shortcutInstalled = installFrameFindShortcuts(frame);
    resizeFrame(frame);
    installFrameLayoutUpdates(frame);
    updateMessageFindTargets();
    await moveMessageFind(1);
    await moveMessageFind(1);
    await moveMessageFind(1);
    await waitForFrameLayout();
    const firstBody = frame.contentDocument?.querySelector('mark.message-find-current[data-message-find-index="0"]');
    const firstBodyStyle = firstBody && frame.contentWindow?.getComputedStyle(firstBody);
    const inactive = frame.contentDocument?.querySelector('mark.message-find-match[data-message-find-index="1"]');
    const inactiveStyle = inactive && frame.contentWindow?.getComputedStyle(inactive);
    const inactiveBackground = inactiveStyle?.backgroundColor;
    if (!firstBody || status.textContent !== "3/4" || firstBodyStyle?.backgroundColor !== "rgb(255, 159, 10)"
      || inactiveBackground !== "rgb(255, 255, 0)") {
      throw new Error("native HTML finder did not activate the first body target after headers");
    }
    await moveMessageFind(1);
    await waitForFrameLayout();
    const active = frame.contentDocument?.querySelector('mark.message-find-current[data-message-find-index="1"]');
    const style = active && frame.contentWindow?.getComputedStyle(active);
    const frameBounds = frame.getBoundingClientRect();
    const paneBounds = pane.getBoundingClientRect();
    const activeBounds = active?.getBoundingClientRect();
    const activeTop = activeBounds && frameBounds.top + activeBounds.top;
    const details = {
      active: Boolean(active), shortcut_installed: shortcutInstalled, status: status.textContent, loads, background: style?.backgroundColor, color: style?.color,
      active_top: activeTop, pane_top: paneBounds.top, pane_bottom: paneBounds.bottom, pane_scroll_top: pane.scrollTop,
      frame_top: frameBounds.top, frame_height: frameBounds.height, mark_top: activeBounds?.top,
      mark_offset_top: active?.offsetTop, inactive_background: inactiveBackground,
      document_height: frame.contentDocument?.documentElement.scrollHeight, frame_scroll_y: frame.contentWindow?.scrollY,
    };
    if (!active || !shortcutInstalled || status.textContent !== "4/4" || loads !== 1 || inactiveBackground !== "rgb(255, 255, 0)"
      || style?.backgroundColor !== "rgb(255, 159, 10)" || style.color !== "rgb(0, 0, 0)"
      || activeTop == null || activeTop < paneBounds.top || activeTop > paneBounds.bottom || pane.scrollTop <= 0) {
      throw new Error(`native HTML find did not retain a visible orange body target: ${JSON.stringify(details)}`);
    }
  } finally {
    frame.remove();
    content.hidden = wasHidden;
    state.messageFindQuery = "";
    state.messageFindTargets = [];
    state.messageFindIndex = -1;
    state.view = previousView;
  }
}

function showBridgeFailure() {
  const error = byId("error");
  error.textContent = "The native application bridge did not start. Restart mailsearch-gui and check its terminal output.";
  error.hidden = false;
}

async function chooseArchive() {
  const status = await call(() => window.pywebview.api.choose_archive());
  if (status) {
    resetArchiveView();
    applyStatus(status);
    await refreshIngestOverview();
  }
}

function resetArchiveView() {
  state.searchRequest += 1;
  state.partRequest += 1;
  state.selected = null;
  state.selectionRequest = null;
  state.view = null;
  state.mailboxTree = [];
  state.searchFilters = [];
  state.highlightTerms = [];
  state.messageFindQuery = "";
  state.messageFindTargets = [];
  state.messageFindIndex = -1;
  state.messageFindRenderRequest += 1;
  state.messageFindRender = Promise.resolve(false);
  clearMessageFindUpdate();
  state.remoteContentAuthorizedMessage = null;
  state.remoteContentAuthorizedPart = null;
  state.dragExports.clear();
  state.dragPreparing.clear();
  clearResultViewport();
  showSingleMessageSelection();
  renderSearchFilters();
  closeSuggestions();
  clearAttachmentPreview();
  renderSearchHelp();
  elements["message-content"].hidden = true;
  elements["message-find"].hidden = true;
  elements["message-find-query"].value = "";
}

function applyStatus(status) {
  state.highlightBackground = status.configuration.search_highlight_background;
  document.documentElement.style.setProperty("--search-highlight-background", state.highlightBackground);
  elements["archive-label"].textContent = status.archive || "No archive selected";
  document.title = status.ready
    ? `Mail Archiver — ${status.archive} (${status.message_count.toLocaleString()} messages)`
    : "Mail Archiver";
  elements.search.disabled = !status.ready;
  elements["result-status"].textContent = status.ready ? "Enter a search." : "Choose an archive to begin.";
  if (status.ready) elements.search.focus();
}

async function refreshIngestOverview() {
  const overview = await call(() => window.pywebview.api.ingest_overview());
  if (!overview) return;
  const line = elements["ingest-status-line"];
  const status = overview.status;
  line.hidden = elements.search.disabled;
  line.className = `ingest-status-line no-print${status ? ` ${status.state}` : ""}`;
  line.dataset.statusId = status?.status_id || "";
  if (!status) {
    setIngestStatusText("No ingest history · Click to open Ingests");
    return;
  }
  const messages = Number(status.processed_messages).toLocaleString();
  const percent = Number(status.percent).toFixed(1);
  if (status.state === "running") {
    setIngestStatusText(`Ingesting ${percent}% · ${messages} messages · ${status.active_workers}/${status.configured_workers} workers · ETA ${status.eta}`);
  } else if (status.state === "completed") {
    setIngestStatusText(`Last ingest completed · ${messages} messages · ${formatElapsed(status.elapsed_seconds)} · Click for history`);
  } else {
    setIngestStatusText(`Last ingest ${status.state}: ${status.phase} · ${messages} messages · Click for details`);
  }
}

function setIngestStatusText(text) {
  state.ingestStatusText = text;
  renderBottomStatus();
}

function showLinkDestination(destination) {
  state.linkDestination = destination;
  renderBottomStatus();
}

function clearLinkDestination(destination = "") {
  if (destination && state.linkDestination !== destination) return;
  state.linkDestination = "";
  renderBottomStatus();
}

function renderBottomStatus() {
  const line = elements["ingest-status-line"];
  if (!line) return;
  const destination = state.linkDestination;
  line.textContent = destination || state.ingestStatusText;
  line.title = destination;
  line.classList.toggle("link-destination", Boolean(destination));
}

async function openIngestWindow() {
  const statusId = elements["ingest-status-line"].dataset.statusId || null;
  await call(() => window.pywebview.api.open_ingest_window(statusId));
  elements["ingest-status-line"].dataset.openedWindow = "true";
}

function formatElapsed(value) {
  const seconds = Math.max(0, Math.round(Number(value || 0)));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return [hours ? `${hours}h` : "", hours || minutes ? `${minutes}m` : "", `${remainder}s`].filter(Boolean).join(" ");
}

async function toggleMailboxTree() {
  state.showTree = elements["show-original-folders"].checked;
  elements["mailbox-browser"].hidden = !state.showTree;
  document.querySelector(".workspace").classList.toggle("tree-visible", state.showTree);
  if (state.showTree && !state.mailboxTree.length) await loadMailboxTree();
  await runSearch();
}

async function toggleSourceVolumes() {
  const nodes = flattenTree(state.mailboxTree);
  const bySelection = new Map(nodes.map(node => [node.selection, node]));
  const logical = new Set([...state.mailboxSelections].map(token => bySelection.get(token)?.logical_selection || token));
  state.showVolumes = elements["show-source-volumes"].checked;
  await loadMailboxTree();
  if (state.showVolumes) {
    state.mailboxSelections = new Set(
      flattenTree(state.mailboxTree).filter(node => logical.has(node.logical_selection)).map(node => node.selection),
    );
  } else {
    state.mailboxSelections = logical;
  }
  markCurrentSelection();
  renderMailboxTree();
  await runSearch();
}

async function loadMailboxTree() {
  const request = ++state.treeRequest;
  const showVolumes = state.showVolumes;
  const tree = await call(() => window.pywebview.api.mailbox_tree(showVolumes));
  if (!tree || request !== state.treeRequest || showVolumes !== state.showVolumes) return;
  state.mailboxTree = tree;
  renderMailboxTree();
}

function flattenTree(nodes) {
  return nodes.flatMap(node => [node, ...flattenTree(node.children)]);
}

function renderMailboxTree() {
  const parents = new Map();
  const index = new Map();
  const visit = (node, parent) => {
    index.set(node.selection, node);
    if (parent) parents.set(node.selection, parent);
    node.children.forEach(child => visit(child, node));
  };
  state.mailboxTree.forEach(node => visit(node, null));
  const covered = node => {
    for (let current = node; current; current = parents.get(current.selection)) {
      if (state.mailboxSelections.has(current.selection)) return true;
    }
    return false;
  };
  const selectedBelow = node => node.children.some(child => state.mailboxSelections.has(child.selection) || selectedBelow(child));
  const build = node => {
    const item = document.createElement("li");
    item.setAttribute("role", "treeitem");
    const line = document.createElement("div");
    line.className = "mailbox-node";
    line.dataset.label = node.label;
    line.dataset.kind = node.kind;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = covered(node);
    checkbox.indeterminate = !checkbox.checked && selectedBelow(node);
    item.setAttribute("aria-checked", checkbox.indeterminate ? "mixed" : String(checkbox.checked));
    checkbox.addEventListener("change", () => updateMailboxSelection(node, checkbox.checked, parents, index));
    const disclosure = document.createElement("button");
    disclosure.type = "button";
    disclosure.className = "tree-disclosure";
    disclosure.textContent = node.children.length ? "▾" : "";
    disclosure.disabled = !node.children.length;
    disclosure.tabIndex = -1;
    disclosure.setAttribute("aria-label", node.children.length ? `Collapse ${node.label}` : "No children");
    const label = document.createElement("span");
    label.textContent = `${node.label} (${node.count.toLocaleString()})`;
    line.append(disclosure, checkbox, label);
    item.append(line);
    if (node.children.length) {
      const children = document.createElement("ul");
      children.setAttribute("role", "group");
      children.append(...node.children.map(build));
      item.append(children);
      const expanded = value => {
        children.hidden = !value;
        item.setAttribute("aria-expanded", String(value));
        disclosure.textContent = value ? "▾" : "▸";
        disclosure.setAttribute("aria-label", `${value ? "Collapse" : "Expand"} ${node.label}`);
      };
      disclosure.addEventListener("click", () => expanded(children.hidden));
      checkbox.addEventListener("keydown", event => {
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
          event.preventDefault(); expanded(event.key === "ArrowRight");
        }
      });
    }
    return item;
  };
  const root = document.createElement("ul");
  root.className = "mailbox-tree-root";
  root.append(...state.mailboxTree.map(build));
  elements["mailbox-tree"].replaceChildren(root);
}

function updateMailboxSelection(node, checked, parents, index) {
  const descendants = candidate => {
    const found = [];
    for (const child of candidate.children) found.push(child, ...descendants(child));
    return found;
  };
  if (checked) {
    descendants(node).forEach(child => state.mailboxSelections.delete(child.selection));
    if (![...state.mailboxSelections].some(token => {
      for (let current = node; current; current = parents.get(current.selection)) {
        if (current.selection === token) return true;
      }
      return false;
    })) state.mailboxSelections.add(node.selection);
  } else if (!state.mailboxSelections.delete(node.selection)) {
    let selectedAncestor = parents.get(node.selection);
    while (selectedAncestor && !state.mailboxSelections.has(selectedAncestor.selection)) {
      selectedAncestor = parents.get(selectedAncestor.selection);
    }
    if (selectedAncestor) {
      state.mailboxSelections.delete(selectedAncestor.selection);
      for (let current = node; current !== selectedAncestor;) {
        const parent = parents.get(current.selection);
        parent.children.filter(sibling => sibling !== current).forEach(sibling => state.mailboxSelections.add(sibling.selection));
        current = parent;
      }
    }
  }
  for (const token of [...state.mailboxSelections]) if (!index.has(token)) state.mailboxSelections.delete(token);
  markCurrentSelection();
  renderMailboxTree();
  runSearch();
}

async function loadFilterSets() {
  const preferences = await call(() => window.pywebview.api.saved_filter_sets());
  if (!preferences) return;
  state.filterSets = preferences.filter_sets;
  populateFilterSetMenu();
}

function populateFilterSetMenu() {
  const option = (value, label) => {
    const item = document.createElement("option"); item.value = value; item.textContent = label; return item;
  };
  const options = [option("", "None")];
  options.push(...state.filterSets.map(item => option(item.name, item.name)));
  if (state.activeFilterSet === "__current") options.push(option("__current", "Current selection"));
  options.push(option("__save__", "Save..."));
  elements["filter-set"].replaceChildren(...options);
  elements["filter-set"].value = state.activeFilterSet;
}

function markCurrentSelection() {
  state.activeFilterSet = state.mailboxSelections.size ? "__current" : "";
  populateFilterSetMenu();
}

async function selectFilterSet() {
  const selected = elements["filter-set"].value;
  if (selected === "__save__") {
    elements["filter-set"].value = state.activeFilterSet;
    elements["filter-set-name"].value = "";
    elements["save-filter-dialog"].showModal();
    elements["filter-set-name"].focus();
    return;
  }
  if (!selected) {
    state.mailboxSelections.clear();
    state.activeFilterSet = "";
    renderMailboxTree();
    await runSearch();
    return;
  }
  if (selected === "__current") return;
  const filterSet = state.filterSets.find(item => item.name === selected);
  if (!filterSet) return;
  state.showVolumes = filterSet.show_volumes;
  elements["show-source-volumes"].checked = state.showVolumes;
  state.mailboxSelections = new Set(filterSet.selections);
  state.activeFilterSet = filterSet.name;
  await loadMailboxTree();
  populateFilterSetMenu();
  await runSearch();
}

async function saveFilterSet(event) {
  event.preventDefault();
  const name = elements["filter-set-name"].value.trim();
  const preferences = await call(() => window.pywebview.api.save_filter_set(
    name, state.showVolumes, [...state.mailboxSelections],
  ));
  if (!preferences) return;
  state.filterSets = preferences.filter_sets;
  state.activeFilterSet = name;
  elements["save-filter-dialog"].close();
  populateFilterSetMenu();
}

async function openFilterManager() {
  await loadFilterSets();
  const rows = state.filterSets.map(item => {
    let currentName = item.name;
    const row = document.createElement("div"); row.className = "filter-set-row";
    const input = document.createElement("input"); input.value = item.name; input.setAttribute("aria-label", `Rename ${item.name}`);
    const rename = actionButton("Rename", async () => {
      const preferences = await call(() => window.pywebview.api.rename_filter_set(currentName, input.value.trim()));
      if (!preferences) return;
      currentName = input.value.trim();
      state.filterSets = preferences.filter_sets;
      if (state.activeFilterSet === item.name) state.activeFilterSet = currentName;
      populateFilterSetMenu();
    });
    const remove = actionButton("Delete", async () => {
      const preferences = await call(() => window.pywebview.api.delete_filter_set(currentName));
      if (!preferences) return;
      state.filterSets = preferences.filter_sets;
      if (state.activeFilterSet === currentName) state.activeFilterSet = "__current";
      row.remove();
      populateFilterSetMenu();
    });
    row.append(input, rename, remove);
    return row;
  });
  elements["filter-set-list"].replaceChildren(...rows);
  elements["manage-filter-dialog"].showModal();
}

function quotedSearchValue(value) {
  return `"${value.replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`;
}

function effectiveQuery() {
  const filters = state.searchFilters.map(filter => {
    const selector = filter.kind === "address" ? filter.role : "subject";
    return `${selector}:${quotedSearchValue(filter.value)}`;
  });
  const text = elements.search.value.trim();
  if (text) filters.push(text);
  return filters.join(" ");
}

function scheduleSuggestions() {
  window.clearTimeout(state.suggestionTimer);
  const query = elements.search.value.trim();
  if (query.length < SUGGESTION_MINIMUM) { closeSuggestions(); return; }
  const request = ++state.suggestionRequest;
  state.suggestionTimer = window.setTimeout(() => loadSuggestions(query, request), SUGGESTION_DELAY_MS);
}

async function loadSuggestions(query, request) {
  const suggestions = await call(() => window.pywebview.api.suggestions(query, SUGGESTION_LIMIT));
  if (!suggestions || request !== state.suggestionRequest || elements.search.value.trim() !== query) return;
  renderSuggestions(suggestions);
}

function renderSuggestions(suggestions) {
  state.suggestionItems = [];
  state.suggestionIndex = -1;
  const contents = [];
  const heading = label => {
    const item = document.createElement("div"); item.className = "suggestion-heading"; item.textContent = label; return item;
  };
  const option = (icon, label, count, accept) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion-option";
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", "false");
    const iconNode = document.createElement("span"); iconNode.className = "suggestion-icon"; iconNode.textContent = icon;
    const labelNode = document.createElement("span"); labelNode.className = "suggestion-label"; labelNode.textContent = label;
    const countNode = document.createElement("span"); countNode.className = "suggestion-count";
    countNode.textContent = count === null ? "" : count.toLocaleString();
    if (count !== null) countNode.title = `${count.toLocaleString()} message${count === 1 ? "" : "s"}`;
    button.append(iconNode, labelNode, countNode);
    const index = state.suggestionItems.length;
    button.addEventListener("mouseenter", () => selectSuggestion(index));
    button.addEventListener("mousedown", event => { event.preventDefault(); accept(); });
    state.suggestionItems.push({element: button, accept});
    return button;
  };
  if (suggestions.addresses.length) {
    contents.push(heading("Addresses"));
    for (const address of suggestions.addresses) {
      const label = address.display_name ? `${address.display_name} — ${address.address}` : address.address;
      contents.push(option("◎", label, address.message_count, () => addAddressFilter(address)));
    }
  }
  contents.push(heading("Subjects"));
  contents.push(option("✉", `Subject contains “${suggestions.query}”`, null, () => addSubjectFilter(suggestions.query)));
  for (const subject of suggestions.subjects) {
    contents.push(option("✉", subject.subject, subject.message_count, () => addSubjectFilter(subject.subject)));
  }
  elements["search-suggestions"].replaceChildren(...contents);
  elements["search-suggestions"].hidden = false;
  elements.search.setAttribute("aria-expanded", "true");
}

function selectSuggestion(index) {
  if (!state.suggestionItems.length) return;
  state.suggestionIndex = Math.max(0, Math.min(index, state.suggestionItems.length - 1));
  state.suggestionItems.forEach((item, itemIndex) => {
    const active = itemIndex === state.suggestionIndex;
    item.element.classList.toggle("active", active);
    item.element.setAttribute("aria-selected", String(active));
    if (active) item.element.scrollIntoView({block: "nearest"});
  });
}

function navigateSuggestions(event) {
  if (event.key === "Backspace" && !elements.search.value && state.searchFilters.length) {
    state.searchFilters.pop(); renderSearchFilters(); runSearch(); return;
  }
  if (elements["search-suggestions"].hidden) return;
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    const delta = event.key === "ArrowDown" ? 1 : -1;
    selectSuggestion(state.suggestionIndex < 0 ? (delta > 0 ? 0 : state.suggestionItems.length - 1) : state.suggestionIndex + delta);
  } else if (event.key === "Enter" && state.suggestionIndex >= 0) {
    event.preventDefault(); acceptSuggestion(state.suggestionIndex);
  } else if (event.key === "Escape") {
    event.preventDefault(); closeSuggestions();
  }
}

function acceptSuggestion(index) {
  state.suggestionItems[index]?.accept();
}

function closeSuggestions() {
  window.clearTimeout(state.suggestionTimer);
  state.suggestionRequest += 1;
  state.suggestionItems = [];
  state.suggestionIndex = -1;
  if (elements["search-suggestions"]) {
    elements["search-suggestions"].hidden = true;
    elements["search-suggestions"].replaceChildren();
  }
  elements.search?.setAttribute("aria-expanded", "false");
}

function addAddressFilter(suggestion) {
  state.searchFilters.push({
    kind: "address", value: suggestion.address, label: suggestion.display_name || suggestion.address, role: "any",
  });
  elements.search.value = "";
  closeSuggestions();
  renderSearchFilters();
  elements.search.focus();
  runSearch();
}

function addSubjectFilter(subject) {
  state.searchFilters.push({kind: "subject", value: subject, label: subject});
  elements.search.value = "";
  closeSuggestions();
  renderSearchFilters();
  elements.search.focus();
  runSearch();
}

function renderSearchFilters() {
  const roleOptions = [["any", "Any"], ["from", "From"], ["to", "To"], ["cc", "Cc"], ["bcc", "Bcc"]];
  const chips = state.searchFilters.map((filter, index) => {
    const chip = document.createElement("span"); chip.className = "search-chip";
    if (filter.kind === "address") {
      const role = document.createElement("select");
      role.setAttribute("aria-label", `Address role for ${filter.value}`);
      role.append(...roleOptions.map(([value, label]) => {
        const option = document.createElement("option"); option.value = value; option.textContent = label; return option;
      }));
      role.value = filter.role;
      role.addEventListener("change", () => { filter.role = role.value; runSearch(); });
      chip.append(role);
    }
    const label = document.createElement("span");
    label.className = "search-chip-label";
    label.textContent = filter.kind === "subject" ? `Subject: ${filter.label}` : filter.label;
    label.title = filter.value;
    const remove = document.createElement("button");
    remove.type = "button"; remove.className = "search-chip-remove"; remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove ${filter.label} filter`);
    remove.addEventListener("click", () => {
      state.searchFilters.splice(index, 1); renderSearchFilters(); runSearch(); elements.search.focus();
    });
    chip.append(label, remove);
    return chip;
  });
  elements["search-filters"]?.replaceChildren(...chips);
}

async function runSearch() {
  const query = effectiveQuery();
  const sortBy = elements["sort-by"].value;
  const sortDirection = state.sortDirection;
  const searchAttachments = elements["search-attachments"].checked;
  const request = ++state.searchRequest;
  clearCurrentMessageFind();
  clearLinkDestination();
  state.partRequest += 1;
  state.selectionRequest = null;
  state.selected = null;
  state.view = null;
  state.remoteContentAuthorizedMessage = null;
  state.remoteContentAuthorizedPart = null;
  state.highlightTerms = [];
  state.messageFindQuery = "";
  invalidateMessageFindTargets();
  clearMessageFindUpdate();
  state.commandAContext = "";
  clearResultViewport();
  elements["message-find"].hidden = true;
  elements["message-find-query"].value = "";
  elements["message-content"].hidden = true;
  elements["result-status"].classList.remove("background-search");
  const mailboxSelections = state.showTree ? [...state.mailboxSelections] : [];
  if (!query.trim() && !mailboxSelections.length) {
    Object.assign(state, {query, sortBy, sortDirection, searchAttachments, offset: 0});
    renderSearchHelp();
    elements["result-status"].textContent = "Enter a search.";
    return;
  }
  elements["result-status"].textContent = "Searching…";
  await runCompleteSearch({query, sortBy, sortDirection, searchAttachments, mailboxSelections, request});
}

async function runCompleteSearch(context) {
  const {query, sortBy, sortDirection, searchAttachments, mailboxSelections, request} = context;
  Object.assign(state, {query, sortBy, sortDirection, searchAttachments, offset: 0});
  clearResultViewport();
  const first = await call(() => window.pywebview.api.search(
    query, 0, sortBy, sortDirection, searchAttachments, mailboxSelections, RESULT_INITIAL_LIMIT,
  ));
  if (request !== state.searchRequest) return;
  if (!first) { updateResultStatus(); return; }
  state.highlightTerms = first.highlight_terms;
  appendResults(first.results, request);
  state.offset = first.results.length;
  if (!first.has_more) { updateResultStatus(); return; }
  elements["result-status"].classList.add("background-search");
  elements["result-status"].textContent = `Searching in background… ${state.offset.toLocaleString()} messages shown`;
  const remainder = await call(() => window.pywebview.api.search(
    query, state.offset, sortBy, sortDirection, searchAttachments, mailboxSelections, 0,
  ));
  if (request !== state.searchRequest) return;
  elements["result-status"].classList.remove("background-search");
  if (!remainder) {
    elements["result-status"].textContent = `${state.offset.toLocaleString()} messages shown; background search failed`;
    return;
  }
  appendResults(remainder.results, request);
  state.offset += remainder.results.length;
  updateResultStatus();
}

function renderSearchHelp() {
  clearResultViewport();
  elements["result-list"].hidden = true;
  elements["result-help"].hidden = false;
  elements["result-help"].replaceChildren(elements["search-help-template"].content.cloneNode(true));
}

function appendResults(results, request) {
  if (request !== state.searchRequest) return;
  state.results.push(...results);
  showResultTable();
  void state.resultTable.replaceData(state.results);
}

function clearResultViewport() {
  state.results = [];
  state.resultPreviews.clear();
  state.resultPreviewPending.clear();
  state.resultPreviewBatch.clear();
  if (state.resultPreviewTimer !== null) window.clearTimeout(state.resultPreviewTimer);
  state.resultPreviewTimer = null;
  state.resultSelection.clear();
  state.resultRangeAnchor = null;
  state.resultRangeSelection = [];
  state.resultRangeActive = false;
  state.suppressResultClick = false;
  state.resultTable?.clearData();
}

function toggleSortDirection() {
  state.sortDirection = state.sortDirection === "descending" ? "ascending" : "descending";
  const descending = state.sortDirection === "descending";
  elements["sort-direction"].textContent = descending ? "↓" : "↑";
  elements["sort-direction"].ariaLabel = descending ? "Sort descending" : "Sort ascending";
  elements["sort-direction"].title = elements["sort-direction"].ariaLabel;
  runSearch();
}

function showResultTable() {
  elements["result-help"].hidden = true;
  elements["result-help"].replaceChildren();
  elements["result-list"].hidden = false;
}

function initializeResultTable() {
  if (!window.Tabulator) throw new Error("The bundled Tabulator result table did not load.");
  state.resultTable = new window.Tabulator(elements["result-list"], {
    data: [],
    index: "message_pk",
    height: "100%",
    layout: "fitColumns",
    headerVisible: false,
    renderVertical: "virtual",
    selectableRows: true,
    selectableRowsRangeMode: "click",
    placeholder: "No messages.",
    columns: [{
      field: "subject",
      formatter: resultCardFormatter,
      headerSort: false,
      widthGrow: 1,
    }],
  });
  state.resultTable.on("rowClick", selectResultRow);
  state.resultTable.on("rowDblClick", openResultWindow);
  state.resultTable.on("rowMouseDown", beginResultRange);
  state.resultTable.on("rowMouseEnter", extendResultRange);
  state.resultTable.on("rowSelectionChanged", selected => {
    state.resultSelection = new Set(selected.map(result => result.message_pk));
    updateMessageFileWell();
    if (state.resultSelection.size > 1) showMultipleMessageSelection();
    else if (state.resultSelection.size === 1 && state.selected === null && state.selectionRequest === null) {
      void selectMessage(selected[0].message_pk);
    }
  });
}

function resultCardFormatter(cell) {
  const result = cell.getRow().getData();
  queueResultPreview(result.message_pk, state.searchRequest);
  const card = document.createElement("div");
  card.className = "result";
  card.id = `message-result-${result.message_pk}`;
  card.dataset.messagePk = result.message_pk;
  card.dataset.dateUtc = result.date_utc;
  const subjectLine = document.createElement("div");
  subjectLine.className = "result-subject-line";
  const subject = document.createElement("div");
  subject.className = "result-subject";
  subject.textContent = result.subject || "(no subject)";
  const paperclip = document.createElement("span");
  paperclip.className = "result-attachment";
  paperclip.textContent = result.attachment_count > 1 ? `📎 ${result.attachment_count}` : "📎";
  paperclip.title = `${result.attachment_count} attachment${result.attachment_count === 1 ? "" : "s"}`;
  paperclip.setAttribute("aria-label", paperclip.title);
  paperclip.hidden = result.attachment_count === 0;
  subjectLine.append(subject, paperclip);
  const line = document.createElement("div");
  line.className = "result-line";
  const sender = document.createElement("span");
  sender.className = "result-sender";
  sender.textContent = result.sender || "(missing sender)";
  const date = document.createElement("span");
  date.className = "result-date";
  date.textContent = formatDate(result.date_utc);
  line.append(sender, date);
  const preview = document.createElement("div");
  preview.className = "result-preview";
  preview.setAttribute("aria-label", "Message preview");
  preview.textContent = state.resultPreviews.get(result.message_pk) || "";
  card.append(subjectLine, line, preview);
  return card;
}

function queueResultPreview(messagePk, request) {
  if (state.resultPreviews.has(messagePk) || state.resultPreviewPending.has(messagePk)) return;
  state.resultPreviewPending.add(messagePk);
  state.resultPreviewBatch.add(messagePk);
  if (state.resultPreviewTimer !== null) return;
  state.resultPreviewTimer = window.setTimeout(() => {
    state.resultPreviewTimer = null;
    const messagePks = [...state.resultPreviewBatch];
    state.resultPreviewBatch.clear();
    queuePreviews(messagePks, request);
  }, 0);
}

function updateResultStatus() {
  const count = state.offset.toLocaleString();
  elements["result-status"].textContent = `${count} message${state.offset === 1 ? "" : "s"}`;
}

function toggleSortDirection() {
  state.sortDirection = state.sortDirection === "descending" ? "ascending" : "descending";
  const descending = state.sortDirection === "descending";
  elements["sort-direction"].textContent = descending ? "↓" : "↑";
  elements["sort-direction"].ariaLabel = descending ? "Sort descending" : "Sort ascending";
  elements["sort-direction"].title = elements["sort-direction"].ariaLabel;
  runSearch(false);
}

function beginResultRange(event, row) {
  if (event.button !== 0) return;
  state.resultRangeAnchor = row.getData().message_pk;
  state.resultRangeSelection = [];
  state.resultRangeActive = false;
}

function extendResultRange(event, row) {
  if (state.resultRangeAnchor === null || event.buttons !== 1) return;
  const messagePk = row.getData().message_pk;
  if (messagePk === state.resultRangeAnchor) return;
  const first = state.results.findIndex(result => result.message_pk === state.resultRangeAnchor);
  const last = state.results.findIndex(result => result.message_pk === messagePk);
  if (first < 0 || last < 0) return;
  state.resultRangeActive = true;
  state.resultRangeSelection = state.results.slice(Math.min(first, last), Math.max(first, last) + 1)
    .map(result => result.message_pk);
  state.resultTable?.deselectRow();
  state.resultTable?.selectRow(state.resultRangeSelection);
}

function finishResultRange() {
  if (state.resultRangeAnchor === null) return;
  if (state.resultRangeActive) {
    state.suppressResultClick = true;
    requestAnimationFrame(() => { state.suppressResultClick = false; });
  }
  state.resultRangeAnchor = null;
  state.resultRangeActive = false;
}

function selectResultRow(event, row) {
  if (state.suppressResultClick) {
    state.resultTable?.deselectRow();
    state.resultTable?.selectRow(state.resultRangeSelection);
    state.suppressResultClick = false;
    return;
  }
  window.setTimeout(() => {
    let selected = state.resultTable?.getSelectedRows() || [];
    if (!event.shiftKey && !event.metaKey && !event.ctrlKey && selected.length !== 1) {
      state.resultTable?.deselectRow();
      state.resultTable?.selectRow(row);
      selected = state.resultTable?.getSelectedRows() || [];
    }
    if (selected.length !== 1 || selected[0] !== row || state.selectionRequest === row.getData().message_pk) return;
    void selectMessage(row.getData().message_pk);
  }, 0);
}

async function openResultWindow(_event, row) {
  await call(() => window.pywebview.api.open_message_window(row.getData().message_pk, state.highlightTerms));
  row.getElement().querySelector(".result")?.setAttribute("data-opened-window", "true");
}

async function requestPreviews(messagePks, request) {
  if (!messagePks.length) return;
  const requested = await call(() => window.pywebview.api.request_previews(messagePks));
  if (request !== state.searchRequest) return;
  if (requested === null) {
    messagePks.forEach(messagePk => state.resultPreviewPending.delete(messagePk));
    return;
  }
  while (true) {
    const batch = await call(() => window.pywebview.api.take_previews(messagePks));
    if (request !== state.searchRequest) return;
    if (!batch) {
      messagePks.forEach(messagePk => state.resultPreviewPending.delete(messagePk));
      return;
    }
    for (const item of batch.previews) {
      state.resultPreviews.set(item.message_pk, item.preview);
      state.resultPreviewPending.delete(item.message_pk);
      state.resultTable?.getRow(item.message_pk)?.reformat();
    }
    if (batch.error) { showError(`Could not load message previews: ${batch.error}`); return; }
    if (!batch.pending) {
      messagePks.forEach(messagePk => state.resultPreviewPending.delete(messagePk));
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 75));
  }
}

function queuePreviews(messagePks, request) {
  previewQueue = previewQueue.then(() => {
    if (request !== state.searchRequest) return undefined;
    return requestPreviews(messagePks, request);
  }).catch(error => showError(`Could not load message previews: ${error?.message || error}`));
}

async function selectMessage(messagePk) {
  showSingleMessageSelection();
  clearLinkDestination();
  state.selectionRequest = messagePk;
  state.partRequest += 1;
  state.remoteContentAuthorizedMessage = null;
  state.remoteContentAuthorizedPart = null;
  clearCurrentMessageFind();
  clearMessageFindUpdate();
  state.messageFindQuery = elements["message-find"].hidden ? "" : elements["message-find-query"].value.trim();
  invalidateMessageFindTargets();
  const view = await call(() => window.pywebview.api.message(messagePk));
  if (!view || state.selectionRequest !== messagePk) return;
  state.selected = messagePk;
  state.view = view;
  state.messageFindIndex = -1;
  state.resultTable?.deselectRow();
  state.resultTable?.selectRow(messagePk);
  updateMessageFileWell();
  void state.resultTable?.scrollToRow(messagePk, "middle", false);
  elements["message-content"].hidden = false;
  const adjustment = view.date_adjustment;
  const banner = elements["computed-date-banner"];
  banner.hidden = !adjustment;
  banner.textContent = adjustment
    ? `Date adjusted: The date in the Date: header (${adjustment.date_header}) is more than two days from ` +
      `the median date of the Received: headers (${adjustment.received_median_utc}). ` +
      `Archive routing uses the computed UTC date (${adjustment.archive_routing_utc}).`
    : "";
  elements["message-well"].classList.toggle("computed-date", Boolean(adjustment));
  setHighlightedText(elements["message-subject"], view.subject);
  updateMessageFileWell();
  renderMessageHeaders(view);
  elements["part-select"].replaceChildren(...view.body_parts.map(partOption));
  elements["part-select"].value = String(view.preferred_part_id);
  renderAttachments(view.attachments);
  renderLocations(view);
  const displayed = await showPart(view.preferred_part_id, false);
  if (state.selectionRequest !== messagePk || state.selected !== messagePk || !displayed) return;
  if (state.messageFindQuery) await moveMessageFind(1);
}

function renderLocations(view) {
  const nodes = [];
  const addLocation = (label, value, copyIndex = null) => {
    const term = document.createElement("dt"); term.textContent = `${label}:`;
    const detail = document.createElement("dd");
    const text = document.createElement("span"); text.textContent = value;
    detail.append(text);
    if (copyIndex !== null) {
      const copy = document.createElement("button");
      copy.className = "copy-source-path";
      copy.type = "button";
      copy.textContent = "⧉";
      copy.title = "Copy source path";
      copy.setAttribute("aria-label", "Copy source path");
      copy.addEventListener("click", () => copySourcePath(copyIndex));
      detail.append(copy);
    }
    nodes.push(term, detail);
  };
  if (view.archive_path) addLocation("Archive mailbox", view.archive_path);
  view.source_locations.forEach((source, index) => {
    const origin = source.preferred ? `Preferred source (${source.origin})` : source.origin;
    addLocation(origin, source.volume);
    addLocation("Source path", source.offset ? `${source.path}?offset=${source.offset}` : source.path,
      source.copy_path ? index : null);
  });
  elements["provenance-section"].hidden = nodes.length === 0;
  elements["message-locations"].replaceChildren(...nodes);
}

async function copySourcePath(sourceLocationIndex) {
  await call(() => window.pywebview.api.copy_source_path(state.selected, sourceLocationIndex));
}

function navigateResults(event) {
  if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
  if (!state.results.length) return;
  event.preventDefault();
  let index = state.results.findIndex(result => result.message_pk === state.selected);
  if (event.key === "ArrowDown") index = Math.min(index + 1, state.results.length - 1);
  else index = index < 0 ? state.results.length - 1 : Math.max(index - 1, 0);
  void selectMessage(state.results[index].message_pk);
}

function handleCommandShortcut(event) {
  if (!event.metaKey || event.altKey) return;
  if (isTextInput(event.target) && event.target !== elements["message-find-query"]) return;
  if (event.key.toLowerCase() === "a" && !isTextInput(event.target)) {
    if (state.commandAContext === "results") {
      event.preventDefault();
      selectAllResults();
      return;
    }
    if (state.commandAContext === "message" && state.view) {
      event.preventDefault();
      selectMessageText();
      return;
    }
  }
  if (!state.view) return;
  if (event.key.toLowerCase() === "f") {
    event.preventDefault();
    void openMessageFind();
    return;
  }
  if (event.key.toLowerCase() === "g") {
    event.preventDefault();
    if (elements["message-find"].hidden) void openMessageFind();
    else void moveMessageFind(event.shiftKey ? -1 : 1);
    return;
  }
  let partId = null;
  if (event.key === "0" || (event.shiftKey && event.key.toLowerCase() === "u")) partId = -1;
  else if (!event.shiftKey && /^[1-9]$/.test(event.key)) partId = Number(event.key);
  if (partId === null) return;
  event.preventDefault();
  const part = state.view.body_parts.find(candidate => candidate.part_id === partId);
  if (!part) { showError(`This message has no displayable MIME part ${partId}.`); return; }
  elements["part-select"].value = String(partId);
  void selectMessagePart(partId, false);
}

function isTextInput(target) {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement || target.closest?.("[contenteditable]");
}

function selectAllResults() {
  state.resultTable?.selectRow();
}

function selectMessageText() {
  const range = document.createRange();
  range.selectNodeContents(elements["body-view"]);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

function displayedHeaderText() {
  const headers = elements["message-headers"];
  const fields = [...headers.querySelectorAll("dt")].map((label, index) =>
    `${label.innerText} ${headers.querySelectorAll("dd")[index].innerText}`);
  return [elements["message-subject"].innerText, ...fields].join("\n");
}

function copyVisibleMessageText() {
  const frame = elements["body-view"].querySelector(".html-frame");
  const body = frame ? frame.contentDocument?.body.innerText : elements["body-view"].innerText;
  const text = frame ? `${displayedHeaderText()}\n\n${body || ""}` : body || "";
  if (text) void call(() => window.pywebview.api.copy_visible_text(text));
}

function renderMessageHeaders(view) {
  elements["message-headers"].replaceChildren(...headerNodes(view.headers));
}

function initialMessageFindQuery() {
  return state.highlightTerms[0] || "";
}

async function openMessageFind() {
  elements["message-find"].hidden = false;
  const selectionRequest = state.selectionRequest;
  let query = state.messageFindQuery;
  let renderRequest = state.messageFindRenderRequest;
  if (!state.messageFindQuery) {
    state.messageFindQuery = initialMessageFindQuery();
    elements["message-find-query"].value = state.messageFindQuery;
    query = state.messageFindQuery;
    if (query) {
      const displayed = await renderMessageFindHighlights();
      renderRequest = state.messageFindRenderRequest;
      if (!displayed || state.selectionRequest !== selectionRequest || state.messageFindQuery !== query ||
        elements["message-find-query"].value.trim() !== query) return;
    }
  }
  if (state.selectionRequest !== selectionRequest || state.messageFindQuery !== query ||
    state.messageFindRenderRequest !== renderRequest || elements["message-find-query"].value.trim() !== query) return;
  elements["message-find-query"].focus();
  elements["message-find-query"].select();
  if (state.messageFindQuery) await moveMessageFind(1);
}

async function closeMessageFind() {
  clearMessageFindUpdate();
  clearCurrentMessageFind();
  elements["message-find"].hidden = true;
  state.messageFindQuery = "";
  elements["message-find-query"].value = "";
  invalidateMessageFindTargets();
  await renderMessageFindHighlights();
}

function scheduleMessageFindUpdate() {
  clearMessageFindUpdate();
  state.messageFindTimer = window.setTimeout(() => {
    state.messageFindTimer = null;
    void updateMessageFind();
  }, MESSAGE_FIND_DELAY_MS);
}

function clearMessageFindUpdate() {
  if (state.messageFindTimer !== null) window.clearTimeout(state.messageFindTimer);
  state.messageFindTimer = null;
}

async function flushMessageFindUpdate() {
  if (state.messageFindTimer === null) return false;
  clearMessageFindUpdate();
  await updateMessageFind();
  return true;
}

async function updateMessageFind() {
  state.messageFindQuery = elements["message-find-query"].value.trim();
  clearCurrentMessageFind();
  invalidateMessageFindTargets();
  const displayed = await renderMessageFindHighlights();
  if (displayed && state.messageFindQuery) await moveMessageFind(1);
}

async function renderMessageFindHighlights() {
  if (!state.view || state.selectionRequest !== state.selected) return false;
  const request = ++state.messageFindRenderRequest;
  const render = (async () => {
    renderMessageHeaders(state.view);
    const partId = Number(elements["part-select"].value);
    const allowRemote = hasRemoteContentAuthorization(state.selected, partId);
    const displayed = await showPart(partId, allowRemote);
    return displayed && request === state.messageFindRenderRequest;
  })();
  state.messageFindRender = render;
  return render;
}

async function waitForMessageFindRender() {
  let render;
  do {
    render = state.messageFindRender;
    await render;
  } while (render !== state.messageFindRender);
}

async function moveMessageFind(direction, retried = false) {
  if (await flushMessageFindUpdate()) return;
  if (!state.view || state.selectionRequest !== state.selected) {
    updateMessageFindStatus();
    return;
  }
  const messagePk = state.selected;
  const selectionRequest = state.selectionRequest;
  let query = state.messageFindQuery;
  if (!state.messageFindQuery) {
    state.messageFindQuery = initialMessageFindQuery();
    elements["message-find-query"].value = state.messageFindQuery;
    if (!state.messageFindQuery) {
      updateMessageFindStatus();
      return;
    }
    await renderMessageFindHighlights();
    if (!state.view || state.selected !== messagePk || state.selectionRequest !== selectionRequest) return;
    query = state.messageFindQuery;
  }
  const renderRequest = state.messageFindRenderRequest;
  await waitForMessageFindRender();
  if (!state.view || state.selected !== messagePk || state.selectionRequest !== selectionRequest ||
    state.messageFindQuery !== query || state.messageFindRenderRequest !== renderRequest ||
    elements["message-find-query"].value.trim() !== query) return;
  const targets = state.messageFindTargets;
  if (!targets.length) {
    updateMessageFindStatus();
    return;
  }
  const nextIndex = state.messageFindIndex < 0
    ? (direction < 0 ? targets.length - 1 : 0)
    : (state.messageFindIndex + direction + targets.length) % targets.length;
  const current = targets[nextIndex];
  const stale = !current.element.isConnected || (current.frame && (
    !isCurrentHtmlFrame(current.frame)
    || current.element.ownerDocument !== current.frame.contentDocument
    || current.element.dataset.mailarchiverFindTarget !== current.frame.dataset.messageFindToken
  ));
  if (stale) {
    state.messageFindIndex = -1;
    updateMessageFindTargets();
    if (!retried && state.messageFindTargets.length) await moveMessageFind(direction, true);
    return;
  }
  state.messageFindIndex = nextIndex;
  clearCurrentMessageFind();
  if (current.frame) {
    if (!selectHtmlFindTarget(current.frame, current.element)) {
      state.messageFindIndex = -1;
      updateMessageFindStatus();
      return;
    }
  } else {
    current.element.classList.add("message-find-current");
    current.element.scrollIntoView({block: "center", inline: "nearest"});
  }
  updateMessageFindStatus();
}

function clearCurrentMessageFind() {
  elements["message-well"].querySelectorAll('[data-mailarchiver-find-target="outer"].message-find-current')
    .forEach(mark => mark.classList.remove("message-find-current"));
  const frame = elements["body-view"].querySelector(".html-frame");
  const token = frame?.dataset.messageFindToken;
  frame?.contentDocument?.querySelectorAll(".message-find-current").forEach(mark => {
    if (mark.dataset.mailarchiverFindTarget !== token) return;
    mark.classList.remove("message-find-current");
    mark.style.removeProperty("background");
    mark.style.removeProperty("color");
    mark.style.removeProperty("outline");
    mark.style.removeProperty("box-shadow");
    mark.style.removeProperty("transition");
    mark.style.removeProperty("animation");
  });
}

function isCurrentHtmlFrame(frame) {
  return frame.isConnected && elements["body-view"].querySelector(".html-frame") === frame;
}

function selectHtmlFindTarget(frame, mark) {
  if (!isCurrentHtmlFrame(frame) || !mark.isConnected || mark.ownerDocument !== frame.contentDocument
    || mark.dataset.mailarchiverFindTarget !== frame.dataset.messageFindToken) return false;
  mark.classList.add("message-find-current");
  mark.style.setProperty("background", "#ff9f0a", "important");
  mark.style.setProperty("color", "#000", "important");
  mark.style.setProperty("outline", "2px solid #b54a00", "important");
  mark.style.setProperty("box-shadow", "inset 0 0 0 1px #fff", "important");
  mark.style.setProperty("transition", "none", "important");
  mark.style.setProperty("animation", "none", "important");
  scrollHtmlFindTarget(frame, mark);
  return true;
}

function scrollHtmlFindTarget(frame, mark) {
  const scroll = () => {
    if (!isCurrentHtmlFrame(frame) || !mark.classList.contains("message-find-current")) return;
    resizeFrame(frame);
    const pane = elements["message-pane"];
    const frameBounds = frame.getBoundingClientRect();
    const paneBounds = pane.getBoundingClientRect();
    const markBounds = mark.getBoundingClientRect();
    pane.scrollTo({top: pane.scrollTop + frameBounds.top - paneBounds.top + markBounds.top - pane.clientHeight / 2});
  };
  scroll();
  window.setTimeout(scroll, 50);
  requestAnimationFrame(() => requestAnimationFrame(scroll));
}

function currentHtmlFindTarget(frame) {
  const token = frame.dataset.messageFindToken;
  return [...(frame.contentDocument?.querySelectorAll("mark.message-find-current") || [])]
    .find(mark => mark.dataset.mailarchiverFindTarget === token) || null;
}

function refreshHtmlFrameLayout(frame) {
  if (!isCurrentHtmlFrame(frame)) return;
  resizeFrame(frame);
  const current = currentHtmlFindTarget(frame);
  if (current) scrollHtmlFindTarget(frame, current);
}

function installFrameLayoutUpdates(frame) {
  const document = frame.contentDocument;
  if (!document) return;
  const refresh = () => refreshHtmlFrameLayout(frame);
  document.addEventListener("load", refresh, true);
  document.addEventListener("error", refresh, true);
  document.fonts?.ready.then(refresh).catch(() => undefined);
  frame.addEventListener("load", refresh, {once: true});
}

function updateMessageFindTargets() {
  const targets = [...elements["message-well"].querySelectorAll('[data-mailarchiver-find-target="outer"]')]
    .map(element => ({element}));
  const frame = elements["body-view"].querySelector(".html-frame");
  const token = frame?.dataset.messageFindToken;
  if (frame?.contentDocument && token) {
    for (const element of frame.contentDocument.querySelectorAll("mark.message-find-match")) {
      if (element.dataset.mailarchiverFindTarget === token) targets.push({frame, element});
    }
  }
  state.messageFindTargets = targets;
  if (state.messageFindIndex >= targets.length) state.messageFindIndex = -1;
  updateMessageFindStatus();
}

function invalidateMessageFindTargets() {
  state.messageFindTargets = [];
  state.messageFindIndex = -1;
  state.messageFindRenderRequest += 1;
  state.messageFindRender = Promise.resolve(false);
  updateMessageFindStatus();
}

function hasRemoteContentAuthorization(messagePk, partId) {
  return messagePk !== null && state.remoteContentAuthorizedMessage === messagePk
    && state.remoteContentAuthorizedPart === partId;
}

function updateMessageFindStatus() {
  const count = state.messageFindTargets.length;
  elements["message-find-status"].textContent = !state.messageFindQuery ? "" :
    (count ? `${state.messageFindIndex + 1 || 0}/${count}` : "0/0");
}

function headerNodes(headers) {
  const important = new Set(["from", "to", "cc", "subject", "date"]);
  return headers.filter(header => important.has(header.name.toLowerCase())).flatMap(header => {
    const term = document.createElement("dt"); term.textContent = `${header.name}:`;
    const value = document.createElement("dd"); setHighlightedText(value, header.value);
    return [term, value];
  });
}

function partOption(part) {
  const option = document.createElement("option");
  option.value = part.part_id;
  option.textContent = part.label;
  return option;
}

async function showPart(partId, allowRemote) {
  if (!state.selected || state.selectionRequest !== state.selected) return false;
  const messagePk = state.selected;
  const request = ++state.partRequest;
  const part = await call(() => window.pywebview.api.part(messagePk, partId, allowRemote));
  if (!part || request !== state.partRequest || messagePk !== state.selected) return false;
  clearLinkDestination();
  elements["body-view"].replaceChildren();
  elements["remote-content"].hidden = !part.remote_content_blocked;
  if (part.kind === "html") {
    const frame = document.createElement("iframe");
    frame.className = "html-frame";
    frame.setAttribute("sandbox", "allow-same-origin");
    const highlighted = highlightedHtml(part.content);
    frame.srcdoc = highlighted.content;
    frame.dataset.highlightCount = String(highlighted.count);
    frame.dataset.messageFindCount = String(highlighted.findCount);
    frame.dataset.messageFindToken = highlighted.marker;
    elements["body-view"].append(frame);
    try {
      await waitForFrameDocument(frame);
    } catch (error) {
      if (request === state.partRequest && messagePk === state.selected && isCurrentHtmlFrame(frame)) {
        frame.remove();
        updateMessageFindTargets();
        showError(String(error?.message || error));
      }
      return false;
    }
    if (request !== state.partRequest || messagePk !== state.selected || !isCurrentHtmlFrame(frame)) return false;
    installFrameFindShortcuts(frame);
    installFrameLinkHandlers(frame);
    resizeFrame(frame);
    installFrameLayoutUpdates(frame);
  } else {
    const body = document.createElement("div");
    body.className = part.kind === "raw" ? "raw" : "plain";
    setHighlightedText(body, part.content);
    elements["body-view"].append(body);
  }
  if (request !== state.partRequest || messagePk !== state.selected) return false;
  updateMessageFindTargets();
  return true;
}

async function selectMessagePart(partId, allowRemote) {
  if (state.selected === null || state.selectionRequest !== state.selected) return;
  if (allowRemote && state.selected !== null) {
    state.remoteContentAuthorizedMessage = state.selected;
    state.remoteContentAuthorizedPart = partId;
  }
  clearCurrentMessageFind();
  clearMessageFindUpdate();
  state.messageFindQuery = elements["message-find"].hidden ? "" : elements["message-find-query"].value.trim();
  invalidateMessageFindTargets();
  const displayed = await showPart(partId, hasRemoteContentAuthorization(state.selected, partId));
  if (displayed && state.messageFindQuery) await moveMessageFind(1);
}

function highlightPattern() {
  const find = state.messageFindQuery;
  const terms = [...new Map([find, ...state.highlightTerms]
    .filter(term => typeof term === "string" && term.length)
    .map(term => [term.toLowerCase(), term])).values()];
  if (find) terms.splice(1, terms.length - 1, ...terms.slice(1).sort((left, right) => right.length - left.length));
  else terms.sort((left, right) => right.length - left.length);
  const key = terms.map(term => term.toLowerCase()).join("\u0000");
  if (highlightPattern._cacheKey === key) return highlightPattern._cache;
  if (!terms.length) {
    highlightPattern._cacheKey = "";
    highlightPattern._cache = null;
    return null;
  }
  const escaped = terms.map(term => term.replace(/[.*+?^${}()|[\[\]\\]/g, "\\$&"));
  const regex = new RegExp(escaped.join("|"), "giu");
  highlightPattern._cacheKey = key;
  highlightPattern._cache = regex;
  return regex;
}

function highlightedFragment(value, owner = document, marker = "outer") {
  const fragment = owner.createDocumentFragment();
  const pattern = highlightPattern();
  let cursor = 0;
  let count = 0;
  if (pattern) {
    pattern.lastIndex = 0;
    for (const match of value.matchAll(pattern)) {
      fragment.append(owner.createTextNode(value.slice(cursor, match.index)));
      const mark = owner.createElement("mark");
      mark.className = "search-highlight";
      if (state.messageFindQuery && match[0].toLowerCase() === state.messageFindQuery.toLowerCase()) {
        mark.classList.add("message-find-match");
        mark.dataset.mailarchiverFindTarget = marker;
      }
      mark.textContent = match[0];
      fragment.append(mark);
      cursor = match.index + match[0].length;
      count += 1;
    }
  }
  fragment.append(owner.createTextNode(value.slice(cursor)));
  return {fragment, count};
}

function setHighlightedText(element, value) {
  element.replaceChildren(highlightedFragment(value, element.ownerDocument).fragment);
}

function highlightedHtml(value) {
  const parsed = new DOMParser().parseFromString(value, "text/html");
  const walker = parsed.createTreeWalker(parsed.body, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) {
    if (!walker.currentNode.parentElement?.closest("style, noscript, template")) {
      nodes.push(walker.currentNode);
    }
  }
  let count = 0;
  let findIndex = 0;
  const marker = `mailarchiver-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${++state.messageFindMarker}`}`;
  for (const node of nodes) {
    const highlighted = highlightedFragment(node.nodeValue, parsed, marker);
    count += highlighted.count;
    highlighted.fragment.querySelectorAll?.(".message-find-match").forEach(mark => {
      mark.dataset.messageFindIndex = String(findIndex);
      findIndex += 1;
    });
    node.replaceWith(highlighted.fragment);
  }
  const style = parsed.createElement("style");
  style.textContent = `mark.search-highlight { color: inherit; background: ${state.highlightBackground}; } mark.message-find-current { color: #000 !important; background: #ff9f0a !important; box-shadow: inset 0 0 0 2px #b54a00; }`;
  parsed.head.append(style);
  parsed.documentElement.dataset.mailarchiverFrameToken = marker;
  return {content: `<!doctype html>${parsed.documentElement.outerHTML}`, count, findCount: findIndex, marker};
}

function resizeFrame(frame) {
  try {
    const document = frame.contentDocument;
    const height = Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight || 0,
      document.documentElement.offsetHeight, document.body?.offsetHeight || 0);
    frame.style.height = `${Math.max(460, height + 20)}px`;
  } catch (_) { /* sandbox */ }
}

function renderAttachments(attachments) {
  elements["attachment-section"].hidden = attachments.length === 0;
  elements["attachment-list"].replaceChildren();
  clearAttachmentPreview();
  for (const attachment of attachments) {
    const item = document.createElement("div");
    item.className = "attachment";
    const name = document.createElement("span"); name.className = "attachment-name"; name.textContent = attachment.filename;
    const size = document.createElement("span"); size.className = "attachment-size"; size.textContent = byteSize(attachment.byte_length);
    item.append(name, size);
    if (attachment.preview) item.append(actionButton("Preview", () => previewAttachment(attachment)));
    item.append(actionButton("Open", () => openAttachment(attachment)));
    item.append(actionButton("Save…", () => saveAttachment(attachment)));
    elements["attachment-list"].append(item);
  }
}

function actionButton(label, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

async function previewAttachment(attachment) {
  const content = await call(() => window.pywebview.api.attachment(state.selected, attachment.part_id));
  if (!content) return;
  clearAttachmentPreview();
  const bytes = Uint8Array.from(atob(content.content_base64), character => character.charCodeAt(0));
  state.previewUrl = URL.createObjectURL(new Blob([bytes], {type: content.content_type}));
  const preview = attachment.preview === "image" ? document.createElement("img") : document.createElement("iframe");
  preview.src = state.previewUrl;
  preview.alt = content.filename;
  elements["attachment-preview"].append(preview);
}

async function openAttachment(attachment) {
  let result = await call(() => window.pywebview.api.open_attachment(state.selected, attachment.part_id, false));
  if (result?.requires_confirmation && confirm(`${attachment.filename} may contain executable content. Open it anyway?`)) {
    result = await call(() => window.pywebview.api.open_attachment(state.selected, attachment.part_id, true));
  }
}

function saveAttachment(attachment) {
  call(() => window.pywebview.api.save_attachment(state.selected, attachment.part_id));
}

function clearAttachmentPreview() {
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = null;
  elements["attachment-preview"].replaceChildren();
}

function selectedDragMessagePks() {
  const selection = [...state.resultSelection];
  return selection.length ? selection : state.selected ? [state.selected] : [];
}

function showMultipleMessageSelection() {
  const count = state.resultSelection.size;
  if (count < 2) return;
  clearLinkDestination();
  state.selectionRequest = null;
  state.partRequest += 1;
  state.selected = null;
  state.view = null;
  clearCurrentMessageFind();
  clearMessageFindUpdate();
  state.remoteContentAuthorizedMessage = null;
  state.remoteContentAuthorizedPart = null;
  const title = document.createElement("h1");
  title.textContent = `${count} messages selected.`;
  const detail = document.createElement("p");
  detail.textContent = "Drag the file icon to Finder to export the selected messages as a ZIP archive.";
  elements["message-selection-summary"].replaceChildren(title, detail, elements["message-file-well"]);
  elements["message-selection-summary"].hidden = false;
  elements["message-content"].hidden = false;
  elements["message-content"].classList.add("multi-selection");
  elements["message-well"].classList.add("multi-selection");
}

function showSingleMessageSelection() {
  const summary = elements["message-selection-summary"];
  if (elements["message-file-well"].parentElement !== elements["message-well"].querySelector(".message-summary")) {
    elements["message-well"].querySelector(".message-summary").append(elements["message-file-well"]);
  }
  summary.replaceChildren();
  summary.hidden = true;
  elements["message-content"].classList.remove("multi-selection");
  elements["message-well"].classList.remove("multi-selection");
}

function dragExportKey(messagePks) {
  return [...new Set(messagePks)].sort((left, right) => left - right).join(",");
}

function updateMessageFileWell() {
  const messagePks = selectedDragMessagePks();
  if (!messagePks.length) return;
  const info = state.dragExports.get(dragExportKey(messagePks));
  const multiple = messagePks.length > 1;
  elements["message-file-name"].textContent = info?.filename || (multiple ? `${messagePks.length} Messages.zip` : "Message.eml");
  const action = multiple ? "Drag selected messages to Finder as a ZIP archive" : "Drag this message to Finder";
  elements["message-file-well"].title = `${action}; the first drag prepares its temporary file`;
  elements["message-file-well"].setAttribute("aria-label", action);
}

function installDrag(element, messagePks) {
  element.addEventListener("dragstart", async event => {
    const messages = messagePks();
    const key = dragExportKey(messages);
    const info = state.dragExports.get(key);
    if (!info) {
      event.preventDefault();
      await prepareDrag(messages);
      return;
    }
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData("text/uri-list", info.url);
    event.dataTransfer.setData("DownloadURL", `${info.content_type}:${info.filename}:${info.url}`);
    event.dataTransfer.setData("text/plain", info.url);
  });
}

async function prepareDrag(messagePks) {
  if (!messagePks.length) return;
  const key = dragExportKey(messagePks);
  if (state.dragExports.has(key) || state.dragPreparing.has(key)) return;
  state.dragPreparing.add(key);
  const info = await call(() => window.pywebview.api.prepare_drag(messagePks));
  state.dragPreparing.delete(key);
  if (info) {
    state.dragExports.set(key, info);
    if (dragExportKey(selectedDragMessagePks()) === key) updateMessageFileWell();
  }
}

async function call(operation) {
  try { return await operation(); }
  catch (error) { showError(String(error?.message || error)); return null; }
}

function showError(message) {
  elements.error.textContent = message;
  elements.error.hidden = false;
  clearTimeout(showError.timer);
  showError.timer = setTimeout(() => { elements.error.hidden = true; }, 6000);
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat(undefined, {dateStyle: "medium"}).format(date);
}

function byteSize(value) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
