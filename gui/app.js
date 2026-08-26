/* Drive the read-only pywebview mail browser while rejecting stale async responses. */
"use strict";

const state = {
  query: "",
  offset: 0,
  sortBy: "date",
  sortDirection: "descending",
  searchAttachments: false,
  selected: null,
  selectionRequest: null,
  searchRequest: 0,
  partRequest: 0,
  view: null,
  dragExports: new Map(),
  previewUrl: null,
  showTree: false,
  showVolumes: false,
  mailboxTree: [],
  mailboxSelections: new Set(),
  filterSets: [],
  activeFilterSet: "",
  treeRequest: 0,
};

const elements = {};
const byId = id => document.getElementById(id);
let initialized = false;

window.addEventListener("pywebviewready", initialize);
window.setTimeout(() => {
  if (window.pywebview?.api?.status) initialize();
  else if (!initialized) showBridgeFailure();
}, 1500);

async function initialize() {
  if (initialized) return;
  initialized = true;
  for (const id of ["choose-archive", "search-form", "search", "archive-label", "result-status", "result-list", "load-more",
    "sort-by", "sort-direction", "search-attachments", "show-original-folders", "mailbox-browser", "mailbox-tree", "show-source-volumes", "filter-set", "manage-filter-sets",
    "save-filter-dialog", "save-filter-form", "filter-set-name", "cancel-save-filter", "manage-filter-dialog", "filter-set-list", "close-filter-manager",
    "message-content", "message-file-well", "message-file-name", "message-subject", "message-headers", "part-select", "remote-content",
    "save-message", "print-message", "body-view", "attachment-section", "attachment-list", "attachment-preview", "provenance-section", "message-locations", "error"]) {
    elements[id] = byId(id);
  }
  elements["choose-archive"].addEventListener("click", async () => {
    await chooseArchive();
    elements["choose-archive"].dataset.completed = String(Number(elements["choose-archive"].dataset.completed || 0) + 1);
  });
  elements["search-form"].addEventListener("submit", event => { event.preventDefault(); runSearch(false); });
  elements["load-more"].addEventListener("click", () => runSearch(true));
  elements["sort-by"].addEventListener("change", () => runSearch(false));
  elements["sort-direction"].addEventListener("click", toggleSortDirection);
  elements["search-attachments"].addEventListener("change", () => runSearch(false));
  elements["show-original-folders"].addEventListener("change", toggleMailboxTree);
  elements["show-source-volumes"].addEventListener("change", toggleSourceVolumes);
  elements["filter-set"].addEventListener("change", selectFilterSet);
  elements["manage-filter-sets"].addEventListener("click", openFilterManager);
  elements["save-filter-form"].addEventListener("submit", saveFilterSet);
  elements["cancel-save-filter"].addEventListener("click", () => elements["save-filter-dialog"].close());
  elements["close-filter-manager"].addEventListener("click", () => elements["manage-filter-dialog"].close());
  elements["result-list"].addEventListener("keydown", navigateResults);
  elements["part-select"].addEventListener("change", () => showPart(Number(elements["part-select"].value), false));
  elements["remote-content"].addEventListener("click", () => showPart(Number(elements["part-select"].value), true));
  elements["save-message"].addEventListener("click", () => call(() => window.pywebview.api.save_message(state.selected)));
  elements["print-message"].addEventListener("click", () => window.print());
  installDrag(elements["message-file-well"], () => state.selected);
  document.addEventListener("keydown", handleCommandShortcut);

  const parameters = new URLSearchParams(window.location.search);
  if (parameters.get("standalone") === "1") document.body.classList.add("standalone");
  const status = await call(() => window.pywebview.api.status());
  if (!status) return;
  await loadFilterSets();
  applyStatus(status);
  const message = Number(parameters.get("message"));
  if (message) await selectMessage(message);
  else if (status.ready) await runSearch(false);
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
    if (status.ready) await runSearch(false);
  }
}

function resetArchiveView() {
  state.searchRequest += 1;
  state.partRequest += 1;
  state.selected = null;
  state.selectionRequest = null;
  state.view = null;
  state.mailboxTree = [];
  state.dragExports.clear();
  clearAttachmentPreview();
  elements["result-list"].replaceChildren();
  elements["message-content"].hidden = true;
}

function applyStatus(status) {
  elements["archive-label"].textContent = status.archive || "No archive selected";
  elements.search.disabled = !status.ready;
  elements["result-status"].textContent = status.ready ? "Enter a search or press Return for newest mail." : "Choose an archive to begin.";
  if (status.ready) elements.search.focus();
}

