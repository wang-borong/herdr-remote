"use strict";

const byId = (id) => document.getElementById(id);
const isDesktop = () => window.matchMedia("(min-width: 901px)").matches;

const elements = {
  connectionDot: byId("connection-dot"),
  connectionLabel: byId("connection-label"),
  connectionBanner: byId("connection-banner"),
  reconnectButton: byId("reconnect-button"),
  newAgentButton: byId("new-agent-button"),
  emptyNewAgent: byId("empty-new-agent"),
  settingsButton: byId("settings-button"),
  agentTotal: byId("agent-total"),
  blockedCount: byId("blocked-count"),
  workingCount: byId("working-count"),
  doneCount: byId("done-count"),
  agentSearch: byId("agent-search"),
  agentList: byId("agent-list"),
  detailEmpty: byId("detail-empty"),
  detailConsole: byId("detail-console"),
  mobileBack: byId("mobile-back"),
  detailAvatar: byId("detail-avatar"),
  detailTitle: byId("detail-title"),
  detailStatus: byId("detail-status"),
  detailMeta: byId("detail-meta"),
  blockedBanner: byId("blocked-banner"),
  blockedPrompt: byId("blocked-prompt"),
  approvalActions: byId("approval-actions"),
  terminalOutput: byId("terminal-output"),
  lastUpdated: byId("last-updated"),
  lineCount: byId("line-count"),
  autoRefreshButton: byId("auto-refresh-button"),
  refreshOutputButton: byId("refresh-output-button"),
  copyOutputButton: byId("copy-output-button"),
  interruptButton: byId("interrupt-button"),
  promptForm: byId("prompt-form"),
  promptInput: byId("prompt-input"),
  promptCount: byId("prompt-count"),
  sendPromptButton: byId("send-prompt-button"),
  newAgentDialog: byId("new-agent-dialog"),
  pathJumpForm: byId("path-jump-form"),
  pathJumpInput: byId("path-jump-input"),
  directoryRefreshButton: byId("directory-refresh-button"),
  directoryUpButton: byId("directory-up-button"),
  directoryPath: byId("directory-path"),
  directoryList: byId("directory-list"),
  directoryNote: byId("directory-note"),
  selectedDirectory: byId("selected-directory"),
  selectedDirectoryPath: byId("selected-directory-path"),
  selectDirectoryButton: byId("select-directory-button"),
  initialPromptInput: byId("initial-prompt-input"),
  initialPromptCount: byId("initial-prompt-count"),
  launchDirectory: byId("launch-directory"),
  startAgentButton: byId("start-agent-button"),
  interruptDialog: byId("interrupt-dialog"),
  confirmInterruptButton: byId("confirm-interrupt-button"),
  settingsDialog: byId("settings-dialog"),
  themeSelect: byId("theme-select"),
  pushStatus: byId("push-status"),
  pushToggleButton: byId("push-toggle-button"),
  sessionAvatar: byId("session-avatar"),
  sessionName: byId("session-name"),
  sessionLogin: byId("session-login"),
  sessionAuth: byId("session-auth"),
  toastRegion: byId("toast-region"),
};

const STATUS = {
  blocked: { label: "需要处理", rank: 0 },
  working: { label: "工作中", rank: 1 },
  idle: { label: "待命", rank: 2 },
  done: { label: "已完成", rank: 3 },
};

const state = {
  ws: null,
  connection: "connecting",
  reconnectAttempt: 0,
  reconnectTimer: null,
  agents: [],
  activePane: null,
  filter: "all",
  query: "",
  outputs: new Map(),
  outputUpdatedAt: new Map(),
  userScrolledUp: false,
  autoRefresh: true,
  lines: 120,
  session: null,
  directory: null,
  selectedDirectory: null,
  directoryPending: false,
  promptPending: false,
  pendingPromptText: "",
  interruptPending: false,
  startPending: false,
  pushSubscription: null,
  serviceWorkerRegistration: null,
};

const initialPane = new URL(window.location.href).searchParams.get("pane");

