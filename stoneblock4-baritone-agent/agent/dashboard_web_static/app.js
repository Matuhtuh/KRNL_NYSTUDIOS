const state = {
  sessionId: "",
  confirmationToken: "",
  streamingMessageEl: null,
  statusStream: null,
  statusReconnectTimer: 0,
  preview: {
    requestedFrameKey: "",
    displayedFrameKey: "",
    failedFrameKey: "",
    loading: false,
    failed: false,
    lastMeta: null,
  },
};

const els = {
  previewImage: document.getElementById("preview-image"),
  previewOverlay: document.getElementById("preview-overlay"),
  previewBadge: document.getElementById("preview-badge"),
  previewDetail: document.getElementById("preview-detail"),
  statusBadge: document.getElementById("status-badge"),
  stageLabel: document.getElementById("stage-label"),
  stageSummary: document.getElementById("stage-summary"),
  actionText: document.getElementById("action-text"),
  actionDetail: document.getElementById("action-detail"),
  bridgeSummary: document.getElementById("bridge-summary"),
  bridgeDetail: document.getElementById("bridge-detail"),
  inventorySummary: document.getElementById("inventory-summary"),
  inventoryList: document.getElementById("inventory-list"),
  operatorCurrent: document.getElementById("operator-current"),
  operatorNext: document.getElementById("operator-next"),
  operatorDeferred: document.getElementById("operator-deferred"),
  lastError: document.getElementById("last-error"),
  chatLog: document.getElementById("chat-log"),
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  sendButton: document.getElementById("send-button"),
  chatBadge: document.getElementById("chat-badge"),
  confirmPanel: document.getElementById("confirm-panel"),
  confirmText: document.getElementById("confirm-text"),
  confirmButton: document.getElementById("confirm-button"),
  messageTemplate: document.getElementById("message-template"),
};

function badge(el, text, mode = "") {
  el.textContent = text;
  el.className = "badge";
  if (mode) {
    el.classList.add(mode);
  }
}

function setChatBadge(text, mode = "") {
  badge(els.chatBadge, text, mode);
}

function formatAgeSeconds(value) {
  if (!Number.isFinite(Number(value))) {
    return "";
  }
  const age = Number(value);
  if (age < 1) {
    return `${age.toFixed(1)}s old`;
  }
  if (age < 60) {
    return `${Math.round(age)}s old`;
  }
  const minutes = Math.floor(age / 60);
  const seconds = Math.round(age % 60);
  return `${minutes}m ${seconds}s old`;
}

function setPreviewOverlay(mode, text = "") {
  els.previewOverlay.textContent = text;
  els.previewOverlay.classList.remove("hidden", "notice");
  if (mode === "hidden") {
    els.previewOverlay.classList.add("hidden");
    return;
  }
  if (mode === "notice") {
    els.previewOverlay.classList.add("notice");
  }
}

function describePreview(preview) {
  const parts = [];
  if (preview.width && preview.height) {
    parts.push(`${preview.width}x${preview.height}`);
  }
  const ageText = formatAgeSeconds(preview.ageSeconds);
  if (ageText) {
    parts.push(preview.fresh ? ageText : `stale ${ageText}`);
  }
  if (!parts.length && preview.error) {
    parts.push(preview.error);
  }
  return parts.join(" | ") || "Waiting for preview frames.";
}

function requestPreviewFrame(preview, frameKey) {
  state.preview.loading = true;
  state.preview.failed = false;
  state.preview.requestedFrameKey = frameKey;
  state.preview.failedFrameKey = "";
  const url = preview.url || "/api/preview.jpg";
  els.previewImage.src = `${url}?t=${encodeURIComponent(frameKey)}`;
}

function renderPreview(preview) {
  const meta = preview || {};
  state.preview.lastMeta = meta;
  els.previewDetail.textContent = describePreview(meta);

  const frameKey = String(meta.observedAt || "");
  const hasDisplayedFrame = Boolean(state.preview.displayedFrameKey);
  const needsFrame =
    Boolean(meta.available) &&
    Boolean(frameKey) &&
    !state.preview.loading &&
    !hasDisplayedFrame &&
    frameKey !== state.preview.failedFrameKey;
  const hasNewFrame =
    Boolean(meta.available) &&
    Boolean(frameKey) &&
    !state.preview.loading &&
    frameKey !== state.preview.displayedFrameKey &&
    frameKey !== state.preview.requestedFrameKey &&
    frameKey !== state.preview.failedFrameKey;

  if (needsFrame || hasNewFrame) {
    requestPreviewFrame(meta, frameKey || `frame-${Date.now()}`);
  }

  if (!meta.available) {
    badge(els.previewBadge, hasDisplayedFrame ? "Preview Offline" : "Preview Missing", "warn");
    setPreviewOverlay(hasDisplayedFrame ? "notice" : "blocker", meta.error || "Preview unavailable");
    return;
  }

  if (state.preview.failed) {
    badge(els.previewBadge, "Preview Error", "warn");
    setPreviewOverlay(hasDisplayedFrame ? "notice" : "blocker", "Preview image failed to load");
    return;
  }

  if (state.preview.loading && !hasDisplayedFrame) {
    badge(els.previewBadge, "Loading Preview", "");
    setPreviewOverlay("blocker", "Loading preview");
    return;
  }

  if (state.preview.loading) {
    badge(els.previewBadge, meta.fresh ? "Refreshing Preview" : "Refreshing Stale Preview", meta.fresh ? "" : "warn");
    setPreviewOverlay("notice", "Refreshing preview");
    return;
  }

  badge(els.previewBadge, meta.fresh ? "Preview Fresh" : "Preview Stale", meta.fresh ? "ok" : "warn");
  setPreviewOverlay(
    meta.fresh ? "hidden" : "notice",
    meta.fresh ? "" : (meta.error || `Preview stale${formatAgeSeconds(meta.ageSeconds) ? ` (${formatAgeSeconds(meta.ageSeconds)})` : ""}`)
  );
}

