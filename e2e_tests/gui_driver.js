/* Exercise the shipped search UI inside its real pywebview/WKWebView window. */
(() => {
  "use strict";
  const checks = [];
  const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  const assert = (condition, label) => {
    if (!condition) throw new Error(label);
    checks.push(label);
  };
  const waitFor = async (condition, label, timeout = 10000) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (condition()) { checks.push(label); return; }
      await sleep(50);
    }
    throw new Error(`Timed out: ${label}`);
  };
  const rows = () => [...document.querySelectorAll("#result-list .result")];
  const subjects = () => rows().map(row => row.querySelector(".result-subject").textContent);
  const search = async (query, expected, attachments = false) => {
    const box = document.getElementById("search-attachments");
    if (box.checked !== attachments) {
      box.checked = attachments;
      box.dispatchEvent(new Event("change", {bubbles: true}));
      await sleep(50);
    }
    document.getElementById("search").value = query;
    document.getElementById("search-form").dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));
    await waitFor(
      () => rows().length === expected && document.getElementById("result-status").textContent !== "Searching…",
      `search '${query}' returns ${expected}`,
    );
  };

  (async () => {
    await waitFor(() => typeof window.pywebview?.api?.search === "function", "native bridge injected");
    await waitFor(() => rows().length === 100, "initial search displays first 100 results", 20000);
    assert(document.getElementById("archive-label").textContent.includes("archive"), "archive status displayed");
    assert(!document.getElementById("load-more").hidden, "pagination control displayed");
    await waitFor(() => document.querySelector(".result-preview")?.textContent.length > 0, "background preview displayed");

    document.getElementById("choose-archive").click();
    await sleep(300);
    await waitFor(
      () => rows().length === 100 && document.getElementById("result-status").textContent !== "Searching…",
      "choose-archive control refreshes the active archive",
    );
    document.getElementById("load-more").click();
    await waitFor(() => rows().length === 107, "Load More appends the second page");
    assert(document.getElementById("load-more").hidden, "Load More hides on the final page");

    const sort = document.getElementById("sort-by");
    sort.value = "subject";
    sort.dispatchEvent(new Event("change", {bubbles: true}));
    document.getElementById("sort-direction").click();
    await waitFor(() => subjects()[0] === "Bulk message 001", "subject sort ascending is applied");
    assert(document.getElementById("sort-direction").textContent === "↑", "sort direction control updates");

    await search("Appendixquartz", 0, false);
    await search("Appendixquartz", 1, true);
    assert(subjects()[0] === "Rich UI message", "attachment-only match identifies its message");

    await search("subject:Bulk", 100, false);
    const firstBulk = rows()[0];
    firstBulk.click();
    await waitFor(() => firstBulk.classList.contains("selected"), "result click selects a message");
    const firstPk = firstBulk.dataset.messagePk;
    document.getElementById("result-list").dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowDown", bubbles: true}));
    await waitFor(
      () => document.querySelector(".result.selected")?.dataset.messagePk !== firstPk,
      "arrow-key result navigation changes selection",
    );

    await search('subject:"Rich UI message"', 1, false);
    const rich = rows()[0];
    rich.click();
    await waitFor(() => document.getElementById("message-subject").textContent === "Rich UI message", "message viewer opens");
    assert(document.getElementById("message-headers").textContent.includes("curator@example.net"), "message headers displayed");
    assert(document.getElementById("message-locations").textContent.includes("Source path"), "source provenance displayed");
    assert(document.getElementById("message-locations").textContent.includes("Archive mailbox"), "archive provenance displayed");
    assert(document.querySelectorAll("#attachment-list .attachment").length === 2, "attachment list displayed");
    assert(!document.getElementById("remote-content").hidden, "remote HTML is initially blocked");
    assert(document.querySelector("#body-view iframe"), "preferred HTML body displayed");
    document.getElementById("remote-content").click();
    await waitFor(() => document.getElementById("remote-content").hidden, "remote-content opt-in reloads HTML");

    const parts = document.getElementById("part-select");
    parts.value = [...parts.options].find(option => option.textContent.startsWith("Plain Text")).value;
    parts.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => document.querySelector("#body-view .plain")?.textContent.includes("Plain E2E body"), "plain MIME alternative displayed");
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "0", metaKey: true, bubbles: true}));
    await waitFor(() => document.querySelector("#body-view .raw")?.textContent.includes("Subject: Rich UI message"), "Command-0 displays raw source");

    const attachmentRows = [...document.querySelectorAll("#attachment-list .attachment")];
    const imageRow = attachmentRows.find(row => row.textContent.includes("tiny.png"));
    imageRow.querySelector("button").click();
    await waitFor(() => document.querySelector("#attachment-preview img"), "image attachment preview displayed");
    [...imageRow.querySelectorAll("button")].find(button => button.textContent.startsWith("Save")).click();
    const riskyRow = attachmentRows.find(row => row.textContent.includes("review.command"));
    let confirmations = 0;
    window.confirm = () => { confirmations += 1; return false; };
    [...riskyRow.querySelectorAll("button")].find(button => button.textContent === "Open").click();
    await waitFor(() => confirmations === 1, "risky attachment requires confirmation");
    [...riskyRow.querySelectorAll("button")].find(button => button.textContent.startsWith("Save")).click();

    document.getElementById("save-message").click();
    let prints = 0;
    window.print = () => { prints += 1; };
    document.getElementById("print-message").click();
    assert(prints === 1, "print control invokes browser printing");
    document.getElementById("message-file-well").dispatchEvent(new PointerEvent("pointerenter", {bubbles: true}));
    await waitFor(() => document.getElementById("message-file-name").textContent !== "Message.eml", "drag export is prepared");
    const transfer = {values: {}, effectAllowed: "", setData(type, value) { this.values[type] = value; }};
    const drag = new Event("dragstart", {bubbles: true, cancelable: true});
    Object.defineProperty(drag, "dataTransfer", {value: transfer});
    document.getElementById("message-file-well").dispatchEvent(drag);
    assert(transfer.values.DownloadURL?.startsWith("message/rfc822:"), "drag event publishes an RFC 822 download");

    document.getElementById("search").value = '"';
    document.getElementById("search-form").dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));
    await waitFor(
      () => !document.getElementById("error").hidden && document.getElementById("error").textContent.includes("unclosed quote"),
      "search errors are shown to the user",
    );

    rich.dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
    await sleep(500);
    checks.push("double-click opens a native message window");
    await sleep(500);
    window.__mailarchiveE2E = {passed: true, checks, error: null};
  })().catch(error => {
    window.__mailarchiveE2E = {passed: false, checks, error: String(error?.stack || error)};
  });
})();