function removeLegacyCredentials() {
  localStorage.removeItem("herdr_relay_token");
  localStorage.removeItem("herdr_relay_url");
  const url = new URL(window.location.href);
  if (!url.searchParams.has("token")) return;
  url.searchParams.delete("token");
  history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

function applyTheme(theme) {
  const selected = ["system", "dark", "light"].includes(theme) ? theme : "system";
  document.documentElement.dataset.theme = selected;
  elements.themeSelect.value = selected;
  localStorage.setItem("herdr_theme", selected);
}

function normalizedStatus(agent) {
  const value = String(agent?.status || "idle").toLowerCase();
  if (value === "blocked") return "blocked";
  if (value === "working" || value === "running") return "working";
  if (value === "done" || value === "completed" || value === "finished") return "done";
  return "idle";
}

function statusLabel(agent) {
  return STATUS[normalizedStatus(agent)].label;
}

function agentLabel(agent) {
  return agent?.label || agent?.project || agent?.agent || agent?.pane_id || "Agent";
}

function shortPath(path) {
  if (!path) return "未提供工作目录";
  const parts = String(path).split("/").filter(Boolean);
  if (parts.length <= 3) return path;
  return `…/${parts.slice(-3).join("/")}`;
}

function activeAgent() {
  return state.agents.find((agent) => agent.pane_id === state.activePane) || null;
}

function socketReady() {
  return state.ws && state.ws.readyState === WebSocket.OPEN;
}

function send(message) {
  if (!socketReady()) {
    showToast("连接不可用", "Relay 尚未连接，请稍后重试。", "error");
    return false;
  }
  state.ws.send(JSON.stringify(message));
  return true;
}

function setConnection(status) {
  state.connection = status;
  const labels = {
    connecting: "正在连接",
    online: "安全连接",
    offline: "连接中断",
  };
  elements.connectionLabel.textContent = labels[status];
  elements.connectionDot.className = `connection-dot is-${status}`;
  elements.connectionBanner.hidden = status !== "offline";
  refreshActionAvailability();
}

function connect() {
  if (state.ws && [WebSocket.OPEN, WebSocket.CONNECTING].includes(state.ws.readyState)) return;
  window.clearTimeout(state.reconnectTimer);
  setConnection("connecting");

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
  state.ws = socket;

  socket.addEventListener("open", () => {
    if (state.ws !== socket) return;
    state.reconnectAttempt = 0;
    setConnection("online");
    if (state.activePane) refreshOutput();
  });

  socket.addEventListener("message", (event) => {
    if (state.ws !== socket) return;
    let message;
    try {
      message = JSON.parse(event.data);
    } catch (_) {
      return;
    }
    handleMessage(message);
  });

  socket.addEventListener("close", () => {
    if (state.ws !== socket) return;
    const operationWasPending = state.promptPending
      || state.interruptPending
      || state.startPending
      || state.directoryPending;
    state.ws = null;
    state.promptPending = false;
    state.pendingPromptText = "";
    state.interruptPending = false;
    state.startPending = false;
    state.directoryPending = false;
    renderDirectoryBrowser();
    setConnection("offline");
    if (operationWasPending) {
      showToast(
        "连接在操作期间中断",
        "Relay 未返回最终确认；重连后请先核对 Agent 状态，再决定是否重试。",
        "error",
      );
    }
    scheduleReconnect();
  });

  socket.addEventListener("error", () => {
    socket.close();
  });
}

function scheduleReconnect() {
  window.clearTimeout(state.reconnectTimer);
  const delay = Math.min(15000, 1000 * (2 ** state.reconnectAttempt));
  state.reconnectAttempt += 1;
  state.reconnectTimer = window.setTimeout(connect, delay);
}

function handleMessage(message) {
  switch (message.type) {
    case "session":
      state.session = message;
      renderSession();
      break;
    case "agents":
      replaceAgentSnapshot(Array.isArray(message.agents) ? message.agents : []);
      break;
    case "agent_update":
      if (message.agent) upsertAgent(message.agent);
      break;
    case "blocked":
      handleBlocked(message);
      break;
    case "pane_content":
      handlePaneContent(message);
      break;
    case "directory_listing":
      handleDirectoryListing(message);
      break;
    case "agent_started":
      handleAgentStarted(message);
      break;
    case "command_result":
      handleCommandResult(message);
      break;
    case "push_subscribed":
    case "push_unsubscribed":
      renderPushStatus();
      break;
    case "error":
      handleRelayError(message.message || "Relay 请求失败");
      break;
    default:
      break;
  }
}

function replaceAgentSnapshot(agents) {
  const previous = new Map(state.agents.map((agent) => [agent.pane_id, agent]));
  const nextAgents = agents.map((agent) => {
    const old = previous.get(agent.pane_id) || {};
    return {
      ...old,
      ...agent,
      options: old.options,
      blockedPrompt: old.blockedPrompt,
    };
  });
  const changed = JSON.stringify(nextAgents) !== JSON.stringify(state.agents);
  state.agents = nextAgents;

  if (state.activePane && !activeAgent()) {
    state.activePane = null;
    state.userScrolledUp = false;
    document.body.classList.remove("agent-open");
    updatePaneUrl("");
  }

  if (changed) renderAll();
  selectInitialAgentIfNeeded();
}

function upsertAgent(agent) {
  const index = state.agents.findIndex((item) => item.pane_id === agent.pane_id);
  if (index >= 0) state.agents[index] = { ...state.agents[index], ...agent };
  else state.agents.push(agent);
  renderAll();
}

function handleBlocked(message) {
  const existing = state.agents.find((agent) => agent.pane_id === message.pane_id);
  const wasBlocked = existing && normalizedStatus(existing) === "blocked";
  upsertAgent({
    ...(existing || {}),
    pane_id: message.pane_id,
    agent: message.agent || existing?.agent || "agent",
    project: message.project || existing?.project || "Agent",
    host: message.host || existing?.host || "local",
    status: "blocked",
    options: Array.isArray(message.options) ? message.options : [],
    blockedPrompt: message.prompt || "",
  });
  if (!wasBlocked) {
    showToast("Agent 需要处理", `${message.project || "Agent"} 正在等待你的操作。`, "error");
  }
}

function handlePaneContent(message) {
  if (!message.pane_id) return;
  state.outputs.set(message.pane_id, String(message.content || ""));
  state.outputUpdatedAt.set(message.pane_id, new Date());
  if (message.pane_id === state.activePane) renderOutput();
}

function handleDirectoryListing(message) {
  state.directoryPending = false;
  state.directory = message;
  if (message.can_start_agent && message.path) {
    state.selectedDirectory = {
      path: message.path,
      display_path: message.display_path || message.path,
      git_root: message.git_root || "",
    };
  } else {
    state.selectedDirectory = null;
  }
  renderDirectoryBrowser();
}

function handleAgentStarted(message) {
  state.startPending = false;
  refreshActionAvailability();
  if (!message.ok || !message.agent) return;
  const started = message.agent;
  upsertAgent(started);
  elements.newAgentDialog.close();
  selectAgent(started.pane_id);
  showToast(
    "Codex 已启动",
    started.warning || `工作目录：${started.display_path || started.cwd}`,
    started.warning ? "error" : "success",
  );
}

function handleCommandResult(message) {
  if (!message.ok) return;
  if (message.command === "agent_prompt") {
    state.promptPending = false;
    if (elements.promptInput.value.trim() === state.pendingPromptText) {
      elements.promptInput.value = "";
      updateCounter(elements.promptInput, elements.promptCount);
    }
    state.pendingPromptText = "";
    renderPromptState();
    const queued = message.delivery === "queued";
    showToast(
      queued ? "Prompt 已排队" : "Prompt 已发送",
      queued ? "Agent 正在工作，新任务已提交并将在当前任务后处理。" : "Agent 已收到新的任务。",
      "success",
    );
    window.setTimeout(refreshOutput, 500);
  }
  if (message.command === "send_keys" && state.interruptPending) {
    state.interruptPending = false;
    renderInterruptState();
    if (elements.interruptDialog.open) elements.interruptDialog.close();
    showToast("Interrupt 已发送", "已向当前 Agent 发送 Ctrl+C。", "success");
    window.setTimeout(refreshOutput, 350);
  }
}

function handleRelayError(message) {
  state.promptPending = false;
  state.interruptPending = false;
  state.startPending = false;
  state.directoryPending = false;
  renderPromptState();
  renderInterruptState();
  renderDirectoryBrowser();
  refreshActionAvailability();
  showToast("操作失败", message, "error");
}

function renderAll() {
  renderDashboard();
  renderDetail();
  refreshActionAvailability();
}

function renderDashboard() {
  const counts = { blocked: 0, working: 0, done: 0 };
  for (const agent of state.agents) {
    const status = normalizedStatus(agent);
    if (status in counts) counts[status] += 1;
  }
  elements.blockedCount.textContent = String(counts.blocked);
  elements.workingCount.textContent = String(counts.working);
  elements.doneCount.textContent = String(counts.done);
  elements.agentTotal.textContent = `${state.agents.length} Agent${state.agents.length === 1 ? "" : "s"}`;

  const query = state.query.trim().toLowerCase();
  const visible = state.agents
    .filter((agent) => {
      const status = normalizedStatus(agent);
      if (state.filter === "attention" && status !== "blocked") return false;
      if (state.filter === "working" && status !== "working") return false;
      if (state.filter === "done" && status !== "done") return false;
      if (!query) return true;
      return [agentLabel(agent), agent.agent, agent.cwd, agent.host, agent.pane_id]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query));
    })
    .sort((left, right) => {
      const statusDifference = STATUS[normalizedStatus(left)].rank - STATUS[normalizedStatus(right)].rank;
      return statusDifference || agentLabel(left).localeCompare(agentLabel(right));
    });

  const nodes = visible.map(createAgentCard);
  if (!nodes.length) nodes.push(createAgentListEmpty());
  elements.agentList.replaceChildren(...nodes);
}