function addMessage(role, text) {
  const node = els.messageTemplate.content.firstElementChild.cloneNode(true);
  node.querySelector(".message-role").textContent = role;
  node.querySelector(".message-text").textContent = text || "";
  els.chatLog.appendChild(node);
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
  return node.querySelector(".message-text");
}

function setConfirmation(contract) {
  const requires = Boolean(contract && contract.requires_confirmation && contract.confirmation_token);
  if (!requires) {
    state.confirmationToken = "";
    els.confirmPanel.classList.add("hidden");
    return;
  }
  state.confirmationToken = contract.confirmation_token;
  const action = contract.proposed_action || {};
  els.confirmText.textContent = action.display || contract.text || "Confirmation required.";
  els.confirmPanel.classList.remove("hidden");
}

function renderInventory(snapshot) {
  const inventory = snapshot.inventory || {};
  els.inventorySummary.textContent = `${inventory.freeSlots ?? 0} free slots`;
  els.inventoryList.innerHTML = "";
  (inventory.topStacks || []).slice(0, 8).forEach((row) => {
    const li = document.createElement("li");
    li.textContent = `${row.itemId} x${row.count}`;
    els.inventoryList.appendChild(li);
  });
}

function renderSnapshot(snapshot) {
  const stage = snapshot.stage || {};
  const action = snapshot.currentAction || {};
  const bridge = (snapshot.bridge || {}).status || {};
  const bridgeFreshness = (snapshot.bridge || {}).freshness || {};
  const operatorLoop = snapshot.operatorLoop || {};

  els.stageLabel.textContent = stage.label || stage.stageHint || "-";
  els.stageSummary.textContent = stage.summary || "-";
  els.actionText.textContent = action.explanation || "-";
  els.actionDetail.textContent = action.taskText || action.activeCommand || action.message || "-";
  els.bridgeSummary.textContent = `inWorld=${bridge.inWorld} bridgeOk=${bridge.bridgeOk} pathing=${bridge.isPathing}`;
  els.bridgeDetail.textContent = `${bridge.playerName || "-"} | ${bridge.stoneblockStageHint || "-"} | ${bridge.currentGoal || ""}`.trim();
  els.lastError.textContent = snapshot.lastError || "None";
  els.operatorCurrent.textContent = operatorLoop.currentTask || action.taskText || "idle";
  els.operatorNext.textContent = `Next: ${operatorLoop.nextSelectedTask || "-"} | Reason: ${operatorLoop.nextTaskReason || "-"}`;
  els.operatorDeferred.textContent = `Deferred: ${((operatorLoop.deferredTasks || []).map((row) => `${row.kind}:${row.id}`).join(", ")) || "-"}`;

  badge(
    els.statusBadge,
    bridgeFreshness.fresh ? "Status Fresh" : "Status Stale",
    bridgeFreshness.fresh ? "ok" : "warn"
  );

  renderPreview(snapshot.preview || {});

  renderInventory(snapshot);
}

async function createSession() {
  try {
    const resp = await fetch("/api/chat/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!resp.ok) {
      throw new Error(`chat session failed (${resp.status})`);
    }
    const data = await resp.json();
    state.sessionId = data.sessionId;
    const chatStatus = data.chatStatus || {};
    const configured = Boolean(chatStatus.apiConfigured);
    const provider = String(chatStatus.provider || "").trim();
    const model = String(chatStatus.model || "").trim();
    const reason = String(chatStatus.reason || "").trim();
    if (configured) {
      const label = [provider, model].filter(Boolean).join(" / ");
      setChatBadge(label ? `AI Ready (${label})` : "AI Ready", "ok");
    } else if (reason) {
      setChatBadge("AI Offline", "warn");
      addMessage("Assistant", `AI chat is offline: ${reason}`);
    } else {
      setChatBadge("AI Offline", "warn");
    }
  } catch (error) {
    console.error("chat session bootstrap failed", error);
    setChatBadge("Chat Offline", "warn");
  }
}

