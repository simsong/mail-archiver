"use strict";

const byId = id => document.getElementById(id);
let ready = false;
let busy = false;

function updateButtons() {
  byId("cancel").disabled = !ready || busy;
  byId("choose-source").disabled = !ready || busy;
  byId("choose-destination").disabled = !ready || busy;
  byId("start-import").disabled = !ready || busy || !byId("source-path").value || !byId("destination-path").value;
}

async function perform(action) {
  if (busy) return;
  busy = true;
  byId("error").hidden = true;
  updateButtons();
  try {
    await action();
  } catch (error) {
    byId("error").textContent = String(error?.message || error);
    byId("error").hidden = false;
    byId("status").textContent = "Check the folders and try again.";
  } finally {
    busy = false;
    updateButtons();
  }
}

function initialize() {
  if (ready || !window.pywebview?.api?.choose_source) return;
  ready = true;
  byId("status").textContent = "You can change either folder before starting.";
  for (const [name, method] of [["source", "choose_source"], ["destination", "choose_destination"]]) {
    byId(`choose-${name}`).addEventListener("click", () => perform(async () => {
      const path = await window.pywebview.api[method]();
      if (path) {
        byId(`${name}-path`).value = path;
        byId(`${name}-path`).title = path;
      }
    }));
  }
  byId("cancel").addEventListener("click", () => perform(async () => {
    await window.pywebview.api.cancel();
  }));
  window.addEventListener("keydown", event => {
    if (event.key === "Escape" && !busy) byId("cancel").click();
  });
  byId("start-import").addEventListener("click", () => perform(async () => {
    byId("status").textContent = "Review the import settings in the dialog…";
    const started = await window.pywebview.api.start_import();
    if (started) resetSelection();
    byId("status").textContent = started ? "Choose folders to start another import." : "Import was not started. You can try again.";
  }));
  updateButtons();
}

function resetSelection() {
  for (const name of ["source", "destination"]) {
    byId(`${name}-path`).value = "";
    byId(`${name}-path`).title = "";
  }
  byId("status").textContent = "You can change either folder before starting.";
}

window.addEventListener("pywebviewready", initialize);
const bridgeRetry = window.setInterval(() => {
  initialize();
  if (ready) window.clearInterval(bridgeRetry);
}, 250);
initialize();