function createAgentCard(agent) {
  const status = normalizedStatus(agent);
  const button = document.createElement("button");
  button.type = "button";
  button.className = `agent-card${agent.pane_id === state.activePane ? " is-selected" : ""}${status === "blocked" ? " is-blocked" : ""}`;
  button.setAttribute("aria-label", `${agentLabel(agent)}，${STATUS[status].label}`);
  button.addEventListener("click", () => selectAgent(agent.pane_id));

  const avatar = document.createElement("span");
  avatar.className = "agent-card-avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = agentLabel(agent).trim().charAt(0) || "A";

  const main = document.createElement("span");
  main.className = "agent-card-main";
  const title = document.createElement("span");
  title.className = "agent-card-title";
  title.textContent = agentLabel(agent);
  const subtitle = document.createElement("span");
  subtitle.className = "agent-card-subtitle";
  const host = agent.host && agent.host !== "local" ? ` · @${agent.host}` : "";
  subtitle.textContent = `${agent.agent || "agent"}${host}`;
  const path = document.createElement("span");
  path.className = "agent-card-path";
  path.textContent = shortPath(agent.cwd);
  main.append(title, subtitle, path);

  const trailing = document.createElement("span");
  trailing.className = "agent-card-trailing";
  const indicator = document.createElement("span");
  indicator.className = `status-indicator status-${status}`;
  indicator.textContent = STATUS[status].label;
  const chevron = document.createElement("span");
  chevron.className = "agent-card-chevron";
  chevron.setAttribute("aria-hidden", "true");
  chevron.textContent = "›";
  trailing.append(indicator, chevron);

  button.append(avatar, main, trailing);
  return button;
}

