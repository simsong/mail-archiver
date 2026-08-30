/* Display the append-by-run ingest status history in an independent window. */
"use strict";

let statuses = [];
let selectedStatusId = new URLSearchParams(window.location.search).get("status");
let initialized = false;
const byId = id => document.getElementById(id);
const integer = value => Number(value || 0).toLocaleString();

window.addEventListener("pywebviewready", initialize);
window.setTimeout(() => {
  if (window.pywebview?.api?.history) initialize();
  else if (!initialized) showError("The native application bridge did not start.");
}, 1500);

async function initialize() {
  if (initialized) return;
  initialized = true;
  await refreshHistory();
  window.setInterval(refreshHistory, 1000);
}

window.refreshHistory = refreshHistory;
window.selectIngest = statusId => {
  selectedStatusId = statusId;
  render();
};

async function refreshHistory() {
  try {
    const history = await window.pywebview.api.history();
    statuses = history.statuses || [];
    if (!statuses.some(status => status.status_id === selectedStatusId)) {
      selectedStatusId = statuses[0]?.status_id || null;
    }
    render(history.errors || []);
  } catch (error) {
    showError(`Could not read ingest history: ${error}`);
  }
}

function render(errors = []) {
  byId("history-count").textContent = `${integer(statuses.length)} run${statuses.length === 1 ? "" : "s"}`;
  const list = byId("history-list");
  list.replaceChildren(...statuses.map(historyRow));
  const errorBox = byId("history-errors");
  errorBox.hidden = errors.length === 0;
  errorBox.textContent = errors.map(error => `${error.filename}: ${error.detail}`).join("\n");
  const selected = statuses.find(status => status.status_id === selectedStatusId);
  renderDetail(selected || null);
}

function historyRow(status) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = `history-row${status.status_id === selectedStatusId ? " selected" : ""}`;
  row.setAttribute("role", "option");
  row.setAttribute("aria-selected", String(status.status_id === selectedStatusId));
  const dot = document.createElement("span");
  dot.className = `state-dot ${status.state}`;
  const main = document.createElement("span");
  main.className = "history-main";
  const title = document.createElement("span");
  title.className = "history-title";
  const state = document.createElement("span");
  state.className = "history-state";
  state.textContent = status.state;
  const percent = document.createElement("span");
  percent.textContent = `${Number(status.percent).toFixed(1)}%`;
  title.append(state, percent);
  const time = document.createElement("span");
  time.className = "history-time";
  time.textContent = new Date(status.started_at).toLocaleString();
  const summary = document.createElement("span");
  summary.className = "history-summary";
  summary.textContent = `${integer(status.processed_messages)} messages · ${formatDuration(status.elapsed_seconds)}`;
  main.append(title, time, summary);
  row.append(dot, main);
  row.addEventListener("click", () => { selectedStatusId = status.status_id; render(); });
  return row;
}

function renderDetail(status) {
  const detail = byId("ingest-detail");
  detail.replaceChildren();
  if (!status) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No ingest history is available for this archive.";
    detail.append(empty);
    return;
  }
  const heading = document.createElement("div");
  heading.className = "detail-heading";
  const headingText = document.createElement("div");
  const title = document.createElement("h1");
  title.textContent = `Ingest ${status.run_pk}`;
  const subtitle = document.createElement("p");
  subtitle.textContent = `${new Date(status.started_at).toLocaleString()} · process ${status.process_id}`;
  headingText.append(title, subtitle);
  const badge = document.createElement("span");
  badge.className = "state-badge";
  badge.textContent = status.state;
  heading.append(headingText, badge);

  const track = document.createElement("div");
  track.className = "progress-track";
  const fill = document.createElement("div");
  fill.className = "progress-fill";
  fill.style.width = `${Math.max(0, Math.min(Number(status.percent), 100))}%`;
  track.append(fill);
  const caption = document.createElement("div");
  caption.className = "progress-caption";
  caption.append(textSpan(status.phase), textSpan(`${Number(status.percent).toFixed(1)}% · ETA ${status.eta}`));

  detail.append(heading, track, caption, statistics(status));
  if (status.failure_detail) detail.append(section("Failure", textBlock(status.failure_detail, "failure")));
  detail.append(section("Threads", workerTable(status.workers)));
  if (status.source_roots.length) {
    const sources = document.createElement("ul");
    sources.className = "source-list";
    for (const source of status.source_roots) {
      const item = document.createElement("li");
      item.textContent = source;
      sources.append(item);
    }
    detail.append(section("Sources", sources));
  }
}

