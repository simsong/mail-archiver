/* Requirement: archive searches automatically return matches across the collection's full time span. */
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
    throw new Error(`Timed out: ${label}; painted=${document.querySelectorAll("#result-list .result").length}; retained=${state.results.length}; status=${document.getElementById("result-status")?.textContent}`);
  };
  const rows = () => [...document.querySelectorAll("#result-list .result")];
  const subjects = () => rows().map(row => row.querySelector(".result-subject").textContent);
  const isSelected = row => row.closest(".tabulator-row")?.classList.contains("tabulator-selected");
  const tableHolder = () => document.querySelector("#result-list .tabulator-tableholder");
  const currentFrameFind = () => {
    const frame = document.querySelector("#body-view iframe");
    const token = frame?.dataset.messageFindToken;
    return token && [...frame.contentDocument.querySelectorAll("mark.message-find-current")]
      .find(mark => mark.dataset.mailarchiverFindTarget === token);
  };
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
      () => state.results.length === expected && document.getElementById("result-status").textContent !== "Searching…",
      `search '${query}' returns ${expected}`,
    );
  };
  const treeNode = label => [...document.querySelectorAll(".mailbox-node")]
    .find(node => node.dataset.label === label);

  (async () => {
    await waitFor(() => typeof window.pywebview?.api?.search === "function", "native bridge injected");
    await waitFor(() => rows().length === 0 && document.getElementById("result-status").textContent === "Enter a search.", "archive opens without an implicit search");
    const help = document.querySelector(".search-help");
    assert(help?.textContent.includes("Every search covers the complete archive"), "startup displays archival search guidance");
    for (const operator of ["any:", "from:", "to:", "cc:", "bcc:", "subject:", "date:", "before:", "after:"]) {
      assert(help.textContent.includes(operator), `search help documents ${operator}`);
    }
    assert(document.getElementById("archive-label").textContent.includes("archive"), "archive status displayed");
    assert(document.title.includes("archive") && document.title.includes("(207 messages)"), "window title identifies archive and total message count");
    await waitFor(() => document.getElementById("ingest-status-line").textContent.includes("Last ingest completed"), "completed ingest status appears in the main status line");
    document.getElementById("ingest-status-line").click();
    await waitFor(() => document.getElementById("ingest-status-line").dataset.openedWindow === "true", "ingest status line invokes the independent ingest window");

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
    await waitFor(() => !document.querySelector(".search-chip") && rows().length === 0, "address filter chip can be removed without an empty search");

    searchInput.value = "beth";
    searchInput.dispatchEvent(new Event("input", {bubbles: true}));
    await waitFor(() => [...document.querySelectorAll(".suggestion-option")].some(item => item.textContent.includes("ELISABETH")), "subject completions reopen");
    const bethSubject = [...document.querySelectorAll(".suggestion-option")]
      .find(item => item.textContent.includes("Your Flight Receipt - ELISABETH"));
    bethSubject.dispatchEvent(new MouseEvent("mousedown", {bubbles: true, cancelable: true}));
    await waitFor(() => document.querySelector(".search-chip")?.textContent.includes("Subject:") && rows().length === 1, "subject completion creates a subject filter chip");
    document.querySelector(".search-chip-remove").click();
    await waitFor(() => !document.querySelector(".search-chip") && rows().length === 0, "subject filter chip can be removed without an empty search");

    await search("from:beth", 1, false);
    rows()[0].click();
    await waitFor(
      () => [...document.querySelectorAll("#message-headers mark.search-highlight")]
        .some(mark => mark.textContent.toLowerCase() === "beth"),
      "from selector highlights its matching displayed header value",
    );
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "g", metaKey: true, bubbles: true}));
    const messageFind = document.getElementById("message-find-query");
    await waitFor(() => !document.getElementById("message-find").hidden && messageFind.value === "beth" &&
      document.activeElement === messageFind && messageFind.selectionStart === 0 && messageFind.selectionEnd === 4 &&
      document.getElementById("message-find-status").textContent.startsWith("1/"),
    "Command-G opens in-message find and selects its first match");
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "g", metaKey: true, bubbles: true}));
    await waitFor(() => document.querySelector(".message-find-current"), "Command-G selects the next in-message match");
    await search("beth", 2, false);
    const firstBeth = rows()[0];
    const secondBeth = rows()[1];
    firstBeth.click();
    await waitFor(() => isSelected(firstBeth) && state.selected === Number(firstBeth.dataset.messagePk),
      "first full-text result selected");
    const realPart = window.pywebview.api.part;
    let delayedMessage = Number(firstBeth.dataset.messagePk);
    window.pywebview.api.part = async (...args) => {
      const response = await realPart(...args);
      if (args[0] === delayedMessage) await sleep(300);
      return response;
    };
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "f", metaKey: true, bubbles: true}));
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "g", metaKey: true, bubbles: true}));
    await waitFor(() => messageFind.value === "beth", "find starts from the new full-text archive search");
    secondBeth.click();
    await waitFor(() => isSelected(secondBeth) &&
      document.querySelector(".message-find-current") &&
      document.getElementById("message-find-status").textContent.startsWith("1/"),
    "changing messages during pending find work retains text and restarts at its first match");
    await sleep(350);
    assert(isSelected(secondBeth) && state.messageFindIndex === 0 &&
      document.getElementById("message-find-status").textContent.startsWith("1/"),
    "stale Command-F and Command-G work cannot advance the newly selected message");
    delayedMessage = null;
    window.pywebview.api.part = realPart;
    document.getElementById("message-find-close").click();
    await waitFor(() => document.getElementById("message-find").hidden && !messageFind.value && !state.messageFindQuery,
      "closing find clears its retained text");
    rows()[0].click();
    await waitFor(() => isSelected(rows()[0]) && !state.messageFindQuery &&
      !document.querySelector(".message-find-current"),
    "selecting another message does not revive a closed finder");
    await waitFor(() => document.getElementById("message-file-name").textContent === "Message.eml",
      "selecting a message does not prepare a temporary file");

    await search("bulk", 203, false);
    await waitFor(
      () => rows().every(row => row.querySelector(".result-preview").textContent.length > 0),
      "serialized preview queue fills every painted result",
      20000,
    );
    assert(rows().length < state.results.length && state.results.length === 203,
      "virtual result list retains all results while painting only the viewport");
    const resultsPane = tableHolder();
    const lastBulkPk = state.results.at(-1).message_pk;
    resultsPane.scrollTop = resultsPane.scrollHeight;
    resultsPane.dispatchEvent(new Event("scroll"));
    await waitFor(() => rows().some(row => Number(row.dataset.messagePk) === lastBulkPk),
      "virtual result list paints the final row after scrolling");
    resultsPane.scrollTop = 0;
    resultsPane.dispatchEvent(new Event("scroll"));
    await waitFor(() => rows().some(row => Number(row.dataset.messagePk) === state.results[0].message_pk),
      "virtual result list repaints the first row after scrolling back");
    const list = document.getElementById("result-list");
    const textSelection = new Event("selectstart", {bubbles: true, cancelable: true});
    rows()[0].querySelector(".result-subject").dispatchEvent(textSelection);
    assert(textSelection.defaultPrevented,
      "the result table prevents native text selection so a pointer drag selects rows");
    rows()[0].click();
    await waitFor(() => state.selected === Number(rows()[0].dataset.messagePk),
      "a pointer gesture ending in its starting row remains a message click");
    assert(!rows()[0].dataset.openedWindow, "a same-row click does not open a separate message window");
    window.getSelection().removeAllRanges();
    rows()[0].dispatchEvent(new MouseEvent("mousedown", {button: 0, buttons: 1, bubbles: true}));
    rows()[2].dispatchEvent(new MouseEvent("mouseenter", {buttons: 1, bubbles: true}));
    document.dispatchEvent(new MouseEvent("mouseup", {button: 0, bubbles: true}));
    rows()[2].click();
    await waitFor(() => state.resultSelection.size === 3, "dragging in the Tabulator table selects three rows");
    assert(state.resultSelection.size === 3 && !window.getSelection().toString(),
      "dragging in the result list selects message rows rather than text");
    const selectionSummary = document.getElementById("message-selection-summary");
    assert(!selectionSummary.hidden && selectionSummary.textContent.includes("3 messages selected.") &&
      document.getElementById("message-file-well").parentElement === selectionSummary,
    "multiple selected rows replace the stale message with a selected-message summary and ZIP drag icon");
    const zipTransfer = {values: {}, effectAllowed: "", setData(type, value) { this.values[type] = value; }};
    const zipDrag = new Event("dragstart", {bubbles: true, cancelable: true});
    Object.defineProperty(zipDrag, "dataTransfer", {value: zipTransfer});
    document.getElementById("message-file-well").dispatchEvent(zipDrag);
    if (!zipTransfer.values.DownloadURL) {
      await waitFor(() => state.dragExports.has([...state.resultSelection].sort((a, b) => a - b).join(",")),
        "first multi-message drag prepares a ZIP");
      const preparedZipTransfer = {values: {}, effectAllowed: "", setData(type, value) { this.values[type] = value; }};
      const preparedZipDrag = new Event("dragstart", {bubbles: true, cancelable: true});
      Object.defineProperty(preparedZipDrag, "dataTransfer", {value: preparedZipTransfer});
      document.getElementById("message-file-well").dispatchEvent(preparedZipDrag);
      assert(preparedZipTransfer.values.DownloadURL?.startsWith("application/zip:"),
        "multi-message drag publishes a ZIP download");
    } else {
      assert(zipTransfer.values.DownloadURL.startsWith("application/zip:"), "multi-message drag publishes a ZIP download");
    }
    await sleep(0);
    rows()[0].click();
    await waitFor(() => state.selected === Number(rows()[0].dataset.messagePk) && selectionSummary.hidden &&
      document.getElementById("message-file-well").parentElement.classList.contains("message-summary"),
      "a completed row drag cannot suppress the next row click");
    rows()[0].dispatchEvent(new MouseEvent("mousedown", {bubbles: true}));
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "a", metaKey: true, bubbles: true}));
    assert(state.resultSelection.size === state.results.length && rows().every(isSelected),
      "Command-A in the message list selects every result row");
    const chooseArchive = document.getElementById("choose-archive");
    const completedChoices = chooseArchive.dataset.completed || "0";
    chooseArchive.click();
    await waitFor(
      () => chooseArchive.dataset.completed !== completedChoices && state.results.length === 0,
      "choose-archive control refreshes the active archive without searching",
    );

    await search("bulk", 203, false);
    const sort = document.getElementById("sort-by");
    sort.value = "subject";
    sort.dispatchEvent(new Event("change", {bubbles: true}));
    document.getElementById("sort-direction").click();
    await waitFor(() => subjects()[0] === "Bulk message 001", "subject sort ascending is applied");
    assert(document.getElementById("sort-direction").textContent === "↑", "sort direction control updates");

    await search("Appendixquartz", 0, false);
    await search("Appendixquartz", 1, true);
    assert(subjects()[0] === "Rich UI message", "attachment-only match identifies its message");

    await search("subject:Bulk", 203, false);
    const firstBulk = rows()[0];
    firstBulk.click();
    await waitFor(() => isSelected(firstBulk) && state.selected === Number(firstBulk.dataset.messagePk),
      "result click selects a message");
    const firstPk = firstBulk.dataset.messagePk;
    document.getElementById("result-list").dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowDown", bubbles: true}));
    await waitFor(
      () => document.querySelector(".tabulator-selected .result")?.dataset.messagePk !== firstPk,
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
    assert(!document.getElementById("message-locations").textContent.includes(".mbox:"), "locations do not present offsets as pathname suffixes");
    assert(document.querySelector(".copy-source-path")?.title === "Copy source path", "local source path has a copy control");
    assert(getComputedStyle(document.getElementById("provenance-section")).userSelect === "none",
      "source-location evidence is not selectable as message text");
    const messageContent = document.getElementById("message-content");
    messageContent.style.minHeight = "1200px";
    const wellBounds = document.getElementById("message-well").getBoundingClientRect();
    const locationsBounds = document.getElementById("provenance-section").getBoundingClientRect();
    assert(wellBounds.bottom - locationsBounds.bottom < 45,
      "locations anchor to the bottom of spare message-pane height");
    messageContent.style.minHeight = "";
    document.body.classList.add("standalone");
    const standalonePane = document.getElementById("message-pane");
    assert(getComputedStyle(standalonePane).overflowY === "auto" && standalonePane.scrollHeight > standalonePane.clientHeight, "standalone message window scrolls to its complete message and locations");
    document.body.classList.remove("standalone");
    assert(document.querySelectorAll("#attachment-list .attachment").length === 2, "attachment list displayed");
    assert(!document.getElementById("remote-content").hidden, "remote HTML is initially blocked");
    const htmlFrame = document.querySelector("#body-view iframe");
    assert(htmlFrame, "preferred HTML body displayed");
    assert(htmlFrame.dataset.highlightCount === "1", "search phrase is highlighted in HTML body text");
    const externalLink = htmlFrame.contentDocument.createElement("a");
    externalLink.href = "https://example.org/archive?source=mail";
    externalLink.textContent = "External archive reference";
    htmlFrame.contentDocument.body.append(" ", externalLink);
    externalLink.dispatchEvent(new MouseEvent("pointerover", {bubbles: true}));
    assert(document.getElementById("ingest-status-line").textContent === externalLink.href,
      "hovering a message link shows its destination in the bottom status bar");
    let copiedLink = "";
    let openedLink = "";
    window.pywebview.api.copy_link = destination => { copiedLink = destination; return destination; };
    window.pywebview.api.open_link = destination => { openedLink = destination; return destination; };
    const firstLinkClick = new MouseEvent("click", {bubbles: true, cancelable: true});
    externalLink.dispatchEvent(firstLinkClick);
    const linkDialog = document.getElementById("link-dialog");
    await waitFor(() => linkDialog.open && document.getElementById("link-destination").textContent === externalLink.href,
      "clicking a message link presents the destination instead of opening it");
    assert(firstLinkClick.defaultPrevented, "message-link click prevents automatic external navigation");
    document.getElementById("link-copy").click();
    await waitFor(() => copiedLink === externalLink.href && !linkDialog.open,
      "Copy Link sends the reviewed destination to the pasteboard bridge");
    externalLink.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true}));
    await waitFor(() => linkDialog.open, "the reviewed link can be presented again for opening");
    document.getElementById("link-open").click();
    await waitFor(() => openedLink === externalLink.href && !linkDialog.open,
      "Open Link invokes the explicit native bridge action");
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "f", metaKey: true, bubbles: true}));
    await waitFor(() => messageFind.value === "message viewer" &&
      currentFrameFind(),
    "HTML find navigation renders and highlights the target match");
    const htmlCurrent = currentFrameFind();
    const htmlCurrentStyle = document.querySelector("#body-view iframe").contentWindow.getComputedStyle(htmlCurrent);
    assert(htmlCurrentStyle.backgroundColor === "rgb(255, 159, 10)" && htmlCurrentStyle.color === "rgb(0, 0, 0)",
      `HTML current match has an explicit high-contrast highlight (${htmlCurrent.outerHTML})`);
    let copiedMessageText = "";
    window.pywebview.api.copy_visible_text = text => { copiedMessageText = text; };
    document.getElementById("copy-message-text").click();
    await waitFor(() => copiedMessageText.includes("Subject: Rich UI message") && copiedMessageText.includes("HTML E2E body for the message viewer"),
      "copy control includes displayed HTML and viewer headers");
    document.getElementById("remote-content").click();
    await waitFor(() => document.getElementById("remote-content").hidden, "remote-content opt-in reloads HTML");
    messageFind.dispatchEvent(new Event("input", {bubbles: true}));
    await waitFor(() => {
      const remoteFrame = document.querySelector("#body-view iframe");
      return remoteFrame?.contentDocument?.querySelector('img[src="https://tracker.invalid/pixel.png"]') &&
        currentFrameFind();
    }, "finder redraw retains the explicit remote-content authorization");

    const parts = document.getElementById("part-select");
    const selectedHtmlPart = parts.value;
    const secondaryHtmlPart = [...parts.options].find(option => option.value !== selectedHtmlPart && option.textContent.startsWith("HTML"));
    assert(secondaryHtmlPart, "fixture exposes an independently selectable second HTML part");
    parts.value = secondaryHtmlPart.value;
    parts.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => document.querySelector("#body-view iframe")?.contentDocument?.body.textContent.includes("Secondary HTML alternative") &&
      !document.getElementById("remote-content").hidden,
    "a remote-content choice does not authorize another HTML part");
    assert(!document.querySelector("#body-view iframe").contentDocument
      .querySelector('img[src="https://tracker.invalid/secondary.png"]'),
    "an unapproved HTML part omits its remote image URL");

    parts.value = [...parts.options].find(option => option.textContent.startsWith("Plain Text")).value;
    parts.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => document.querySelector("#body-view .plain")?.textContent.includes("Plain E2E body"), "plain MIME alternative displayed");
    const plainHighlight = document.querySelector("#body-view .plain mark.search-highlight");
    assert(plainHighlight?.textContent === "message viewer", "search phrase is highlighted in plain text");
    assert(getComputedStyle(plainHighlight).backgroundColor === "rgb(255, 245, 157)", "plain-text highlighting uses the configured yellow background");
    const plainLink = document.querySelector('#body-view .plain a[href="https://example.org/plain-link?source=mail"]');
    assert(plainLink, "plain-text URLs render as links");
    plainLink.dispatchEvent(new MouseEvent("pointerover", {bubbles: true}));
    assert(document.getElementById("ingest-status-line").textContent === plainLink.href,
      "hovering a plain-text link shows its destination in the bottom status bar");
    const plainLinkClick = new MouseEvent("click", {bubbles: true, cancelable: true});
    plainLink.dispatchEvent(plainLinkClick);
    await waitFor(() => linkDialog.open && document.getElementById("link-destination").textContent === plainLink.href,
      "clicking a plain-text link presents the destination instead of navigating");
    assert(plainLinkClick.defaultPrevented, "plain-text link click prevents automatic external navigation");
    document.getElementById("link-ignore").click();
    document.querySelector("#body-view .plain").dispatchEvent(new MouseEvent("mousedown", {bubbles: true}));
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "a", metaKey: true, bubbles: true}));
    assert(window.getSelection().toString().includes("Plain E2E body") &&
      !window.getSelection().toString().includes("Subject:") && !window.getSelection().toString().includes("Locations"),
    "Command-A in plain text selects only the displayed body");
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "0", metaKey: true, bubbles: true}));
    await waitFor(() => document.querySelector("#body-view .raw")?.textContent.includes("Subject: Rich UI message"), "Command-0 displays raw source");
    assert(!document.querySelector("#body-view .raw a"), "raw source does not linkify URLs");
    assert(document.querySelectorAll("#body-view .raw mark.search-highlight").length === 2, "search phrase is highlighted throughout raw source");
    document.querySelector("#body-view .raw").dispatchEvent(new MouseEvent("mousedown", {bubbles: true}));
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "a", metaKey: true, bubbles: true}));
    assert(window.getSelection().toString().includes("Subject: Rich UI message"),
      "Command-A in raw source selects the displayed message text");
    assert(!window.getSelection().toString().includes("Locations"),
      "Command-A in raw source excludes viewer provenance");
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "f", metaKey: true, bubbles: true}));
    await waitFor(() => messageFind.value === "message viewer", "find resets for a new archive search");
    messageFind.value = "curator@example.net";
    messageFind.dispatchEvent(new Event("input", {bubbles: true}));
    await waitFor(() => [...document.querySelectorAll(".message-find-match")]
      .some(mark => mark.textContent === "curator@example.net"), "in-message find highlights replacement text");
    messageFind.value = "message viewer";
    messageFind.dispatchEvent(new Event("input", {bubbles: true}));
    await waitFor(() => document.querySelectorAll("#body-view .raw .message-find-match").length === 2,
      "in-message find identifies every raw-source match");
    const firstFind = document.querySelector(".message-find-current");
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "g", metaKey: true, bubbles: true}));
    await waitFor(() => document.querySelector(".message-find-current") !== firstFind,
      "Command-G advances to the next in-message match");

    await search("from:curator", 1, false);
    const curatorMessage = rows()[0];
    curatorMessage.click();
    await waitFor(() => isSelected(curatorMessage) && document.querySelector("#body-view iframe")?.contentDocument,
      "selector result opens for header-to-body find navigation");
    const curatorBody = document.querySelector("#body-view iframe").contentDocument.body.textContent;
    assert(curatorBody.includes("Curator one"),
      `selector result exposes the Curator body fixture (${document.getElementById("message-subject").textContent}; ${curatorBody})`);
    document.dispatchEvent(new KeyboardEvent("keydown", {key: "f", metaKey: true, bubbles: true}));
    await waitFor(() => messageFind.value === "curator", "header-to-body finder uses the selector value");
    await waitFor(() => state.messageFindTargets.length === 4 && document.getElementById("message-find-status").textContent === "1/4",
      `header-to-body finder builds four targets and begins at the first header (${state.messageFindQuery}; ${state.messageFindTargets.length})`);
    const curatorFrame = document.querySelector("#body-view iframe");
    const frameShortcut = key => !curatorFrame.contentDocument.body.dispatchEvent(new curatorFrame.contentWindow.KeyboardEvent("keydown", {
      key, metaKey: true, bubbles: true, cancelable: true,
    }));
    assert(frameShortcut("g"), "Command-G inside an HTML message is handled by the viewer");
    await waitFor(() => document.getElementById("message-find-status").textContent === "2/4" &&
      document.querySelector("#message-headers .message-find-current"), "Command-G advances through header matches");
    assert(frameShortcut("g"), "repeated Command-G inside an HTML message remains handled by the viewer");
    await waitFor(() => document.getElementById("message-find-status").textContent === "3/4" &&
      document.querySelector("#body-view iframe")?.contentDocument
        ?.querySelector('mark.message-find-current[data-message-find-index="0"]'),
    "Command-G reaches the exact first rendered body mark after headers");
    const headerBodyCurrent = document.querySelector("#body-view iframe").contentDocument
      .querySelector('mark.message-find-current[data-message-find-index="0"]');
    assert(document.querySelector("#body-view iframe").contentWindow.getComputedStyle(headerBodyCurrent).backgroundColor === "rgb(255, 159, 10)",
      "header-to-body current match remains visibly orange despite email CSS and an ID decoy");
    await waitFor(() => {
      const pane = document.getElementById("message-pane");
      const frame = document.querySelector("#body-view iframe");
      const mark = frame?.contentDocument?.querySelector('mark.message-find-current[data-message-find-index="0"]');
      if (!mark || pane.scrollTop <= 0) return false;
      const frameBounds = frame.getBoundingClientRect();
      const paneBounds = pane.getBoundingClientRect();
      const markBounds = mark.getBoundingClientRect();
      const top = frameBounds.top + markBounds.top;
      return top >= paneBounds.top && top + markBounds.height <= paneBounds.bottom;
    }, "header-to-body navigation scrolls the offscreen current HTML mark into view");

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
    const messageFileName = document.getElementById("message-file-name");
    const beforeHover = messageFileName.textContent;
    document.getElementById("message-file-well").dispatchEvent(new PointerEvent("pointerenter", {bubbles: true}));
    await new Promise(resolve => setTimeout(resolve, 100));
    assert(messageFileName.textContent === beforeHover, "hovering does not prepare a message file");
    const transfer = {values: {}, effectAllowed: "", setData(type, value) { this.values[type] = value; }};
    const drag = new Event("dragstart", {bubbles: true, cancelable: true});
    Object.defineProperty(drag, "dataTransfer", {value: transfer});
    document.getElementById("message-file-well").dispatchEvent(drag);
    if (!transfer.values.DownloadURL) {
      await waitFor(() => messageFileName.textContent !== beforeHover, "first drag prepares the message file");
      const preparedTransfer = {values: {}, effectAllowed: "", setData(type, value) { this.values[type] = value; }};
      const preparedDrag = new Event("dragstart", {bubbles: true, cancelable: true});
      Object.defineProperty(preparedDrag, "dataTransfer", {value: preparedTransfer});
      document.getElementById("message-file-well").dispatchEvent(preparedDrag);
      assert(preparedTransfer.values.DownloadURL?.startsWith("message/rfc822:"), "prepared drag publishes an RFC 822 download");
    } else {
      assert(transfer.values.DownloadURL.startsWith("message/rfc822:"), "explicit drag publishes an RFC 822 download");
    }

    await search("", 0, false);
    assert(document.querySelector(".search-help"), "empty search restores search-language help");
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
    await waitFor(() => state.results.length === 204, "mailbox selection returns its complete archive result set");

    showTree.checked = false;
    showTree.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => document.getElementById("mailbox-browser").hidden && rows().length === 0, "hiding tree disables its filter without running an empty search");
    showTree.checked = true;
    showTree.dispatchEvent(new Event("change", {bubbles: true}));
    await waitFor(() => !document.getElementById("mailbox-browser").hidden && state.results.length === 204, "showing tree restores its complete filter result set");

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
    await waitFor(() => rows().length === 0, "None disables the saved mailbox filter without running an empty search");
    filterSets.value = "Work";
    filterSets.dispatchEvent(new Event("change", {bubbles: true}));
    await sleep(300);
    await waitFor(() => state.results.length === 204, "named filter set restores its complete selection");
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

    await search('"message viewer"', 1, false);
    const windowMessage = rows()[0];
    windowMessage.dispatchEvent(new MouseEvent("dblclick", {bubbles: true}));
    await waitFor(() => windowMessage.dataset.openedWindow === "true", "double-click invokes the message-window bridge");
    window.__mailarchiveE2E = {passed: true, checks, error: null};
  })().catch(error => {
    window.__mailarchiveE2E = {passed: false, checks, error: String(error?.message || error)};
  });
})();