function createAgentListEmpty() {
  const empty = document.createElement("div");
  empty.className = "list-empty";
  const copy = document.createElement("div");
  const title = document.createElement("strong");
  const detail = document.createElement("span");
  if (!state.agents.length) {
    title.textContent = "尚未发现 Agent";
    detail.textContent = state.connection === "online" ? "你可以在允许的工作目录中启动一个新的 Codex。" : "连接恢复后会自动显示本机 Agents。";
  } else {
    title.textContent = "没有匹配结果";
    detail.textContent = "尝试更换筛选条件或搜索关键词。";
  }
  copy.append(title, detail);
  empty.append(copy);
  return empty;
}

function selectInitialAgentIfNeeded() {
  if (state.activePane && activeAgent()) return;
  if (initialPane && state.agents.some((agent) => agent.pane_id === initialPane)) {
    selectAgent(initialPane, false);
    return;
  }
  if (isDesktop() && state.agents.length) {
    const first = [...state.agents].sort(
      (left, right) => STATUS[normalizedStatus(left)].rank - STATUS[normalizedStatus(right)].rank,
    )[0];
    selectAgent(first.pane_id, false);
  }
}

function selectAgent(paneId, updateHistory = true) {
  state.activePane = paneId;
  state.userScrolledUp = false;
  document.body.classList.add("agent-open");
  if (updateHistory) updatePaneUrl(paneId);
  renderAll();
  refreshOutput();
}

function clearAgentSelection() {
  state.activePane = null;
  document.body.classList.remove("agent-open");
  updatePaneUrl("");
  renderAll();
}

