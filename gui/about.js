/* Keep the persistent About window synchronized with application health. */
"use strict";

const byId = id => document.getElementById(id);
let initialized = false;

window.addEventListener("pywebviewready", initialize);
window.setTimeout(() => {
  if (window.pywebview?.api?.status) initialize();
  else if (!initialized) showError("The native application bridge did not start.");
}, 1500);

async function initialize() {
  if (initialized) return;
  initialized = true;
  await refresh();
  window.setInterval(refresh, 1000);
}

async function refresh() {
  try {
    render(await window.pywebview.api.status());
  } catch (error) {
    showError(`Could not read application status: ${String(error?.message || error)}`);
  }
}

function render(status) {
  byId("name").textContent = status.metadata.name;
  byId("version").textContent = `Version ${status.metadata.version}`;
  byId("copyright").textContent = status.metadata.copyright;
  byId("disk").textContent = `${formatBytes(status.disk_free_bytes)} available on ${status.disk_path}`;
  byId("internet").textContent = status.internet.detail;
  const activity = status.ingests.length ? status.ingests.map(activityCard) : [empty("No saved archive is open.")];
  byId("activity").replaceChildren(...activity);
  const notices = status.notices.length ? [...status.notices].reverse().map(noticeCard) : [empty("No messages.")];
  byId("notices").replaceChildren(...notices);
}

function activityCard(activity) {
  const card = document.createElement("div");
  card.className = "activity";
  const title = document.createElement("strong");
  title.textContent = activity.archive;
  const detail = document.createElement("p");
  detail.className = "detail";
  const status = activity.status;
  if (activity.operation_id && !status) detail.textContent = "Import starting…";
  else if (!status) detail.textContent = "No ingest history.";
  else if (status.state === "running") {
    detail.textContent = `${status.phase} · ${Number(status.percent).toFixed(1)}% · ${Number(status.processed_messages).toLocaleString()} messages · ETA ${status.eta}`;
  } else {
    detail.textContent = `Last ingest ${status.state} · ${status.phase} · ${Number(status.processed_messages).toLocaleString()} messages`;
  }
  card.append(title, detail);
  return card;
}

function noticeCard(notice) {
  const card = document.createElement("div");
  card.className = `notice ${notice.severity}`;
  const title = document.createElement("strong");
  title.textContent = `${notice.severity[0].toUpperCase()}${notice.severity.slice(1)} · ${new Date(notice.created_at).toLocaleString()}`;
  const message = document.createElement("p");
  message.textContent = notice.message;
  card.append(title, message);
  return card;
}

function empty(message) {
  const paragraph = document.createElement("p");
  paragraph.className = "empty";
  paragraph.textContent = message;
  return paragraph;
}

function formatBytes(value) {
  let amount = Number(value || 0);
  for (const unit of ["B", "KiB", "MiB", "GiB", "TiB"]) {
    if (amount < 1024 || unit === "TiB") return `${amount.toFixed(unit === "B" ? 0 : 1)} ${unit}`;
    amount /= 1024;
  }
  return "0 B";
}

function showError(message) {
  const error = byId("error");
  error.textContent = message;
  error.hidden = false;
}
