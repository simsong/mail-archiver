/* Requirement: the real GUI can load one page or every remaining search-result page. */
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
    throw new Error(`Timed out: ${label}; results=${document.querySelectorAll("#result-list .result").length}; status=${document.getElementById("result-status")?.textContent}`);
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
  const treeNode = label => [...document.querySelectorAll(".mailbox-node")]
    .find(node => node.dataset.label === label);

  (async () => {
    await waitFor(() => typeof window.pywebview?.api?.search === "function", "native bridge injected");
    await waitFor(() => rows().length === 100, "initial search displays first 100 results", 20000);
    assert(document.getElementById("archive-label").textContent.includes("archive"), "archive status displayed");
    assert(document.title.includes("archive") && document.title.includes("(207 messages)"), "window title identifies archive and total message count");
    await waitFor(() => document.getElementById("ingest-status-line").textContent.includes("Last ingest completed"), "completed ingest status appears in the main status line");
    document.getElementById("ingest-status-line").click();
    await waitFor(() => document.getElementById("ingest-status-line").dataset.openedWindow === "true", "ingest status line invokes the independent ingest window");
    assert(!document.getElementById("load-more").hidden, "pagination control displayed");
    assert(!document.getElementById("load-all").hidden, "load-all control displayed");
    assert(document.getElementById("load-more").textContent === "Load more", "ordinary pagination is labeled Load more");
    await waitFor(() => document.querySelector(".result-preview")?.textContent.length > 0, "background preview displayed");

    const searchInput = document.getElementById("search");
    searchInput.value = "beth";
    searchInput.dispatchEvent(new Event("input", {bubbles: true}));
    await waitFor(
      () => !document.getElementById("search-suggestions").hidden && [...document.querySelectorAll(".suggestion-heading")].some(item => item.textContent === "Addresses"),
      "typing opens grouped address and subject completions",
    );
    const bethAddress = [...document.querySelectorAll(".suggestion-option")]
      .find(item => item.textContent.includes("beth@example.org"));
    assert(bethAddress && bethAddress.textContent.includes("2"), "address completion shows deduplicated message count");
    assert([...document.querySelectorAll(".suggestion-option")].some(item => item.textContent.includes("ELISABETH")), "subject completion uses substring matching");
    bethAddress.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true}));
    await waitFor(() => document.querySelectorAll(".search-chip").length === 1 && rows().length === 2, "address completion creates an Any filter chip");
    const role = document.querySelector(".search-chip select");
    assert([...role.options].map(option => option.textContent).join(",") === "Any,From,To,Cc,Bcc", "address chip offers all recipient-role menus");
    role.value = "from"; role.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => rows().length === 1, "From menu scopes the selected address");
    role.value = "to"; role.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => rows().length === 0, "To menu excludes non-To occurrences");
    role.value = "cc"; role.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => rows().length === 1, "Cc menu uses preserved recipient roles");
    role.value = "bcc"; role.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => rows().length === 0, "Bcc menu uses preserved recipient roles");
    role.value = "any"; role.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => rows().length === 2, "Any menu restores sender-or-recipient matching");
    document.querySelector(".search-chip-remove").click();
    await waitFor(() => !document.querySelector(".search-chip") && rows().length === 100, "address filter chip can be removed");

    searchInput.value = "beth";
    searchInput.dispatchEvent(new Event("input", {bubbles: true}));
    await waitFor(() => [...document.querySelectorAll(".suggestion-option")].some(item => item.textContent.includes("ELISABETH")), "subject completions reopen");
    const bethSubject = [...document.querySelectorAll(".suggestion-option")]
      .find(item => item.textContent.includes("Your Flight Receipt - ELISABETH"));
    bethSubject.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true}));
    await waitFor(() => document.querySelector(".search-chip")?.textContent.includes("Subject:") && rows().length === 1, "subject completion creates a subject filter chip");
    document.querySelector(".search-chip-remove").click();
    await waitFor(() => !document.querySelector(".search-chip") && rows().length === 100, "subject filter chip can be removed");

    await search("bulk", 100, false);
    assert(
      document.getElementById("load-more").textContent.startsWith("Find older ones — shown:"),
      "deferred pagination names older results and the displayed date range",
    );
    document.getElementById("load-more").click();
    await waitFor(() => rows().length === 200, "Find older ones appends a complete-query page");
    assert(document.getElementById("load-more").textContent === "Load more", "complete-query continuation returns to Load more");
    document.getElementById("load-more").click();
    await waitFor(() => rows().length === 203, "Load more appends the final complete-query page");
    assert(document.getElementById("load-more").hidden, "Load more hides on the final page");

    await search("", 100, false);
    document.getElementById("load-all").click();
    document.getElementById("search").value = 'subject:"Rich UI message"';
    document.getElementById("search-form").dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));
    await waitFor(() => rows().length === 1 && subjects()[0] === "Rich UI message", "a newer search stops an in-progress Load all");
    await search("", 100, false);
    document.getElementById("load-all").click();
    assert(document.getElementById("result-status").textContent.startsWith("Loading all…"), "Load all reports running progress");
    await waitFor(() => rows().length === 207, "Load all appends every remaining page", 20000);
    assert(document.getElementById("load-more").hidden, "Load More hides on the final page");
    assert(document.getElementById("load-all").hidden, "Load all hides on the final page");
    await waitFor(
      () => rows().every(row => row.querySelector(".result-preview").textContent.length > 0),
      "serialized preview queue fills every loaded result",
      20000,
    );
    const chooseArchive = document.getElementById("choose-archive");
    const completedChoices = chooseArchive.dataset.completed || "0";
    chooseArchive.click();
    await waitFor(
      () => chooseArchive.dataset.completed !== completedChoices && rows().length === 100 && document.getElementById("result-status").textContent !== "Searching…",
      "choose-archive control refreshes the active archive",
    );

    const sort = document.getElementById("sort-by");
    sort.value = "subject";
    sort.dispatchEvent(new Event("change", {bubbles: true}));
    document.getElementById("sort-direction").click();
    await waitFor(() => subjects()[0] === "Bulk message 001", "subject sort ascending is applied");
    assert(document.getElementById("sort-direction").textContent === "↑", "sort direction control updates");

    await search("Appendixquartz", 0, false);
    assert(
      !document.getElementById("load-more").hidden && document.getElementById("load-more").textContent === "Find older ones",
      "partial recent search offers an explicit complete older-message search",
    );
    document.getElementById("load-more").click();
    await waitFor(() => document.getElementById("load-more").hidden, "complete older-message search confirms no body match");
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

    await search('"message viewer"', 1, false);
    const rich = rows()[0];
    rich.click();
    await waitFor(() => document.getElementById("message-subject").textContent === "Rich UI message", "message viewer opens");
    const dateBanner = document.getElementById("computed-date-banner");
    assert(!dateBanner.hidden, "computed-date warning banner displayed");
    assert(dateBanner.textContent.includes("Tue, 31 Dec 2024 12:00:00 +0000"), "banner identifies original Date header");
    assert(dateBanner.textContent.includes("2024-02-02T00:00:00+00:00"), "banner identifies Received median and routing UTC date");
    assert(document.getElementById("message-well").classList.contains("computed-date"), "computed-date message tint applied");
    assert(document.getElementById("message-headers").textContent.includes("curator@example.net"), "message headers displayed");
    assert(document.getElementById("message-locations").textContent.includes("Source path"), "source provenance displayed");
    assert(document.getElementById("message-locations").textContent.includes("Archive mailbox"), "archive provenance displayed");
    assert(document.querySelectorAll("#attachment-list .attachment").length === 2, "attachment list displayed");
    assert(!document.getElementById("remote-content").hidden, "remote HTML is initially blocked");
    const htmlFrame = document.querySelector("#body-view iframe");
    assert(htmlFrame, "preferred HTML body displayed");
    assert(htmlFrame.dataset.highlightCount === "1" && htmlFrame.srcdoc.includes('class="search-highlight">message viewer</mark>'), "search phrase is highlighted in HTML body text");
    assert(htmlFrame.srcdoc.includes("background: #fff59d"), "HTML highlighting uses the configured yellow background");
    document.getElementById("remote-content").click();
    await waitFor(() => document.getElementById("remote-content").hidden, "remote-content opt-in reloads HTML");

    const parts = document.getElementById("part-select");
    parts.value = [...parts.options].find(option => option.textContent.startsWith("Plain Text")).value;
    parts.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => document.querySelector("#body-view .plain")?.textContent.includes("Plain E2E body"), "plain MIME alternative displayed");
    const plainHighlight = document.querySelector("#body-view .plain mark.search-highlight");
    assert(plainHighlight?.textContent === "message viewer", "search phrase is highlighted in plain text");
    assert(getComputedStyle(plainHighlight).backgroundColor === "rgb(255, 245, 157)", "plain-text highlighting uses the configured yellow background");
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "0", metaKey: true, bubbles: true}));
    await waitFor(() => document.querySelector("#body-view .raw")?.textContent.includes("Subject: Rich UI message"), "Command-0 displays raw source");
    assert(document.querySelectorAll("#body-view .raw mark.search-highlight").length === 2, "search phrase is highlighted throughout raw source");

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

    await search("", 100, false);
    const showTree = document.getElementById("show-original-folders");
    assert(document.getElementById("mailbox-browser").hidden, "original-mailbox tree starts hidden");
    showTree.checked = true;
    showTree.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => !document.getElementById("mailbox-browser").hidden && treeNode("Inbox"), "original-mailbox tree appears");
    assert(treeNode("Inbox").textContent.includes("204"), "mailbox count is deduplicated");
    const mailboxLabels = [...document.querySelectorAll(".mailbox-node")].map(node => node.dataset.label);
    assert(
      treeNode("Loose Mail") && !treeNode("001-single.eml"),
      `single-message files collapse into their containing folder; labels=${mailboxLabels.join(",")}`,
    );
    treeNode("Inbox").querySelector("input[type=checkbox]").click();
    await waitFor(() => rows().length === 100 && !document.getElementById("load-more").hidden, "mailbox selection filters before pagination");
    document.getElementById("load-more").click();
    await waitFor(() => rows().length === 200, "Load more appends one mailbox-filtered page");
    assert(!document.getElementById("load-more").hidden, "Load more remains available before the final mailbox-filtered page");

    showTree.checked = false;
    showTree.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => document.getElementById("mailbox-browser").hidden && rows().length === 100, "hiding tree disables its filter");
    showTree.checked = true;
    showTree.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => !document.getElementById("mailbox-browser").hidden && rows().length === 100, "showing tree restores its filter selection");

    const showVolumes = document.getElementById("show-source-volumes");
    showVolumes.checked = true;
    showVolumes.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => document.querySelector(".mailbox-node[data-kind=volume]"), "source-volume roots can be shown");
    showVolumes.checked = false;
    showVolumes.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => !document.querySelector(".mailbox-node[data-kind=volume]"), "source volumes are merged when hidden");

    const filterSets = document.getElementById("filter-set");
    filterSets.value = "__save__";
    filterSets.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => document.getElementById("save-filter-dialog").open, "Save opens a filter-set naming dialog");
    document.getElementById("filter-set-name").value = "Work";
    document.getElementById("save-filter-form").dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));
    await waitFor(() => [...filterSets.options].some(option => option.textContent === "Work"), "named filter set is saved");
    filterSets.value = "";
    filterSets.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => rows().length === 100 && !document.getElementById("load-more").hidden, "None disables the saved mailbox filter");
    filterSets.value = "Work";
    filterSets.dispatchEvent(new Event("change", {bubbles: true}));
    await sleep(300);
    await waitFor(() => rows().length === 100 && !document.getElementById("load-more").hidden, "named filter set restores its selection");
    document.getElementById("load-more").click();
    await waitFor(() => rows().length === 200, "restored filter set appends one page from the saved mailbox union");
    filterSets.value = "__save__";
    filterSets.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => document.getElementById("save-filter-dialog").open, "Save can clone the active filter set");
    document.getElementById("filter-set-name").value = "Work Copy";
    document.getElementById("save-filter-form").dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));
    await waitFor(() => [...filterSets.options].some(option => option.textContent === "Work Copy"), "active filter set is cloned under a new name");

    document.getElementById("manage-filter-sets").click();
    await waitFor(() => document.getElementById("manage-filter-dialog").open, "filter-set manager opens");
    const managed = document.querySelector("#filter-set-list .filter-set-row");
    managed.querySelector("input").value = "Professional";
    [...managed.querySelectorAll("button")].find(button => button.textContent === "Rename").click();
    await waitFor(() => [...filterSets.options].some(option => option.textContent === "Professional"), "filter set can be renamed");
    [...managed.querySelectorAll("button")].find(button => button.textContent === "Delete").click();
    await waitFor(() => ![...filterSets.options].some(option => option.textContent === "Professional"), "filter set can be deleted");
    document.getElementById("close-filter-manager").click();

    document.getElementById("search").value = '"';
    document.getElementById("search-form").dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));
    await waitFor(
      () => !document.getElementById("error").hidden && document.getElementById("error").textContent.includes("unclosed quote"),
      "search errors are shown to the user",
    );

    rich.dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
    await waitFor(() => rich.dataset.openedWindow === "true", "double-click invokes the message-window bridge");
    window.__mailarchiveE2E = {passed: true, checks, error: null};
  })().catch(error => {
    window.__mailarchiveE2E = {passed: false, checks, error: String(error?.message || error)};
  });
})();