function updatePaneUrl(paneId) {
  const url = new URL(window.location.href);
  url.searchParams.delete("token");
  if (paneId) url.searchParams.set("pane", paneId);
  else url.searchParams.delete("pane");
  history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

function renderDetail() {
  const agent = activeAgent();
  elements.detailEmpty.hidden = Boolean(agent);
  elements.detailConsole.hidden = !agent;
  if (!agent) return;

  const status = normalizedStatus(agent);
  elements.detailAvatar.textContent = agentLabel(agent).trim().charAt(0) || "A";
  elements.detailTitle.textContent = agentLabel(agent);
  elements.detailStatus.textContent = STATUS[status].label;
  elements.detailStatus.className = `status-pill status-${status}`;
  const host = agent.host && agent.host !== "local" ? ` · ${agent.host}` : "";
  elements.detailMeta.textContent = `${agent.agent || "agent"}${host} · ${agent.cwd || agent.pane_id}`;
  renderBlockedBanner(agent);
  renderOutput();
  renderPromptState();
  renderInterruptState();
}

function renderBlockedBanner(agent) {
  const blocked = normalizedStatus(agent) === "blocked";
  elements.blockedBanner.hidden = !blocked;
  elements.approvalActions.replaceChildren();
  if (!blocked) return;

  elements.blockedPrompt.textContent = agent.blockedPrompt || "Agent 需要你的确认后才能继续。";
  const options = agent.options?.length
    ? agent.options
    : ["yes, single permission", "trust, always allow", "no (tab to edit)"];
  for (const option of options) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `approval-button ${approvalClass(option)}`;
    button.textContent = approvalLabel(option);
    button.addEventListener("click", () => respondToBlocked(option));
    elements.approvalActions.append(button);
  }
}

function approvalLabel(option) {
  const value = option.toLowerCase();
  if (value.includes("single permission") || value === "yes" || value === "y") return "允许一次";
  if (value.includes("always allow") || value.includes("approve all")) return "全部允许";
  if (value.includes("configure")) return "逐项配置";
  if (value.includes("no") || value.includes("exit") || value === "n") return "拒绝";
  return option;
}

function approvalClass(option) {
  const value = option.toLowerCase();
  if (value.includes("yes") || value.includes("approve")) return "is-approve";
  if (value.includes("no") || value.includes("exit")) return "is-deny";
  return "";
}

function respondToBlocked(text) {
  if (!state.activePane) return;
  if (send({ type: "respond", pane_id: state.activePane, text })) {
    showToast("操作已发送", approvalLabel(text), "success");
    window.setTimeout(refreshOutput, 450);
  }
}

function renderOutput() {
  if (!state.activePane) return;
  const content = state.outputs.get(state.activePane);
  elements.terminalOutput.textContent = content === undefined
    ? "正在读取 Agent 输出…"
    : (content || "当前 Pane 暂无输出。 ");
  const updatedAt = state.outputUpdatedAt.get(state.activePane);
  elements.lastUpdated.textContent = updatedAt
    ? `更新于 ${updatedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`
    : "等待数据";
  if (!state.userScrolledUp) {
    window.requestAnimationFrame(() => {
      elements.terminalOutput.scrollTop = elements.terminalOutput.scrollHeight;
    });
  }
}

function refreshOutput() {
  if (!state.activePane || !socketReady()) return;
  send({ type: "read_pane", pane_id: state.activePane, lines: state.lines });
}

function renderPromptState() {
  const ready = Boolean(activeAgent()) && socketReady() && !state.promptPending;
  elements.promptInput.disabled = !Boolean(activeAgent()) || state.promptPending;
  elements.sendPromptButton.disabled = !ready || !elements.promptInput.value.trim();
  elements.sendPromptButton.querySelector("span").textContent = state.promptPending ? "正在发送…" : "发送 Prompt";
}

function submitPrompt() {
  const text = elements.promptInput.value.trim();
  if (!text || !state.activePane || state.promptPending) return;
  if (!send({ type: "agent_prompt", pane_id: state.activePane, text })) return;
  state.promptPending = true;
  state.pendingPromptText = text;
  renderPromptState();
}

function renderInterruptState() {
  elements.confirmInterruptButton.disabled = state.interruptPending || !socketReady();
  elements.confirmInterruptButton.textContent = state.interruptPending ? "正在发送…" : "确认 Interrupt";
}

function confirmInterrupt() {
  if (!state.activePane || state.interruptPending) return;
  if (!send({ type: "send_keys", pane_id: state.activePane, keys: ["C-c"] })) return;
  state.interruptPending = true;
  renderInterruptState();
}