function handleChatStatus(payload) {
  const phase = String((payload || {}).phase || "");
  if (!phase) return;
  if (phase === "request_started") {
    const model = String((payload || {}).model || "").trim();
    setChatBadge(model ? `Requesting ${model}` : "Requesting", "");
    return;
  }
  if (phase === "first_token") {
    setChatBadge("Streaming", "");
    return;
  }
  if (phase === "stream_complete") {
    setChatBadge("Ready", "ok");
  }
}

function handleFinalContract(contract) {
  if (state.streamingMessageEl && contract.text) {
    state.streamingMessageEl.textContent = contract.text;
  } else if (contract.text) {
    addMessage("Assistant", contract.text);
  }
  state.streamingMessageEl = null;
  setConfirmation(contract);
  if (contract && contract.status === "error") {
    setChatBadge("Chat Error", "warn");
    return;
  }
  if (contract && contract.requires_confirmation) {
    setChatBadge("Confirm Action", "warn");
    return;
  }
  setChatBadge("Ready", "ok");
}

async function streamChat(message) {
  const userText = message.trim();
  if (!userText) return;
  addMessage("You", userText);
  if (!state.sessionId) {
    await createSession();
    if (!state.sessionId) {
      addMessage("Assistant", "Chat is unavailable right now.");
      return;
    }
  }
  state.streamingMessageEl = addMessage("Assistant", "");
  setChatBadge("Requesting", "");

  let sawFinal = false;
  try {
    const resp = await fetch("/api/chat/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: state.sessionId, message: userText }),
    });
    if (!resp.ok) {
      throw new Error(`chat stream failed (${resp.status})`);
    }
    if (!resp.body) {
      throw new Error("chat stream body missing");
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const block of parts) {
        const lines = block.split("\n");
        let eventName = "message";
        let dataText = "";
        for (const line of lines) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          if (line.startsWith("data:")) dataText += line.slice(5).trim();
        }
        if (!dataText) continue;
        let payload;
        try {
          payload = JSON.parse(dataText);
        } catch (error) {
          throw new Error(`chat stream payload parse failed: ${error.message || error}`);
        }
        if (eventName === "status") {
          handleChatStatus(payload);
          continue;
        }
        if (eventName === "delta" && state.streamingMessageEl) {
          state.streamingMessageEl.textContent += payload.text || "";
        }
        if (eventName === "tool") {
          addMessage("Tool", `${payload.name}: ${payload.summary}`);
        }
        if (eventName === "final") {
          sawFinal = true;
          handleFinalContract(payload);
        }
      }
    }
    if (!sawFinal) {
      throw new Error("chat stream ended without final reply");
    }
  } catch (error) {
    const visibleText = `Chat request failed: ${error.message || error}`;
    console.error("chat stream failed", error);
    if (state.streamingMessageEl) {
      state.streamingMessageEl.textContent = visibleText;
    } else {
      addMessage("Assistant", visibleText);
    }
    state.streamingMessageEl = null;
    setConfirmation(null);
    setChatBadge("Chat Error", "warn");
  }
}

async function confirmPending() {
  if (!state.confirmationToken) return;
  const resp = await fetch("/api/chat/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sessionId: state.sessionId,
      confirmationToken: state.confirmationToken,
    }),
  });
  const payload = await resp.json();
  handleFinalContract(payload);
}

function subscribeStatus() {
  if (state.statusStream) {
    state.statusStream.close();
  }
  const evt = new EventSource("/events/status");
  state.statusStream = evt;
  evt.addEventListener("snapshot", (event) => {
    renderSnapshot(JSON.parse(event.data));
  });
  evt.addEventListener("error", () => {
    if (state.statusStream !== evt) {
      return;
    }
    evt.close();
    state.statusStream = null;
    if (state.statusReconnectTimer) {
      return;
    }
    state.statusReconnectTimer = window.setTimeout(() => {
      state.statusReconnectTimer = 0;
      subscribeStatus();
    }, 1500);
  });
}

els.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = els.chatInput.value;
  els.chatInput.value = "";
  await streamChat(text);
});

els.confirmButton.addEventListener("click", confirmPending);
els.previewImage.addEventListener("load", () => {
  state.preview.loading = false;
  state.preview.failed = false;
  state.preview.displayedFrameKey = state.preview.requestedFrameKey || state.preview.displayedFrameKey;
  renderPreview(state.preview.lastMeta || {});
});
els.previewImage.addEventListener("error", () => {
  state.preview.loading = false;
  state.preview.failed = true;
  state.preview.failedFrameKey = state.preview.requestedFrameKey;
  renderPreview(state.preview.lastMeta || {});
});

async function boot() {
  subscribeStatus();
  try {
    const initial = await fetch("/api/snapshot");
    if (!initial.ok) {
      throw new Error(`snapshot bootstrap failed (${initial.status})`);
    }
    renderSnapshot(await initial.json());
  } catch (error) {
    console.error("snapshot bootstrap failed", error);
    badge(els.previewBadge, "Preview Waiting", "warn");
    els.previewDetail.textContent = "Waiting for snapshot.";
    setPreviewOverlay("blocker", "Waiting for snapshot");
  }
  createSession();
}

boot();