function statistics(status) {
  const values = [
    ["Messages", integer(status.processed_messages)],
    ["Files", `${integer(status.files_processed)} / ${integer(status.files_total)}`],
    ["Elapsed", formatDuration(status.elapsed_seconds)],
    ["Rate", `${Number(status.message_rate).toFixed(2)} messages/s`],
    ["Archived", integer(status.counts.archived)],
    ["Previously seen", integer(status.counts.duplicates)],
    ["Autosaved", integer(status.counts.autosaves)],
    ["Metadata", integer(status.counts.metadata_excluded)],
    ["Infected", integer(status.counts.infected)],
    ["Unchanged", integer(status.counts.unchanged_sources)],
    ["Workers", `${integer(status.active_workers)} active / ${integer(status.configured_workers)}`],
    ["Peak workers", integer(status.peak_workers)],
  ];
  const grid = document.createElement("div");
  grid.className = "statistics";
  for (const [label, value] of values) {
    const cell = document.createElement("div");
    cell.className = "stat";
    cell.append(textSpan(label), textBlock(value, "strong"));
    grid.append(cell);
  }
  return grid;
}

function workerTable(workers) {
  const table = document.createElement("table");
  table.className = "worker-table";
  const headings = document.createElement("tr");
  for (const label of ["Thread", "Phase / source", "Files", "Messages", "Progress"]) {
    const heading = document.createElement("th");
    heading.textContent = label;
    headings.append(heading);
  }
  const head = document.createElement("thead");
  head.append(headings);
  const body = document.createElement("tbody");
  for (const worker of workers) {
    const row = document.createElement("tr");
    const source = worker.path || worker.last_path || "—";
    const progress = worker.activity_unit
      ? `${integer(worker.activity_done)}${worker.activity_total === null ? "" : ` / ${integer(worker.activity_total)}`} ${worker.activity_unit}`
      : worker.bytes_total ? `${formatBytes(worker.bytes_done)} / ${formatBytes(worker.bytes_total)}` : "—";
    for (const [value, className] of [
      [worker.worker, ""], [`${worker.phase}\n${source}`, "worker-path"], [worker.files_processed, ""],
      [worker.messages_processed, ""], [progress, ""],
    ]) {
      const cell = document.createElement("td");
      cell.className = className;
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }
  table.append(head, body);
  return table;
}

function section(title, content) {
  const block = document.createElement("section");
  block.className = "detail-section";
  const heading = document.createElement("h2");
  heading.textContent = title;
  block.append(heading, content);
  return block;
}

function textSpan(value) {
  const span = document.createElement("span");
  span.textContent = value;
  return span;
}

function textBlock(value, className) {
  const block = document.createElement(className === "strong" ? "strong" : "div");
  if (className !== "strong") block.className = className;
  block.textContent = value;
  return block;
}

function formatDuration(value) {
  const seconds = Math.max(0, Math.round(Number(value || 0)));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return [hours ? `${hours}h` : "", hours || minutes ? `${minutes}m` : "", `${remainder}s`].filter(Boolean).join(" ");
}

function formatBytes(value) {
  let amount = Number(value || 0);
  for (const unit of ["B", "KiB", "MiB", "GiB", "TiB"]) {
    if (amount < 1024 || unit === "TiB") return unit === "B" ? `${amount} B` : `${amount.toFixed(1)} ${unit}`;
    amount /= 1024;
  }
  return "0 B";
}

function showError(message) {
  const error = byId("error");
  error.textContent = message;
  error.hidden = false;
}