async function copyOutput() {
  const content = state.activePane ? state.outputs.get(state.activePane) : "";
  if (!content) {
    showToast("暂无可复制内容", "先刷新当前 Agent 的输出。", "error");
    return;
  }
  try {
    await navigator.clipboard.writeText(content);
    showToast("已复制", "终端输出已复制到剪贴板。", "success");
  } catch (_) {
    showToast("复制失败", "浏览器未授予剪贴板权限。", "error");
  }
}

function setFilter(filter) {
  state.filter = filter;
  document.querySelectorAll("[data-filter]").forEach((button) => {
    if (!button.classList.contains("filter-tab")) return;
    const active = button.dataset.filter === filter;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  renderDashboard();
}

function openNewAgentDialog() {
  if (!socketReady()) {
    showToast("Relay 尚未连接", "连接恢复后才能浏览目录和启动 Agent。", "error");
    return;
  }
  state.directory = null;
  state.selectedDirectory = null;
  state.directoryPending = false;
  state.startPending = false;
  elements.initialPromptInput.value = "";
  elements.pathJumpInput.value = "";
  updateCounter(elements.initialPromptInput, elements.initialPromptCount);
  renderDirectoryBrowser();
  elements.newAgentDialog.showModal();
  browseDirectory(null);
}

function browseDirectory(path) {
  if (!socketReady()) return;
  state.directoryPending = true;
  renderDirectoryBrowser();
  const message = { type: "list_directories" };
  if (path) message.path = path;
  send(message);
}

function renderDirectoryBrowser() {
  elements.directoryList.replaceChildren();
  const listing = state.directory;
  elements.directoryPath.textContent = listing?.display_path || "配置的 Workspace 根目录";
  elements.directoryUpButton.disabled = state.directoryPending || !listing?.parent;
  elements.directoryRefreshButton.disabled = state.directoryPending || !socketReady();
  elements.directoryNote.hidden = !listing?.truncated;

  if (state.directoryPending) {
    elements.directoryList.append(directoryMessage("正在安全读取目录…", "directory-loading"));
  } else if (!listing) {
    elements.directoryList.append(directoryMessage("打开对话框后会显示允许访问的 Workspace。", "directory-empty"));
  } else if (!listing.entries?.length) {
    elements.directoryList.append(directoryMessage("此目录中没有可浏览的子目录。", "directory-empty"));
  } else {
    for (const entry of listing.entries) {
      elements.directoryList.append(createDirectoryEntry(entry));
    }
  }

  const selected = state.selectedDirectory;
  elements.selectedDirectory.hidden = !selected;
  elements.selectedDirectoryPath.textContent = selected?.display_path || "";
  elements.launchDirectory.textContent = selected?.display_path || "尚未选择";
  elements.selectDirectoryButton.disabled = state.directoryPending || !listing?.can_start_agent;
  elements.selectDirectoryButton.textContent = listing?.can_start_agent ? "当前目录已可用于启动" : "选择当前目录";
  refreshActionAvailability();
}

function directoryMessage(text, className) {
  const item = document.createElement("li");
  item.className = className;
  item.textContent = text;
  return item;
}

function createDirectoryEntry(entry) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.className = `directory-entry${entry.is_repo ? " is-repo" : ""}`;
  button.addEventListener("click", () => browseDirectory(entry.path));

  const icon = document.createElement("span");
  icon.className = "directory-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = entry.is_repo ? "⌘" : "▱";

  const main = document.createElement("span");
  main.className = "directory-entry-main";
  const name = document.createElement("strong");
  name.textContent = entry.name;
  const path = document.createElement("span");
  path.textContent = entry.display_path || entry.path;
  main.append(name, path);

  const badge = document.createElement("span");
  badge.className = "repo-badge";
  badge.textContent = entry.is_repo ? "Git" : "打开";
  button.append(icon, main, badge);
  item.append(button);
  return item;
}

function selectCurrentDirectory() {
  if (!state.directory?.can_start_agent || !state.directory.path) return;
  state.selectedDirectory = {
    path: state.directory.path,
    display_path: state.directory.display_path || state.directory.path,
    git_root: state.directory.git_root || "",
  };
  renderDirectoryBrowser();
}

function startAgent() {
  if (!state.selectedDirectory || state.startPending) return;
  const prompt = elements.initialPromptInput.value.trim();
  if (!send({
    type: "start_agent",
    kind: "codex",
    cwd: state.selectedDirectory.path,
    prompt,
  })) return;
  state.startPending = true;
  refreshActionAvailability();
}

function refreshActionAvailability() {
  const connected = socketReady();
  elements.newAgentButton.disabled = !connected;
  elements.emptyNewAgent.disabled = !connected;
  elements.refreshOutputButton.disabled = !connected || !state.activePane;
  elements.copyOutputButton.disabled = !state.activePane;
  elements.interruptButton.disabled = !connected || !state.activePane;
  elements.startAgentButton.disabled = !connected || !state.selectedDirectory || state.startPending;
  elements.startAgentButton.querySelector("span").textContent = state.startPending ? "正在启动…" : "启动 Codex";
  renderPromptState();
  renderInterruptState();
  renderPushStatus();
}

function renderSession() {
  const user = state.session?.user || {};
  const auth = state.session?.auth || "unknown";
  const displayName = user.name || user.login || (auth === "development" ? "本地开发会话" : "安全会话");
  const authLabels = {
    tailscale: "Tailscale",
    "web-session": "Token Session",
    token: "Relay Token",
    "token-query": "Relay Token",
    development: "Local Dev",
  };
  elements.sessionName.textContent = displayName;
  elements.sessionLogin.textContent = user.login || "通过本机 Relay 完成授权";
  elements.sessionAvatar.textContent = displayName.trim().charAt(0).toUpperCase() || "H";
  elements.sessionAuth.textContent = authLabels[auth] || "已连接";
}

async function initPush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    elements.pushStatus.textContent = "当前浏览器不支持 Web Push。";
    renderPushStatus();
    return;
  }
  try {
    state.serviceWorkerRegistration = await navigator.serviceWorker.register("/sw.js");
    state.pushSubscription = await state.serviceWorkerRegistration.pushManager.getSubscription();
    renderPushStatus();
  } catch (error) {
    elements.pushStatus.textContent = `通知初始化失败：${error.message}`;
    elements.pushToggleButton.disabled = true;
  }
}