async function toggleMailboxTree() {
  state.showTree = elements["show-original-folders"].checked;
  elements["mailbox-browser"].hidden = !state.showTree;
  document.querySelector(".workspace").classList.toggle("tree-visible", state.showTree);
  if (state.showTree && !state.mailboxTree.length) await loadMailboxTree();
  await runSearch(false);
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
  await runSearch(false);
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
  runSearch(false);
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
    await runSearch(false);
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
  await runSearch(false);
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

async function runSearch(append) {
  const query = elements.search.value.trim();
  const sortBy = elements["sort-by"].value;
  const sortDirection = state.sortDirection;
  const searchAttachments = elements["search-attachments"].checked;
  const sameSearch = query === state.query && sortBy === state.sortBy && sortDirection === state.sortDirection && searchAttachments === state.searchAttachments;
  const offset = append && sameSearch ? state.offset : 0;
  const request = ++state.searchRequest;
  elements["result-status"].textContent = "Searching…";
  const mailboxSelections = state.showTree ? [...state.mailboxSelections] : [];
  const page = await call(() => window.pywebview.api.search(
    query, offset, sortBy, sortDirection, searchAttachments, mailboxSelections,
  ));
  if (!page || request !== state.searchRequest) return;
  state.query = query;
  state.sortBy = sortBy;
  state.sortDirection = sortDirection;
  state.searchAttachments = searchAttachments;
  state.offset = offset + page.results.length;
  if (!append) elements["result-list"].replaceChildren();
  for (const result of page.results) {
    const row = resultRow(result);
    elements["result-list"].append(row);
  }
  requestPreviews(page.results.map(result => result.message_pk));
  elements["result-status"].textContent = `${state.offset.toLocaleString()} message${state.offset === 1 ? "" : "s"}${page.has_more ? " shown" : ""}`;
  elements["load-more"].hidden = !page.has_more;
}

function toggleSortDirection() {
  state.sortDirection = state.sortDirection === "descending" ? "ascending" : "descending";
  const descending = state.sortDirection === "descending";
  elements["sort-direction"].textContent = descending ? "↓" : "↑";
  elements["sort-direction"].ariaLabel = descending ? "Sort descending" : "Sort ascending";
  elements["sort-direction"].title = elements["sort-direction"].ariaLabel;
  runSearch(false);
}

function resultRow(result) {
  const row = document.createElement("div");
  row.className = "result";
  row.id = `message-result-${result.message_pk}`;
  row.setAttribute("role", "option");
  row.setAttribute("aria-selected", "false");
  row.dataset.messagePk = result.message_pk;
  row.draggable = true;
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
  row.append(subjectLine, line, preview);
  row.addEventListener("mousedown", () => elements["result-list"].focus({preventScroll: true}));
  row.addEventListener("click", () => selectMessage(result.message_pk));
  row.addEventListener("dblclick", async () => {
    await call(() => window.pywebview.api.open_message_window(result.message_pk));
    row.dataset.openedWindow = "true";
  });
  installDrag(row, () => result.message_pk);
  return row;
}

async function requestPreviews(messagePks) {
  if (!messagePks.length) return;
  const requested = await call(() => window.pywebview.api.request_previews(messagePks));
  if (requested === null) return;
  while (true) {
    const batch = await call(() => window.pywebview.api.take_previews(messagePks));
    if (!batch) return;
    for (const item of batch.previews) {
      const preview = document.querySelector(`.result[data-message-pk="${item.message_pk}"] .result-preview`);
      if (preview) preview.textContent = item.preview;
    }
    if (batch.error) { showError(`Could not load message previews: ${batch.error}`); return; }
    if (!batch.pending) return;
    await new Promise(resolve => setTimeout(resolve, 75));
  }
}

async function selectMessage(messagePk) {
  state.selectionRequest = messagePk;
  state.partRequest += 1;
  const view = await call(() => window.pywebview.api.message(messagePk));
  if (!view || state.selectionRequest !== messagePk) return;
  state.selected = messagePk;
  state.view = view;
  document.querySelectorAll(".result.selected").forEach(row => {
    row.classList.remove("selected");
    row.setAttribute("aria-selected", "false");
  });
  const selectedRow = document.querySelector(`.result[data-message-pk="${messagePk}"]`);
  selectedRow?.classList.add("selected");
  selectedRow?.setAttribute("aria-selected", "true");
  elements["result-list"].setAttribute("aria-activedescendant", `message-result-${messagePk}`);
  elements["message-content"].hidden = false;
  elements["message-subject"].textContent = view.subject;
  elements["message-file-name"].textContent = state.dragExports.get(messagePk)?.filename || "Message.eml";
  elements["message-headers"].replaceChildren(...headerNodes(view.headers));
  elements["part-select"].replaceChildren(...view.body_parts.map(partOption));
  elements["part-select"].value = String(view.preferred_part_id);
  renderAttachments(view.attachments);
  renderLocations(view);
  await showPart(view.preferred_part_id, false);
  prepareDrag(messagePk);
}

function renderLocations(view) {
  const locations = [];
  if (view.archive_path) locations.push(["Archive mailbox", view.archive_path]);
  for (const source of view.source_locations) {
    locations.push(["Source volume", source.volume]);
    locations.push(["Source path", `${source.path}:${source.offset}`]);
  }
  elements["provenance-section"].hidden = locations.length === 0;
  elements["message-locations"].replaceChildren(...locations.flatMap(([label, value]) => {
    const term = document.createElement("dt"); term.textContent = `${label}:`;
    const detail = document.createElement("dd"); detail.textContent = value;
    return [term, detail];
  }));
}

function navigateResults(event) {
  if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
  const rows = [...elements["result-list"].querySelectorAll(".result")];
  if (!rows.length) return;
  event.preventDefault();
  let index = rows.findIndex(row => Number(row.dataset.messagePk) === state.selected);
  if (event.key === "ArrowDown") index = Math.min(index + 1, rows.length - 1);
  else index = index < 0 ? rows.length - 1 : Math.max(index - 1, 0);
  const row = rows[index];
  row.scrollIntoView({block: "nearest"});
  selectMessage(Number(row.dataset.messagePk));
}

function handleCommandShortcut(event) {
  if (!event.metaKey || event.altKey || !state.view) return;
  let partId = null;
  if (event.key === "0" || (event.shiftKey && event.key.toLowerCase() === "u")) partId = -1;
  else if (!event.shiftKey && /^[1-9]$/.test(event.key)) partId = Number(event.key);
  if (partId === null) return;
  event.preventDefault();
  const part = state.view.body_parts.find(candidate => candidate.part_id === partId);
  if (!part) { showError(`This message has no displayable MIME part ${partId}.`); return; }
  elements["part-select"].value = String(partId);
  showPart(partId, false);
}

function headerNodes(headers) {
  const important = new Set(["from", "to", "cc", "subject", "date"]);
  return headers.filter(header => important.has(header.name.toLowerCase())).flatMap(header => {
    const term = document.createElement("dt"); term.textContent = `${header.name}:`;
    const value = document.createElement("dd"); value.textContent = header.value;
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
  if (!state.selected) return;
  const messagePk = state.selected;
  const request = ++state.partRequest;
  const part = await call(() => window.pywebview.api.part(messagePk, partId, allowRemote));
  if (!part || request !== state.partRequest || messagePk !== state.selected) return;
  elements["body-view"].replaceChildren();
  elements["remote-content"].hidden = !part.remote_content_blocked;
  if (part.kind === "html") {
    const frame = document.createElement("iframe");
    frame.className = "html-frame";
    frame.setAttribute("sandbox", "allow-popups");
    frame.srcdoc = part.content;
    frame.addEventListener("load", () => resizeFrame(frame));
    elements["body-view"].append(frame);
  } else {
    const body = document.createElement("div");
    body.className = part.kind === "raw" ? "raw" : "plain";
    body.textContent = part.content;
    elements["body-view"].append(body);
  }
}

function resizeFrame(frame) {
  try { frame.style.height = `${Math.max(460, frame.contentDocument.documentElement.scrollHeight + 20)}px`; } catch (_) { /* sandbox */ }
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

function installDrag(element, messagePk) {
  element.addEventListener("pointerenter", () => prepareDrag(messagePk()));
  element.addEventListener("dragstart", event => {
    const info = state.dragExports.get(messagePk());
    if (!info) { event.preventDefault(); showError("Message export is still being prepared; drag again."); return; }
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData("text/uri-list", info.url);
    event.dataTransfer.setData("DownloadURL", `message/rfc822:${info.filename}:${info.url}`);
    event.dataTransfer.setData("text/plain", info.url);
  });
}

async function prepareDrag(messagePk) {
  if (!messagePk || state.dragExports.has(messagePk)) return;
  const info = await call(() => window.pywebview.api.prepare_drag(messagePk));
  if (info) {
    state.dragExports.set(messagePk, info);
    if (state.selected === messagePk) elements["message-file-name"].textContent = info.filename;
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