function renderPushStatus() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    elements.pushToggleButton.disabled = true;
    return;
  }
  elements.pushToggleButton.disabled = !socketReady() || !state.serviceWorkerRegistration;
  if (state.pushSubscription) {
    elements.pushStatus.textContent = "Agent 阻塞时会发送浏览器通知。";
    elements.pushToggleButton.textContent = "停用通知";
  } else if (state.serviceWorkerRegistration) {
    elements.pushStatus.textContent = "可在 Agent 需要处理时及时提醒你。";
    elements.pushToggleButton.textContent = "启用通知";
  }
}

async function togglePush() {
  if (!state.serviceWorkerRegistration || !socketReady()) return;
  if (state.pushSubscription) {
    const payload = state.pushSubscription.toJSON();
    send({ type: "push_unsubscribe", subscription: payload });
    await state.pushSubscription.unsubscribe();
    state.pushSubscription = null;
    renderPushStatus();
    showToast("通知已停用", "浏览器将不再接收 Agent 状态通知。", "success");
    return;
  }

  try {
    const response = await fetch("/api/vapid-public-key", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!data.publicKey) {
      showToast("通知尚未配置", "Relay 未配置 VAPID 公钥。", "error");
      return;
    }
    state.pushSubscription = await state.serviceWorkerRegistration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(data.publicKey),
    });
    send({ type: "push_subscribe", subscription: state.pushSubscription.toJSON() });
    renderPushStatus();
    showToast("通知已启用", "Agent 阻塞时会在此设备上提醒你。", "success");
  } catch (error) {
    showToast("无法启用通知", error.message, "error");
  }
}

function urlBase64ToUint8Array(value) {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  return Uint8Array.from([...raw].map((character) => character.charCodeAt(0)));
}

function updateCounter(input, output) {
  output.textContent = `${input.value.length} / ${input.maxLength}`;
}

function showToast(title, detail, type = "info") {
  const toast = document.createElement("div");
  toast.className = `toast${type === "success" ? " is-success" : type === "error" ? " is-error" : ""}`;
  const dot = document.createElement("span");
  dot.className = "toast-dot";
  dot.setAttribute("aria-hidden", "true");
  const copy = document.createElement("span");
  const strong = document.createElement("strong");
  strong.textContent = title;
  const message = document.createElement("span");
  message.textContent = detail;
  copy.append(strong, message);
  toast.append(dot, copy);
  elements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

function closeDialogById(id) {
  const dialog = byId(id);
  if (dialog?.open) dialog.close();
}

function bindEvents() {
  elements.reconnectButton.addEventListener("click", () => {
    if (state.ws) state.ws.close();
    state.ws = null;
    connect();
  });
  elements.newAgentButton.addEventListener("click", openNewAgentDialog);
  elements.emptyNewAgent.addEventListener("click", openNewAgentDialog);
  elements.settingsButton.addEventListener("click", () => elements.settingsDialog.showModal());
  elements.mobileBack.addEventListener("click", clearAgentSelection);

  elements.agentSearch.addEventListener("input", () => {
    state.query = elements.agentSearch.value;
    renderDashboard();
  });
  document.querySelectorAll(".filter-tab").forEach((button) => {
    button.addEventListener("click", () => setFilter(button.dataset.filter));
  });
  document.querySelectorAll(".status-card").forEach((button) => {
    button.addEventListener("click", () => setFilter(button.dataset.filter));
  });

  elements.lineCount.addEventListener("change", () => {
    state.lines = Number(elements.lineCount.value) || 120;
    refreshOutput();
  });
  elements.autoRefreshButton.addEventListener("click", () => {
    state.autoRefresh = !state.autoRefresh;
    elements.autoRefreshButton.classList.toggle("is-active", state.autoRefresh);
    elements.autoRefreshButton.setAttribute("aria-pressed", String(state.autoRefresh));
    elements.autoRefreshButton.textContent = state.autoRefresh ? "自动刷新" : "已暂停";
  });
  elements.refreshOutputButton.addEventListener("click", refreshOutput);
  elements.copyOutputButton.addEventListener("click", copyOutput);
  elements.terminalOutput.addEventListener("scroll", () => {
    const output = elements.terminalOutput;
    state.userScrolledUp = output.scrollHeight - output.scrollTop - output.clientHeight > 80;
  });

  elements.promptInput.addEventListener("input", () => {
    updateCounter(elements.promptInput, elements.promptCount);
    renderPromptState();
  });
  elements.promptInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      submitPrompt();
    }
  });
  elements.promptForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitPrompt();
  });
  document.querySelectorAll("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      elements.promptInput.value = button.dataset.prompt;
      updateCounter(elements.promptInput, elements.promptCount);
      renderPromptState();
      elements.promptInput.focus();
    });
  });

  elements.interruptButton.addEventListener("click", () => elements.interruptDialog.showModal());
  elements.confirmInterruptButton.addEventListener("click", confirmInterrupt);

  elements.pathJumpForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const path = elements.pathJumpInput.value.trim();
    if (path) browseDirectory(path);
  });
  elements.directoryRefreshButton.addEventListener("click", () => browseDirectory(state.directory?.path || null));
  elements.directoryUpButton.addEventListener("click", () => browseDirectory(state.directory?.parent));
  elements.selectDirectoryButton.addEventListener("click", selectCurrentDirectory);
  elements.initialPromptInput.addEventListener("input", () => {
    updateCounter(elements.initialPromptInput, elements.initialPromptCount);
  });
  elements.startAgentButton.addEventListener("click", startAgent);

  elements.themeSelect.addEventListener("change", () => applyTheme(elements.themeSelect.value));
  elements.pushToggleButton.addEventListener("click", togglePush);

  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => closeDialogById(button.dataset.closeDialog));
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.connection === "offline") connect();
    if (!document.hidden && state.autoRefresh) refreshOutput();
  });
  window.addEventListener("online", connect);
  window.addEventListener("popstate", () => {
    const pane = new URL(window.location.href).searchParams.get("pane");
    if (pane && state.agents.some((agent) => agent.pane_id === pane)) selectAgent(pane, false);
    else if (!pane) clearAgentSelection();
  });
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", (event) => {
      if (event.data?.type !== "navigate" || typeof event.data.url !== "string") return;
      const target = new URL(event.data.url, window.location.origin);
      if (target.origin !== window.location.origin) return;
      window.location.assign(`${target.pathname}${target.search}${target.hash}`);
    });
  }
}

function initialize() {
  removeLegacyCredentials();
  applyTheme(localStorage.getItem("herdr_theme") || "system");
  bindEvents();
  updateCounter(elements.promptInput, elements.promptCount);
  updateCounter(elements.initialPromptInput, elements.initialPromptCount);
  renderAll();
  connect();
  initPush();
  window.setInterval(() => {
    if (state.autoRefresh && !document.hidden && state.connection === "online") refreshOutput();
  }, 3000);
}

initialize();
