"use strict";

const byId = (id) => document.getElementById(id);
const isDesktop = () => window.matchMedia("(min-width: 901px)").matches;
const systemDarkThemeQuery = window.matchMedia("(prefers-color-scheme: dark)");
const AGENT_INTERACTION_KEYS = Object.freeze({
  Up: { keycap: "↑", label: "上移" },
  Down: { keycap: "↓", label: "下移" },
  Enter: { keycap: "↵", label: "Enter" },
  Escape: { keycap: "Esc", label: "返回" },
});
const AGENT_KEY_ACK_TIMEOUT_MS = 8_000;
const PROMPT_IMAGE_TYPES = Object.freeze({
  "image/png": "PNG",
  "image/jpeg": "JPEG",
  "image/webp": "WebP",
});
const PROMPT_IMAGE_MAX_COUNT = 4;
const PROMPT_IMAGE_MAX_BYTES = 8 * 1024 * 1024;
const PROMPT_IMAGE_TOTAL_MAX_BYTES = 20 * 1024 * 1024;
const ANSI_ESCAPE_SEQUENCE_RE = /\x1B(?:\][^\x07]*(?:\x07|\x1B\\)|[@-_][0-?]*[ -/]*[@-~])/g;
const AGENT_DIVIDER_TEXT_RE = /^[─━═—–_\-=]+$/u;
const AGENT_DIVIDER_RUN_RE = /[─━═—–_\-=]+/gu;
const AGENT_DIVIDER_GLYPH_RE = /[─━═—–_\-=]/gu;
const UNICODE_MARK_RE = /\p{Mark}/u;
const AGENT_OUTPUT_SCROLLBACK_LINES = 20_000;
const AGENT_OUTPUT_SCROLL_THUMB_MIN_PX = 48;

const elements = {
  connectionDot: byId("connection-dot"),
  connectionLabel: byId("connection-label"),
  connectionBanner: byId("connection-banner"),
  reconnectButton: byId("reconnect-button"),
  agentWorkspace: byId("agent-workspace"),
  terminalWorkspace: byId("terminal-workspace"),
  fileWorkspace: byId("file-workspace"),
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
  agentOutputTerminal: byId("agent-output-terminal"),
  terminalOutputFallback: byId("terminal-output-fallback"),
  agentOutputNavigation: byId("agent-output-navigation"),
  agentOutputTopButton: byId("agent-output-top-button"),
  agentOutputScrollTrack: byId("agent-output-scroll-track"),
  agentOutputScrollThumb: byId("agent-output-scroll-thumb"),
  agentOutputBottomButton: byId("agent-output-bottom-button"),
  agentKeyToggle: byId("agent-key-toggle"),
  agentKeybar: byId("agent-keybar"),
  agentKeyStatus: byId("agent-key-status"),
  agentKeyButtons: [...document.querySelectorAll("[data-agent-key]")],
  lastUpdated: byId("last-updated"),
  lineCount: byId("line-count"),
  autoRefreshButton: byId("auto-refresh-button"),
  refreshOutputButton: byId("refresh-output-button"),
  copyOutputButton: byId("copy-output-button"),
  interruptButton: byId("interrupt-button"),
  promptForm: byId("prompt-form"),
  promptInput: byId("prompt-input"),
  promptCount: byId("prompt-count"),
  promptImageInput: byId("prompt-image-input"),
  attachImageButton: byId("attach-image-button"),
  promptAttachments: byId("prompt-attachments"),
  queuePromptButton: byId("queue-prompt-button"),
  sendPromptButton: byId("send-prompt-button"),
  newAgentDialog: byId("new-agent-dialog"),
  agentSourceSelect: byId("agent-source-select"),
  agentSourceStatus: byId("agent-source-status"),
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
  launchSource: byId("launch-source"),
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
  addSshHostButton: byId("add-ssh-host-button"),
  nativeSshStatus: byId("native-ssh-status"),
  copyNativeSshButton: byId("copy-native-ssh-button"),
  terminalProfileCount: byId("terminal-profile-count"),
  terminalProfileList: byId("terminal-profile-list"),
  refreshTerminalProfilesButton: byId("refresh-terminal-profiles-button"),
  terminalEmpty: byId("terminal-empty"),
  terminalEmptyTitle: byId("terminal-empty-title"),
  terminalEmptyCopy: byId("terminal-empty-copy"),
  terminalSetupCommand: byId("terminal-setup-command"),
  copyTerminalSetupButton: byId("copy-terminal-setup-button"),
  shellConsole: byId("shell-console"),
  mobileTerminalBack: byId("mobile-terminal-back"),
  terminalProfileAvatar: byId("terminal-profile-avatar"),
  terminalProfileTitle: byId("terminal-profile-title"),
  terminalSessionStatus: byId("terminal-session-status"),
  terminalProfileMeta: byId("terminal-profile-meta"),
  editSshHostButton: byId("edit-ssh-host-button"),
  copyJumpCommandButton: byId("copy-jump-command-button"),
  disconnectTerminalButton: byId("disconnect-terminal-button"),
  copyWebTerminalButton: byId("copy-web-terminal-button"),
  clearWebTerminalButton: byId("clear-web-terminal-button"),
  reconnectTerminalButton: byId("reconnect-terminal-button"),
  webTerminal: byId("web-terminal"),
  terminalConnectionOverlay: byId("terminal-connection-overlay"),
  terminalOverlayTitle: byId("terminal-overlay-title"),
  terminalOverlayCopy: byId("terminal-overlay-copy"),
  terminalCopyButton: byId("terminal-copy-button"),
  terminalPasteButton: byId("terminal-paste-button"),
  terminalCtrlButton: byId("terminal-ctrl-button"),
  terminalTmuxPrefixButton: byId("terminal-tmux-prefix-button"),
  terminalKeybar: document.querySelector(".mobile-keybar"),
  tmuxKeybar: byId("tmux-keybar"),
  sshHostDialog: byId("ssh-host-dialog"),
  sshHostForm: byId("ssh-host-form"),
  sshHostTitle: byId("ssh-host-title"),
  sshHostId: byId("ssh-host-id"),
  sshHostLabel: byId("ssh-host-label"),
  sshHostTarget: byId("ssh-host-target"),
  sshHostPort: byId("ssh-host-port"),
  sshHostColor: byId("ssh-host-color"),
  sshHostDescription: byId("ssh-host-description"),
  sshHostAgentEnabled: byId("ssh-host-agent-enabled"),
  sshHostHerdrBin: byId("ssh-host-herdr-bin"),
  sshHostWorkspaceRoot: byId("ssh-host-workspace-root"),
  saveSshHostButton: byId("save-ssh-host-button"),
  deleteSshHostButton: byId("delete-ssh-host-button"),
  deleteSshHostDialog: byId("delete-ssh-host-dialog"),
  confirmDeleteSshHostButton: byId("confirm-delete-ssh-host-button"),
  terminalCopyDialog: byId("terminal-copy-dialog"),
  terminalCopyTitle: byId("terminal-copy-title"),
  terminalCopyDescription: byId("terminal-copy-description"),
  terminalCopyText: byId("terminal-copy-text"),
  terminalCopyMeta: byId("terminal-copy-meta"),
  terminalCopySelectAllButton: byId("terminal-copy-select-all-button"),
  terminalCopyConfirmButton: byId("terminal-copy-confirm-button"),
  fileSourceSelect: byId("file-source-select"),
  fileSourceStatus: byId("file-source-status"),
  filePanel: document.querySelector(".file-panel"),
  filePathJumpForm: byId("file-path-jump-form"),
  filePathJumpInput: byId("file-path-jump-input"),
  fileRefreshButton: byId("file-refresh-button"),
  fileUploadButton: byId("file-upload-button"),
  fileUpButton: byId("file-up-button"),
  fileDirectoryPath: byId("file-directory-path"),
  fileSearchInput: byId("file-search-input"),
  fileList: byId("file-list"),
  fileListNote: byId("file-list-note"),
  fileDetailEmpty: byId("file-detail-empty"),
  fileViewer: byId("file-viewer"),
  mobileFileBack: byId("mobile-file-back"),
  fileAvatar: byId("file-avatar"),
  fileTitle: byId("file-title"),
  fileKindBadge: byId("file-kind-badge"),
  fileMeta: byId("file-meta"),
  fileHtmlViewButton: byId("file-html-view-button"),
  copyFileButton: byId("copy-file-button"),
  downloadFileButton: byId("download-file-button"),
  fileReaderTitle: byId("file-reader-title"),
  fileReaderMeta: byId("file-reader-meta"),
  fileReaderStatus: byId("file-reader-status"),
  fileMarkdownContent: byId("file-markdown-content"),
  fileHtmlPreview: byId("file-html-preview"),
  fileCodePreview: byId("file-code-preview"),
  fileCodeContent: byId("file-code-content"),
  fileDownloadOnly: byId("file-download-only"),
  fileDownloadOnlyCopy: byId("file-download-only-copy"),
  downloadFileEmptyButton: byId("download-file-empty-button"),
  fileUploadDialog: byId("file-upload-dialog"),
  fileUploadCloseButton: byId("file-upload-close-button"),
  fileUploadTargetHost: byId("file-upload-target-host"),
  fileUploadTargetPath: byId("file-upload-target-path"),
  fileUploadDropzone: byId("file-upload-dropzone"),
  fileUploadInput: byId("file-upload-input"),
  fileUploadList: byId("file-upload-list"),
  fileUploadOverwrite: byId("file-upload-overwrite"),
  fileUploadProgress: byId("file-upload-progress"),
  fileUploadProgressBar: byId("file-upload-progress-bar"),
  fileUploadProgressCopy: byId("file-upload-progress-copy"),
  fileUploadStatus: byId("file-upload-status"),
  fileUploadCancelButton: byId("file-upload-cancel-button"),
  fileUploadSubmitButton: byId("file-upload-submit-button"),
  remoteAccessStatus: byId("remote-access-status"),
  openRemoteShellButton: byId("open-remote-shell-button"),
  toastRegion: byId("toast-region"),
};

const STATUS = {
  blocked: { label: "需要处理", rank: 0 },
  working: { label: "工作中", rank: 1 },
  idle: { label: "待命", rank: 2 },
  done: { label: "已完成", rank: 3 },
};

const TERMINAL_KEY_SEQUENCES = {
  escape: "\x1b",
  tab: "\t",
  "ctrl-c": "\x03",
  "ctrl-l": "\x0c",
  left: "\x1b[D",
  up: "\x1b[A",
  down: "\x1b[B",
  right: "\x1b[C",
  "page-up": "\x1b[5~",
  "page-down": "\x1b[6~",
};

const TERMINAL_CTRL_KEY_SEQUENCES = {
  left: "\x1b[1;5D",
  up: "\x1b[1;5A",
  down: "\x1b[1;5B",
  right: "\x1b[1;5C",
  "page-up": "\x1b[5;5~",
  "page-down": "\x1b[6;5~",
};

const TERMINAL_FOCUS_SEQUENCES = new Set(["\x1b[I", "\x1b[O"]);
const TMUX_PREFIX_SEQUENCE = "\x02";
const TMUX_ACTION_SEQUENCES = {
  prefix: TMUX_PREFIX_SEQUENCE,
  "new-window": `${TMUX_PREFIX_SEQUENCE}c`,
  "previous-window": `${TMUX_PREFIX_SEQUENCE}p`,
  "next-window": `${TMUX_PREFIX_SEQUENCE}n`,
  "window-list": `${TMUX_PREFIX_SEQUENCE}w`,
  "split-horizontal": `${TMUX_PREFIX_SEQUENCE}|`,
  "split-vertical": `${TMUX_PREFIX_SEQUENCE}_`,
  zoom: `${TMUX_PREFIX_SEQUENCE}z`,
};

const TERMINAL_FALLBACK_FONT_FAMILY = '"SFMono-Regular", "Cascadia Code", Consolas, "Liberation Mono", monospace';
const TERMINAL_FONT_FAMILY = '"Herdr FiraCode Nerd", "FiraCode Nerd Font Mono", "MesloLGS Nerd Font Mono", "SFMono-Regular", Consolas, monospace';
const TERMINAL_KEYBAR_PREFERRED_HEIGHT = 42;
const TERMINAL_KEYBAR_MIN_HEIGHT = 35;
const MOBILE_KEYBOARD_MIN_HEIGHT = 120;
const MOBILE_KEYBOARD_HEIGHT_RATIO = 0.2;
const MOBILE_VIEWPORT_WIDTH_TOLERANCE = 48;
const TERMINAL_RESIZE_ACK_TIMEOUT = 1200;
const TERMINAL_PENDING_INPUT_MAX_BYTES = 64 * 1024;
const AGENT_TOUCH_SELECTION_DELAY = 420;
const AGENT_TOUCH_MOVE_THRESHOLD = 8;
const AGENT_TOUCH_SELECTION_EDGE = 36;
const AGENT_TOUCH_SELECTION_SCROLL_INTERVAL = 80;
const NATIVE_SELECTION_RELEASE_GRACE_MS = 900;
const FILE_HIGHLIGHT_MAX_CHARACTERS = 400_000;
const MARKDOWN_CODE_HIGHLIGHT_MAX_CHARACTERS = 160_000;
const WORKSPACE_FILE_REQUEST_TIMEOUT_MS = 30_000;
const WORKSPACE_FILE_FEATURE_UNAVAILABLE = "Relay 后台尚未加载 Files 协议。请重启 Herdr Relay 服务；仅刷新网页不会更新后台进程。";
const WORKSPACE_UPLOAD_DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024;
const WORKSPACE_UPLOAD_DEFAULT_CHUNK_BYTES = 512 * 1024;
const WORKSPACE_UPLOAD_MAX_FILES = 100;
const HTML_PREVIEW_CSP = "default-src 'none'; script-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; media-src 'none'; form-action 'none'; base-uri 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'";
const HTML_PREVIEW_COMMON_STYLE = `
  :root { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  html { min-height: 100%; }
  body { max-width: 1080px; min-height: 100%; margin: 0 auto; padding: 32px 38px 72px; line-height: 1.65; overflow-wrap: anywhere; }
  img, picture, video, canvas, svg { max-width: 100%; height: auto; }
  pre { max-width: 100%; padding: 14px 16px; overflow: auto; border-radius: 10px; }
  code, pre { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
  table { max-width: 100%; border-collapse: collapse; }
  th, td { padding: 7px 10px; border: 1px solid; }
  a:not([href]) { text-decoration: line-through; cursor: not-allowed; }
  @media (max-width: 700px) { body { padding: 22px 18px 56px; } }
`;
const HTML_PREVIEW_LIGHT_STYLE = `
  :root { color-scheme: light; }
  html, body { color: #172033; background: #ffffff; }
  pre { background: #f4f6fa; }
  th, td { border-color: #ccd2df; }
  a:not([href]) { color: #687386; }
`;
const HTML_PREVIEW_DARK_STYLE = `
  :root { color-scheme: dark; }
  html, body { color: #e6ebf5 !important; background: #0b1020 !important; }
  body * {
    color: inherit !important;
    background-color: transparent !important;
    background-image: none !important;
    border-color: #34405a !important;
  }
  pre { color: #e6ebf5 !important; background: #111827 !important; border: 1px solid #34405a; }
  code { color: inherit !important; }
  table, thead, tbody, tfoot, tr { color: #e6ebf5 !important; background: #111a2c !important; }
  th, td { color: #e6ebf5 !important; background: #151f33 !important; border-color: #3a4660 !important; }
  thead th, thead td { color: #f8fafc !important; background: #233250 !important; }
  a { color: #8fb4ff !important; }
  a:not([href]) { color: #9aa6be !important; }
`;
const nativeAgentSelectionControllers = new WeakMap();
const terminalFontReady = document.fonts && typeof document.fonts.load === "function"
  ? document.fonts.load('400 13px "Herdr FiraCode Nerd"').catch(() => [])
  : Promise.resolve([]);

const state = {
  ws: null,
  activeView: "agents",
  connection: "connecting",
  reconnectAttempt: 0,
  reconnectTimer: null,
  agents: [],
  agentSources: [],
  selectedSource: "local",
  activePane: null,
  filter: "all",
  query: "",
  outputs: new Map(),
  ansiOutputs: new Map(),
  outputUpdatedAt: new Map(),
  userScrolledUp: false,
  outputTerminalInstance: null,
  outputTerminalFitAddon: null,
  outputTerminalResizeObserver: null,
  outputTerminalSurfaceWidth: 0,
  outputTerminalSurfaceHeight: 0,
  outputRenderedPane: null,
  outputRenderedSnapshot: null,
  outputRenderedColumns: 0,
  outputRenderId: 0,
  outputNavigationFrame: null,
  autoRefresh: true,
  lines: 500,
  session: null,
  directory: null,
  selectedDirectory: null,
  directoryPending: false,
  fileSource: "local",
  fileListing: null,
  fileListingPending: false,
  fileListingRequestId: 0,
  fileListingError: "",
  fileQuery: "",
  activeFile: null,
  fileReadPending: false,
  fileReadRequestId: 0,
  fileDownloadPending: false,
  fileDownloadRequestId: 0,
  fileUploadSupported: false,
  fileUploadMaxBytes: WORKSPACE_UPLOAD_DEFAULT_MAX_BYTES,
  fileUploadChunkBytes: WORKSPACE_UPLOAD_DEFAULT_CHUNK_BYTES,
  fileUploadEntries: [],
  fileUploadSequence: 0,
  fileUploadActive: null,
  fileUploadPending: false,
  fileUploadTarget: null,
  fileUploadDragDepth: 0,
  fileReaderRenderedMode: "",
  fileReaderRenderedSource: "",
  fileReaderRenderedPath: "",
  fileReaderRenderedSignature: "",
  fileReaderRenderedContent: null,
  fileHtmlSourceMode: false,
  promptPending: false,
  pendingPromptText: "",
  pendingPromptMode: "",
  pendingPromptImageIds: [],
  pendingPromptSubmissionId: 0,
  promptSubmissionSequence: 0,
  promptImages: [],
  promptImageSequence: 0,
  pendingApprovalLabel: "",
  agentKeyPanelOpen: false,
  agentKeyPending: null,
  agentKeyPendingTimer: null,
  agentKeyFeedback: "",
  agentKeyFeedbackType: "",
  agentKeyFeedbackPane: "",
  agentKeyFeedbackTimer: null,
  agentKeySequence: 0,
  interruptPending: false,
  startPending: false,
  pushSubscription: null,
  serviceWorkerRegistration: null,
  terminalAuthorized: false,
  nativeSshEnabled: false,
  machine: null,
  terminalProfiles: [],
  activeTerminalProfile: null,
  terminalRequestedProfile: null,
  terminalSessionId: null,
  terminalPending: false,
  terminalConnected: false,
  terminalPersistent: false,
  terminalCtrlPending: false,
  terminalTmuxPrefixPending: false,
  terminalShouldReconnect: false,
  terminalInstance: null,
  terminalFitAddon: null,
  terminalResizeObserver: null,
  terminalFitFrame: null,
  terminalFitTimer: null,
  terminalFitScrollToBottom: false,
  terminalResizeDirty: false,
  terminalResizeSequence: 0,
  terminalResizePendingId: null,
  terminalResizeAckTimer: null,
  terminalResizeSentCols: 0,
  terminalResizeSentRows: 0,
  terminalResizeAckCols: 0,
  terminalResizeAckRows: 0,
  terminalPendingInput: [],
  terminalPendingInputBytes: 0,
  terminalCaptureId: 0,
  terminalCapturePending: false,
  copyDialogSource: "",
  visualViewportWidth: 0,
  visualViewportBaselineHeight: 0,
  shellKeyboardOpen: false,
  sshProfilePending: false,
  pendingDeleteProfile: null,
};

const initialUrl = new URL(window.location.href);
const initialView = ["terminal", "files"].includes(initialUrl.searchParams.get("view"))
  ? initialUrl.searchParams.get("view")
  : "agents";
const initialTerminalProfile = initialUrl.searchParams.get("terminal");

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
  applyTerminalTheme();
  refreshActiveHtmlPreviewTheme();
}

function resolvedTheme() {
  const selected = document.documentElement.dataset.theme;
  if (selected === "dark" || selected === "light") return selected;
  return systemDarkThemeQuery.matches ? "dark" : "light";
}

function refreshActiveHtmlPreviewTheme() {
  if (
    state.activeFile?.kind !== "html"
    || state.fileHtmlSourceMode
    || typeof state.activeFile.content !== "string"
  ) return;
  renderWorkspaceFileViewer();
}

function handleSystemThemeChange() {
  if (document.documentElement.dataset.theme !== "system") return;
  applyTerminalTheme();
  refreshActiveHtmlPreviewTheme();
}

function terminalTheme() {
  const styles = getComputedStyle(document.documentElement);
  return {
    background: styles.getPropertyValue("--terminal").trim() || "#060810",
    foreground: styles.getPropertyValue("--terminal-text").trim() || "#d7def0",
    cursor: styles.getPropertyValue("--accent-strong").trim() || "#a69bff",
    cursorAccent: styles.getPropertyValue("--terminal").trim() || "#060810",
    selectionBackground: "rgba(139, 124, 255, 0.36)",
    black: "#111827",
    red: "#ff6b7f",
    green: "#4ed49c",
    yellow: "#f5b95c",
    blue: "#8b7cff",
    magenta: "#d78bff",
    cyan: "#53d7de",
    white: "#d7def0",
    brightBlack: "#667085",
    brightRed: "#ff8b9a",
    brightGreen: "#72e5b5",
    brightYellow: "#ffd27d",
    brightBlue: "#afa5ff",
    brightMagenta: "#e6b0ff",
    brightCyan: "#7ce7ec",
    brightWhite: "#ffffff",
  };
}

function applyTerminalTheme() {
  if (state.terminalInstance) state.terminalInstance.options.theme = terminalTheme();
  if (state.outputTerminalInstance) {
    state.outputTerminalInstance.options.theme = terminalTheme();
  }
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
  try {
    state.ws.send(JSON.stringify(message));
    return true;
  } catch (_) {
    showToast("发送失败", "浏览器无法提交这条消息，请减少附件大小后重试。", "error");
    return false;
  }
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
    if (state.activeView === "files") ensureWorkspaceFileBrowser();
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
      || Boolean(state.agentKeyPending)
      || state.interruptPending
      || state.startPending
      || state.directoryPending
      || state.fileListingPending
      || state.fileReadPending
      || state.fileDownloadPending
      || state.fileUploadPending
      || state.sshProfilePending;
    state.ws = null;
    state.promptPending = false;
    state.pendingPromptText = "";
    state.pendingPromptMode = "";
    state.pendingPromptImageIds = [];
    state.pendingPromptSubmissionId = 0;
    renderPromptAttachments();
    window.clearTimeout(state.agentKeyPendingTimer);
    state.agentKeyPendingTimer = null;
    window.clearTimeout(state.agentKeyFeedbackTimer);
    state.agentKeyPending = null;
    state.agentKeyFeedback = "";
    state.agentKeyFeedbackType = "";
    state.agentKeyFeedbackPane = "";
    state.interruptPending = false;
    state.startPending = false;
    state.directoryPending = false;
    state.fileListingPending = false;
    state.fileReadPending = false;
    state.fileDownloadPending = false;
    if (state.fileUploadPending && state.fileUploadActive) {
      state.fileUploadActive.entry.status = "error";
      state.fileUploadActive.entry.error = "Relay 连接中断，请重试";
    }
    state.fileUploadPending = false;
    state.fileUploadActive = null;
    state.fileListingRequestId += 1;
    state.fileReadRequestId += 1;
    state.fileDownloadRequestId += 1;
    state.sshProfilePending = false;
    resetTerminalResizeSync();
    state.terminalSessionId = null;
    state.terminalPending = false;
    state.terminalConnected = false;
    state.terminalPersistent = false;
    renderDirectoryBrowser();
    renderWorkspaceFileBrowser();
    renderWorkspaceFileViewer();
    renderWorkspaceUploadDialog();
    renderRemoteAccess();
    renderTerminalConnection();
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
      handleSession(message);
      break;
    case "agents":
      replaceAgentSnapshot(Array.isArray(message.agents) ? message.agents : []);
      break;
    case "agent_sources":
      handleAgentSources(Array.isArray(message.sources) ? message.sources : []);
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
    case "workspace_listing":
      handleWorkspaceListing(message);
      break;
    case "workspace_file":
      handleWorkspaceFile(message);
      break;
    case "workspace_download_ready":
      handleWorkspaceDownloadReady(message);
      break;
    case "workspace_upload_ready":
      handleWorkspaceUploadReady(message);
      break;
    case "workspace_upload_progress":
      handleWorkspaceUploadProgress(message);
      break;
    case "workspace_upload_committing":
      handleWorkspaceUploadCommitting(message);
      break;
    case "workspace_upload_complete":
      handleWorkspaceUploadComplete(message);
      break;
    case "workspace_upload_cancelled":
      handleWorkspaceUploadCancelled(message);
      break;
    case "workspace_upload_error":
      handleWorkspaceUploadError(message);
      break;
    case "workspace_error":
      handleWorkspaceError(message);
      break;
    case "agent_started":
      handleAgentStarted(message);
      break;
    case "terminal_profiles":
      handleTerminalProfiles(Array.isArray(message.profiles) ? message.profiles : []);
      break;
    case "terminal_opened":
      handleTerminalOpened(message);
      break;
    case "terminal_output":
      handleTerminalOutput(message);
      break;
    case "terminal_resized":
      handleTerminalResized(message);
      break;
    case "terminal_capture":
      handleTerminalCapture(message);
      break;
    case "terminal_exit":
      handleTerminalExit(message);
      break;
    case "terminal_error":
      handleTerminalError(message);
      break;
    case "command_result":
      handleCommandResult(message);
      break;
    case "push_subscribed":
    case "push_unsubscribed":
      renderPushStatus();
      break;
    case "error":
      handleRelayError(message);
      break;
    default:
      break;
  }
}

function replaceAgentSnapshot(agents) {
  const previous = new Map(state.agents.map((agent) => [agent.pane_id, agent]));
  const nextAgents = agents.map((agent) => {
    const old = previous.get(agent.pane_id) || {};
    const next = {
      ...old,
      ...agent,
    };
    const remainsBlocked = normalizedStatus(old) === "blocked"
      && normalizedStatus(next) === "blocked";
    if (!remainsBlocked) return clearBlockedPromptState(next);
    return {
      ...next,
      options: old.options,
      blockedPrompt: old.blockedPrompt,
      promptId: old.promptId,
      interaction: old.interaction,
      multi: old.multi,
      multiOptions: old.multiOptions,
      selectedOptions: old.selectedOptions,
    };
  });
  const changed = JSON.stringify(nextAgents) !== JSON.stringify(state.agents);
  state.agents = nextAgents;

  if (state.activePane && !activeAgent()) {
    state.activePane = null;
    state.agentKeyPanelOpen = false;
    state.userScrolledUp = false;
    if (state.promptImages.length) clearPromptImages();
    document.body.classList.remove("agent-open");
    updatePaneUrl("");
  }

  if (changed) renderAll();
  selectInitialAgentIfNeeded();
}

function upsertAgent(agent) {
  const index = state.agents.findIndex((item) => item.pane_id === agent.pane_id);
  const existing = index >= 0 ? state.agents[index] : {};
  let merged = { ...existing, ...agent };
  const enteredBlockedWithoutPrompt = normalizedStatus(existing) !== "blocked"
    && normalizedStatus(merged) === "blocked"
    && !agent.promptId;
  if (normalizedStatus(merged) !== "blocked" || enteredBlockedWithoutPrompt) {
    merged = clearBlockedPromptState(merged);
  }
  if (index >= 0) state.agents[index] = merged;
  else state.agents.push(merged);
  renderAll();
}

function clearBlockedPromptState(agent) {
  return {
    ...agent,
    options: [],
    blockedPrompt: "",
    promptId: "",
    interaction: "",
    multi: false,
    multiOptions: [],
    selectedOptions: [],
  };
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
    source_id: message.source_id || existing?.source_id || "local",
    status: "blocked",
    options: Array.isArray(message.options) ? message.options : [],
    multiOptions: Array.isArray(message.multi_options) ? message.multi_options : [],
    selectedOptions: Array.isArray(message.selected_options) ? message.selected_options : [],
    promptId: message.prompt_id || "",
    interaction: message.interaction || "prompt",
    multi: Boolean(message.multi),
    blockedPrompt: message.prompt || "",
  });
  if (!wasBlocked) {
    showToast("Agent 需要处理", `${message.project || "Agent"} 正在等待你的操作。`, "error");
  }
}

function handlePaneContent(message) {
  if (!message.pane_id) return;
  state.outputs.set(message.pane_id, String(message.content || ""));
  state.ansiOutputs.set(
    message.pane_id,
    typeof message.ansi_content === "string" ? message.ansi_content : "",
  );
  state.outputUpdatedAt.set(message.pane_id, new Date());
  if (message.pane_id === state.activePane) renderOutput();
}

function handleDirectoryListing(message) {
  if (message.source_id && message.source_id !== state.selectedSource) return;
  state.directoryPending = false;
  state.directory = message;
  if (message.can_start_agent && message.path) {
    state.selectedDirectory = {
      path: message.path,
      display_path: message.display_path || message.path,
      git_root: message.git_root || "",
      source_id: message.source_id || state.selectedSource,
    };
  } else {
    state.selectedDirectory = null;
  }
  renderDirectoryBrowser();
}


function handleWorkspaceListing(message) {
  if (message.source_id && message.source_id !== state.fileSource) return;
  if (message.request_id !== state.fileListingRequestId) return;
  state.fileListingPending = false;
  state.fileListingError = "";
  state.fileListing = message;
  renderWorkspaceFileBrowser();
}

function handleWorkspaceFile(message) {
  if (message.source_id && message.source_id !== state.fileSource) return;
  if (message.request_id !== state.fileReadRequestId) return;
  if (!state.activeFile || message.path !== state.activeFile.path) return;
  state.fileReadPending = false;
  state.activeFile = {
    ...state.activeFile,
    ...message,
    previewable: true,
    downloadable: state.activeFile.downloadable !== false,
    readError: "",
  };
  renderWorkspaceFileViewer();
}

function handleWorkspaceDownloadReady(message) {
  if (message.source_id && message.source_id !== state.fileSource) return;
  if (message.request_id !== state.fileDownloadRequestId) return;
  state.fileDownloadPending = false;
  renderWorkspaceFileViewer();
  if (!message.url || typeof message.url !== "string") {
    showToast("无法下载文件", "Relay 未返回有效的下载地址。", "error");
    return;
  }
  showToast("下载已准备", `${message.name || "文件"} 即将开始下载。`, "success");
  window.location.assign(message.url);
}

function handleWorkspaceError(message) {
  if (message.source_id && message.source_id !== state.fileSource) return;
  const operation = message.operation || "file";
  if (operation === "list") {
    if (message.request_id !== state.fileListingRequestId) return;
    state.fileListingPending = false;
    state.fileListingError = message.message || "Workspace 目录无法读取。";
    renderWorkspaceFileBrowser();
  } else if (operation === "read") {
    if (message.request_id !== state.fileReadRequestId) return;
    state.fileReadPending = false;
    if (state.activeFile) state.activeFile.readError = message.message || "文件无法预览";
    renderWorkspaceFileViewer();
  } else if (operation === "download") {
    if (message.request_id !== state.fileDownloadRequestId) return;
    state.fileDownloadPending = false;
    renderWorkspaceFileViewer();
  }
  showToast("文件操作失败", message.message || "Workspace 文件请求失败。", "error");
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

function handleSession(message) {
  state.session = message;
  state.terminalAuthorized = message.features?.terminal === true;
  state.nativeSshEnabled = message.features?.native_ssh === true;
  state.fileUploadSupported = message.features?.workspace_upload === true;
  const uploadMaxBytes = Number(message.limits?.workspace_upload_max_bytes);
  const uploadChunkBytes = Number(message.limits?.workspace_upload_chunk_bytes);
  state.fileUploadMaxBytes = Number.isSafeInteger(uploadMaxBytes) && uploadMaxBytes > 0
    ? uploadMaxBytes
    : WORKSPACE_UPLOAD_DEFAULT_MAX_BYTES;
  state.fileUploadChunkBytes = Number.isSafeInteger(uploadChunkBytes) && uploadChunkBytes > 0
    ? uploadChunkBytes
    : WORKSPACE_UPLOAD_DEFAULT_CHUNK_BYTES;
  state.machine = message.machine || {};
  state.terminalProfiles = Array.isArray(message.terminal_profiles)
    ? message.terminal_profiles
    : [];
  if (!fileSourceById(state.fileSource)) {
    const available = fileSources();
    state.fileSource = fileSourceById("local")?.id
      || available.find(fileSourceUsable)?.id
      || available[0]?.id
      || "local";
  }
  if (!workspaceFilesSupported()) {
    state.fileListing = null;
    state.fileListingPending = false;
    state.fileListingRequestId += 1;
    state.fileListingError = WORKSPACE_FILE_FEATURE_UNAVAILABLE;
    clearWorkspaceFileSelection(false);
  } else if (state.fileListingError === WORKSPACE_FILE_FEATURE_UNAVAILABLE) {
    state.fileListingError = "";
  }
  const requestedProfile = state.terminalRequestedProfile;
  if (
    state.activeView === "terminal"
    && !state.activeTerminalProfile
    && requestedProfile
    && terminalProfileById(requestedProfile)
  ) {
    state.activeTerminalProfile = requestedProfile;
    state.terminalShouldReconnect = true;
    document.body.classList.add("shell-open");
  }
  state.terminalRequestedProfile = null;
  if (requestedProfile && !terminalProfileById(requestedProfile)) updateAppUrl();
  renderSession();
  renderRemoteAccess();
  renderWorkspaceFileBrowser();
  renderWorkspaceFileViewer();
  if (state.activeView === "files" && workspaceFilesSupported()) {
    ensureWorkspaceFileBrowser();
  }

  if (
    state.activeView === "terminal"
    && state.terminalAuthorized
    && state.activeTerminalProfile
    && state.terminalShouldReconnect
    && !state.terminalPending
    && !state.terminalConnected
  ) {
    window.setTimeout(() => openTerminalProfile(state.activeTerminalProfile), 80);
  }
}

function terminalProfileById(profileId) {
  return state.terminalProfiles.find((profile) => profile.id === profileId) || null;
}

function agentSourceById(sourceId) {
  return state.agentSources.find((source) => source.id === sourceId) || null;
}

function fileSources() {
  const sources = new Map(state.agentSources.map((source) => [source.id, source]));
  if (state.terminalAuthorized) {
    for (const profile of state.terminalProfiles) {
      const existing = sources.get(profile.id);
      sources.set(profile.id, {
        ...profile,
        ...existing,
        terminal_profile: true,
        can_browse: true,
        status: existing?.status || "configured",
        error: existing?.error || "",
      });
    }
  }
  return [...sources.values()];
}

function fileSourceById(sourceId) {
  return fileSources().find((source) => source.id === sourceId) || null;
}

function agentSourceUsable(source) {
  return source?.status === "online" && source.can_start_agent !== false;
}

function fileSourceUsable(source) {
  return Boolean(
    source
    && source.can_browse !== false
    && (
      source.status === "online"
      || source.status === "configured"
      || source.terminal_profile === true
    )
  );
}

function workspaceFilesSupported() {
  return state.session?.features?.workspace_files === true;
}

function workspaceFilesUnavailable() {
  return Boolean(state.session) && !workspaceFilesSupported();
}

function handleAgentSources(sources) {
  const previousSource = state.selectedSource;
  const previousFileSource = state.fileSource;
  state.agentSources = sources;
  if (!agentSourceById(state.selectedSource)) {
    state.selectedSource = agentSourceById("local")?.id
      || sources.find(agentSourceUsable)?.id
      || sources[0]?.id
      || "local";
  }
  if (!fileSourceById(state.fileSource)) {
    const availableFileSources = fileSources();
    state.fileSource = fileSourceById("local")?.id
      || availableFileSources.find(fileSourceUsable)?.id
      || availableFileSources[0]?.id
      || "local";
  }
  renderAgentSourcePicker();
  renderFileSourcePicker();
  if (previousSource !== state.selectedSource && elements.newAgentDialog.open) {
    state.directory = null;
    state.selectedDirectory = null;
    browseDirectory(null);
  }
  if (previousFileSource !== state.fileSource) {
    state.fileListing = null;
    clearWorkspaceFileSelection();
  }
  if (state.activeView === "files") ensureWorkspaceFileBrowser();
  refreshActionAvailability();
}

function handleTerminalProfiles(profiles) {
  const previousFileSource = state.fileSource;
  state.terminalProfiles = profiles;
  const available = fileSources();
  if (!fileSourceById(state.fileSource)) {
    state.fileSource = fileSourceById("local")?.id
      || available.find(fileSourceUsable)?.id
      || available[0]?.id
      || "local";
  }
  if (previousFileSource !== state.fileSource) {
    state.fileListing = null;
    clearWorkspaceFileSelection(false);
  }
  renderRemoteAccess();
  renderFileSourcePicker();
  if (state.activeView === "files") ensureWorkspaceFileBrowser();
}

function renderAgentSourcePicker() {
  if (!elements.agentSourceSelect) return;
  elements.agentSourceSelect.replaceChildren();
  for (const source of state.agentSources) {
    const option = document.createElement("option");
    option.value = source.id;
    const stateLabel = source.status === "online"
      ? `在线 · ${source.agent_count || 0} Agents`
      : (source.status === "offline" ? "离线" : "检查中");
    option.textContent = `${source.label} · ${stateLabel}`;
    option.disabled = source.status === "offline";
    elements.agentSourceSelect.append(option);
  }
  if (agentSourceById(state.selectedSource)) {
    elements.agentSourceSelect.value = state.selectedSource;
  }
  const selected = agentSourceById(state.selectedSource);
  elements.agentSourceSelect.disabled = !state.agentSources.length || state.directoryPending;
  elements.agentSourceStatus.classList.toggle("is-offline", selected?.status === "offline");
  elements.agentSourceStatus.textContent = selected?.status === "online"
    ? `${selected.kind === "local" ? "本机" : "SSH"} Agent Source 已就绪`
    : (selected?.error || "正在等待 Agent Source 健康检查…");
  elements.launchSource.textContent = selected?.label || "本机";
}

function renderFileSourcePicker() {
  const sources = fileSources();
  elements.fileSourceSelect.replaceChildren();
  for (const source of sources) {
    const option = document.createElement("option");
    option.value = source.id;
    const stateLabel = source.status === "online"
      ? `在线 · ${source.kind === "local" ? "本机" : "SSH"}`
      : (source.terminal_profile
        ? `按需连接 · ${source.kind === "local" ? "本机" : "SSH"}`
        : (source.status === "offline" ? "离线" : "检查中"));
    option.textContent = `${source.label} · ${stateLabel}`;
    option.disabled = !fileSourceUsable(source);
    elements.fileSourceSelect.append(option);
  }
  if (fileSourceById(state.fileSource)) elements.fileSourceSelect.value = state.fileSource;
  const selected = fileSourceById(state.fileSource);
  const featureUnavailable = workspaceFilesUnavailable();
  elements.fileSourceSelect.disabled = !sources.length
    || state.fileListingPending
    || featureUnavailable;
  elements.fileSourceStatus.classList.toggle(
    "is-offline",
    selected?.status === "offline" && !selected?.terminal_profile,
  );
  elements.fileSourceStatus.textContent = featureUnavailable
    ? "Relay 后台需要重启后才能使用 Files"
    : (fileSourceUsable(selected)
      ? `${selected.kind === "local" ? "本机" : "SSH"} Workspace ${
        state.fileUploadSupported ? "可浏览与上传" : "可浏览"
      }`
      : (selected?.error || "正在等待主机健康检查…"));
}

function nativeSshCommand() {
  const username = state.machine?.username;
  const host = state.machine?.tailscale_dns || state.machine?.hostname;
  return username && host ? `tailscale ssh ${username}@${host}` : "";
}

function proxyJumpCommand(profile) {
  if (!profile) return "";
  if (profile.kind === "local") return nativeSshCommand();
  const username = state.machine?.username;
  const gateway = state.machine?.tailscale_dns || state.machine?.hostname;
  if (!username || !gateway) return "";
  const port = Number(profile.port) === 22 ? "" : ` -p ${profile.port}`;
  return `ssh -J ${username}@${gateway}${port} ${profile.target}`;
}

function setAppView(view, updateHistory = true) {
  state.activeView = ["terminal", "files"].includes(view) ? view : "agents";
  const terminalActive = state.activeView === "terminal";
  const filesActive = state.activeView === "files";
  elements.agentWorkspace.hidden = terminalActive || filesActive;
  elements.terminalWorkspace.hidden = !terminalActive;
  elements.fileWorkspace.hidden = !filesActive;
  document.body.classList.toggle("terminal-view", terminalActive);
  document.body.classList.toggle("files-view", filesActive);
  document.querySelectorAll("[data-app-view]").forEach((button) => {
    const active = button.dataset.appView === state.activeView;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  if (updateHistory) updateAppUrl();
  if (terminalActive) {
    renderRemoteAccess();
    window.requestAnimationFrame(fitWebTerminal);
  } else if (filesActive) {
    state.terminalInstance?.blur();
    renderWorkspaceFileBrowser();
    renderWorkspaceFileViewer();
    ensureWorkspaceFileBrowser();
  } else {
    state.terminalInstance?.blur();
    selectInitialAgentIfNeeded();
  }
  if (state.visualViewportBaselineHeight) syncVisualViewportLayout();
}

function updateAppUrl() {
  const url = new URL(window.location.href);
  url.searchParams.delete("token");
  if (state.activeView === "terminal") {
    url.searchParams.set("view", "terminal");
    if (state.activeTerminalProfile) url.searchParams.set("terminal", state.activeTerminalProfile);
    else url.searchParams.delete("terminal");
  } else if (state.activeView === "files") {
    url.searchParams.set("view", "files");
    url.searchParams.delete("terminal");
  } else {
    url.searchParams.delete("view");
    url.searchParams.delete("terminal");
  }
  history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

function attachTerminalCopyShortcut(terminal, label, clearAfterCopy = false) {
  terminal.attachCustomKeyEventHandler((event) => {
    const copyKey = event.type === "keydown"
      && String(event.key || "").toLowerCase() === "c"
      && (event.ctrlKey || event.metaKey)
      && !event.altKey;
    if (!copyKey) return true;
    const selection = terminal.getSelection();
    if (!selection) return true;
    event.preventDefault();
    copyText(selection, "已复制选区", `${label}已复制到剪贴板。`).then((copied) => {
      if (copied && clearAfterCopy) clearAgentOutputSelection(terminal);
    });
    return false;
  });
}

function ensureWebTerminal() {
  if (state.terminalInstance) return true;
  if (typeof window.Terminal !== "function" || typeof window.FitAddon?.FitAddon !== "function") {
    showToast("终端组件不可用", "请刷新页面以重新加载本地 xterm.js 资源。", "error");
    return false;
  }
  const terminal = new window.Terminal({
    cursorBlink: true,
    cursorStyle: "bar",
    fontFamily: document.fonts?.check('400 13px "Herdr FiraCode Nerd"')
      ? TERMINAL_FONT_FAMILY
      : TERMINAL_FALLBACK_FONT_FAMILY,
    fontSize: isDesktop() ? 13 : 12,
    lineHeight: 1.25,
    scrollback: 6000,
    theme: terminalTheme(),
    allowTransparency: false,
    macOptionIsMeta: true,
  });
  const fitAddon = new window.FitAddon.FitAddon();
  terminal.loadAddon(fitAddon);
  terminal.open(elements.webTerminal);
  if (terminal.textarea) terminal.textarea.setAttribute("enterkeyhint", "enter");
  attachTerminalCopyShortcut(terminal, "Shell 终端选区");
  enableWebTerminalTouchScrolling(terminal);
  terminal.onData((data) => sendTerminalText(applyTerminalModifiersToInput(data)));
  state.terminalInstance = terminal;
  state.terminalFitAddon = fitAddon;

  terminalFontReady.then(() => {
    if (state.terminalInstance !== terminal) return;
    terminal.options.fontFamily = TERMINAL_FONT_FAMILY;
    terminal.refresh(0, Math.max(0, terminal.rows - 1));
    scheduleWebTerminalFit();
  });

  if ("ResizeObserver" in window) {
    state.terminalResizeObserver = new ResizeObserver(() => scheduleWebTerminalFit());
    state.terminalResizeObserver.observe(elements.webTerminal);
  }
  window.requestAnimationFrame(fitWebTerminal);
  return true;
}

function browserSelectionInside(element) {
  const selection = window.getSelection?.();
  return Boolean(
    selection
    && !selection.isCollapsed
    && selection.anchorNode
    && selection.focusNode
    && element.contains(selection.anchorNode)
    && element.contains(selection.focusNode),
  );
}

function clearBrowserSelectionInside(element) {
  const selection = window.getSelection?.();
  if (!selection || !element) return;
  const anchorInside = selection.anchorNode && element.contains(selection.anchorNode);
  const focusInside = selection.focusNode && element.contains(selection.focusNode);
  if (anchorInside || focusInside) selection.removeAllRanges();
}

function browserSelectionTextInside(element) {
  return browserSelectionInside(element) ? window.getSelection().toString() : "";
}

function agentOutputAccessibilityTree(terminal) {
  return terminal?.element?.querySelector(
    ".xterm-accessibility:not(.agent-output-selection-layer) .xterm-accessibility-tree",
  ) || null;
}

function agentOutputSelectionTree(terminal) {
  const nativeController = nativeAgentSelectionControllers.get(terminal);
  return nativeController?.selectionTree() || agentOutputAccessibilityTree(terminal);
}

function agentOutputSelectionBoundary(selectionTree, container, offset, endBoundary = false) {
  if (!selectionTree || !container || !selectionTree.children.length) return null;
  if (container === selectionTree) {
    const rawIndex = endBoundary ? offset - 1 : offset;
    const rowIndex = Math.max(0, Math.min(selectionTree.children.length - 1, rawIndex));
    const row = selectionTree.children[rowIndex];
    return {
      row,
      rowIndex,
      textOffset: endBoundary ? (row.textContent || "").length : 0,
    };
  }

  let row = container.nodeType === Node.ELEMENT_NODE ? container : container.parentElement;
  while (row && row.parentElement !== selectionTree) row = row.parentElement;
  if (!row) return null;
  const rowIndex = Array.prototype.indexOf.call(selectionTree.children, row);
  if (rowIndex < 0) return null;

  const prefix = document.createRange();
  try {
    prefix.selectNodeContents(row);
    prefix.setEnd(container, offset);
  } catch (_) {
    return null;
  }
  return {
    row,
    rowIndex,
    textOffset: Math.max(0, Math.min((row.textContent || "").length, prefix.toString().length)),
  };
}

function selectedAgentOutputText(terminal, selectionTree) {
  const selection = window.getSelection?.();
  if (
    !selectionTree
    || !selection
    || selection.isCollapsed
    || selection.rangeCount < 1
    || !browserSelectionInside(selectionTree)
  ) return "";

  const range = selection.getRangeAt(0);
  const start = agentOutputSelectionBoundary(
    selectionTree,
    range.startContainer,
    range.startOffset,
  );
  const end = agentOutputSelectionBoundary(
    selectionTree,
    range.endContainer,
    range.endOffset,
    true,
  );
  if (!start || !end || start.rowIndex > end.rowIndex) return selection.toString();

  const rows = selectionTree.children;
  const parts = [];
  for (let rowIndex = start.rowIndex; rowIndex <= end.rowIndex; rowIndex += 1) {
    const row = rows[rowIndex];
    const rawRowText = row?.textContent || "";
    const rowText = rawRowText === "\u00a0" ? "" : rawRowText;
    const from = rowIndex === start.rowIndex ? start.textOffset : 0;
    const to = rowIndex === end.rowIndex ? end.textOffset : rowText.length;
    parts.push(rowText.slice(from, to));
    if (rowIndex >= end.rowIndex) continue;

    const nextRow = rows[rowIndex + 1];
    const nextBufferRow = Number(nextRow?.getAttribute("aria-posinset")) - 1;
    const nextBufferLine = Number.isInteger(nextBufferRow)
      ? terminal?.buffer?.active?.getLine(nextBufferRow)
      : null;
    if (!nextBufferLine?.isWrapped) parts.push("\n");
  }
  return parts.join("");
}

function agentOutputHasSelection(terminal) {
  const nativeController = nativeAgentSelectionControllers.get(terminal);
  const selectionTree = agentOutputSelectionTree(terminal);
  return Boolean(
    terminal?.hasSelection()
    || nativeController?.hasSelection()
    || (selectionTree && browserSelectionInside(selectionTree)),
  );
}

function agentOutputSelectionLocked(terminal) {
  const nativeController = nativeAgentSelectionControllers.get(terminal);
  return Boolean(nativeController?.locksOutput() || agentOutputHasSelection(terminal));
}

function updateAgentOutputSelectionState(terminal) {
  if (!terminal || (state.outputTerminalInstance && state.outputTerminalInstance !== terminal)) return;
  const selected = agentOutputHasSelection(terminal);
  elements.copyOutputButton.classList.toggle("has-selection", selected);
  elements.copyOutputButton.setAttribute(
    "aria-label",
    selected ? "复制已选择的 Agent 输出" : "选择或复制 Agent 输出",
  );
  if (!selected && !agentOutputSelectionLocked(terminal)) {
    window.requestAnimationFrame(() => {
      if (!agentOutputSelectionLocked(terminal)) renderOutput();
    });
  }
}

function clearAgentOutputSelection(terminal) {
  if (!terminal) return;
  const selectionTree = agentOutputSelectionTree(terminal);
  const nativeController = nativeAgentSelectionControllers.get(terminal);
  if (nativeController) nativeController.clear();
  else if (selectionTree) clearBrowserSelectionInside(selectionTree);
  if (terminal.hasSelection()) terminal.clearSelection();
  updateAgentOutputSelectionState(terminal);
}

function enableNativeAgentOutputSelection(terminal, surface, selectionTree) {
  surface.classList.add("use-native-touch-selection");

  const accessibilityLayer = selectionTree.parentElement;
  if (!accessibilityLayer) return;
  const selectionLayer = accessibilityLayer.cloneNode(false);
  selectionLayer.classList.add("agent-output-selection-layer");
  selectionLayer.setAttribute("aria-hidden", "true");
  accessibilityLayer.insertAdjacentElement("afterend", selectionLayer);

  let stableSelectionTree = null;
  let selectionLayerRefreshFrame = null;
  let preserveNativeSelectionUntil = 0;
  let lastNativeSelectionRange = null;
  let restoreSelectionFrame = null;
  let releaseSelectionTimer = null;
  let touchLocksOutput = false;

  const nativeSelectionInside = () => Boolean(
    stableSelectionTree && browserSelectionInside(stableSelectionTree),
  );

  const locksOutput = () => Boolean(
    touchLocksOutput
    || nativeSelectionInside()
    || Date.now() < preserveNativeSelectionUntil,
  );

  const cancelSelectionLayerRefresh = () => {
    if (selectionLayerRefreshFrame === null) return;
    window.cancelAnimationFrame(selectionLayerRefreshFrame);
    selectionLayerRefreshFrame = null;
  };

  const refreshSelectionLayer = () => {
    cancelSelectionLayerRefresh();
    if (locksOutput()) return false;
    selectionLayer.style.cssText = accessibilityLayer.style.cssText;
    const clonedTree = selectionTree.cloneNode(false);
    clonedTree.classList.add("agent-output-selection-tree");
    const buffer = terminal.buffer.active;
    for (let viewportRow = 0; viewportRow < terminal.rows; viewportRow += 1) {
      const sourceRow = selectionTree.children[viewportRow];
      const clonedRow = sourceRow?.cloneNode(false) || document.createElement("div");
      const bufferRow = buffer.viewportY + viewportRow;
      const lineText = buffer.getLine(bufferRow)?.translateToString(true) || "";
      clonedRow.textContent = lineText || "\u00a0";
      clonedRow.setAttribute("role", "listitem");
      clonedRow.setAttribute("aria-posinset", String(bufferRow + 1));
      clonedRow.setAttribute("aria-setsize", String(buffer.length));
      clonedRow.removeAttribute("tabindex");
      clonedTree.append(clonedRow);
    }
    selectionLayer.replaceChildren(clonedTree);
    stableSelectionTree = clonedTree;
    return true;
  };

  const scheduleSelectionLayerRefresh = () => {
    cancelSelectionLayerRefresh();
    if (locksOutput()) return;
    selectionLayerRefreshFrame = window.requestAnimationFrame(() => {
      selectionLayerRefreshFrame = window.requestAnimationFrame(() => {
        selectionLayerRefreshFrame = null;
        refreshSelectionLayer();
      });
    });
  };

  const rememberNativeSelection = () => {
    const selection = window.getSelection?.();
    if (
      !stableSelectionTree
      || !selection
      || selection.rangeCount < 1
      || !browserSelectionInside(stableSelectionTree)
    ) return;
    lastNativeSelectionRange = selection.getRangeAt(0).cloneRange();
  };

  const releaseSelectionLockLater = () => {
    if (releaseSelectionTimer !== null) window.clearTimeout(releaseSelectionTimer);
    const delay = Math.max(0, preserveNativeSelectionUntil - Date.now()) + 32;
    releaseSelectionTimer = window.setTimeout(() => {
      releaseSelectionTimer = null;
      if (nativeSelectionInside()) return;
      preserveNativeSelectionUntil = 0;
      lastNativeSelectionRange = null;
      scheduleSelectionLayerRefresh();
      updateAgentOutputSelectionState(terminal);
    }, delay);
  };

  const preserveNativeSelection = () => {
    preserveNativeSelectionUntil = Date.now() + NATIVE_SELECTION_RELEASE_GRACE_MS;
    rememberNativeSelection();
    releaseSelectionLockLater();
  };

  const restoreNativeSelection = () => {
    restoreSelectionFrame = null;
    if (Date.now() >= preserveNativeSelectionUntil || !lastNativeSelectionRange) return;
    const selection = window.getSelection?.();
    if (!selection || !selection.isCollapsed) return;
    if (
      !lastNativeSelectionRange.startContainer.isConnected
      || !lastNativeSelectionRange.endContainer.isConnected
      || !stableSelectionTree?.contains(lastNativeSelectionRange.startContainer)
      || !stableSelectionTree?.contains(lastNativeSelectionRange.endContainer)
    ) {
      preserveNativeSelectionUntil = 0;
      lastNativeSelectionRange = null;
      scheduleSelectionLayerRefresh();
      updateAgentOutputSelectionState(terminal);
      return;
    }
    selection.removeAllRanges();
    selection.addRange(lastNativeSelectionRange.cloneRange());
  };

  const nativeController = {
    clear() {
      touchLocksOutput = false;
      preserveNativeSelectionUntil = 0;
      lastNativeSelectionRange = null;
      if (releaseSelectionTimer !== null) {
        window.clearTimeout(releaseSelectionTimer);
        releaseSelectionTimer = null;
      }
      if (restoreSelectionFrame !== null) {
        window.cancelAnimationFrame(restoreSelectionFrame);
        restoreSelectionFrame = null;
      }
      if (stableSelectionTree) clearBrowserSelectionInside(stableSelectionTree);
      scheduleSelectionLayerRefresh();
    },
    hasSelection: nativeSelectionInside,
    locksOutput,
    selectedText() {
      return selectedAgentOutputText(terminal, stableSelectionTree);
    },
    selectionTree() {
      return stableSelectionTree;
    },
    refresh: scheduleSelectionLayerRefresh,
  };
  nativeAgentSelectionControllers.set(terminal, nativeController);
  refreshSelectionLayer();

  const keepNativeSelectionEvent = (event) => {
    if (event.target instanceof Node && selectionLayer.contains(event.target)) {
      if (event.type === "contextmenu") preserveNativeSelection();
      event.stopPropagation();
    }
  };
  selectionLayer.addEventListener("mousedown", keepNativeSelectionEvent);
  selectionLayer.addEventListener("mouseup", keepNativeSelectionEvent);
  selectionLayer.addEventListener("click", keepNativeSelectionEvent);
  selectionLayer.addEventListener("contextmenu", keepNativeSelectionEvent);
  document.addEventListener("selectionchange", () => {
    const selection = window.getSelection?.();
    if (nativeSelectionInside()) {
      rememberNativeSelection();
      updateAgentOutputSelectionState(terminal);
      return;
    }
    if (Date.now() < preserveNativeSelectionUntil) {
      if (
        (!selection || selection.isCollapsed)
        && restoreSelectionFrame === null
        && lastNativeSelectionRange
      ) {
        restoreSelectionFrame = window.requestAnimationFrame(restoreNativeSelection);
        return;
      }
      if (!lastNativeSelectionRange) return;
    }
    preserveNativeSelectionUntil = 0;
    lastNativeSelectionRange = null;
    if (terminal.hasSelection()) terminal.clearSelection();
    scheduleSelectionLayerRefresh();
    updateAgentOutputSelectionState(terminal);
  });
  document.addEventListener("copy", (event) => {
    const selectedText = nativeController.selectedText();
    if (selectedText && event.clipboardData) {
      try {
        event.clipboardData.setData("text/plain", selectedText);
        event.preventDefault();
      } catch (_) {
        // Keep the browser's native copy behavior when custom clipboard data is unavailable.
      }
    }
    preserveNativeSelectionUntil = 0;
  }, { capture: true });

  terminal.onRender(scheduleSelectionLayerRefresh);
  terminal.onScroll(scheduleSelectionLayerRefresh);
  terminal.onResize(scheduleSelectionLayerRefresh);
  if ("MutationObserver" in window) {
    const accessibilityObserver = new MutationObserver(scheduleSelectionLayerRefresh);
    accessibilityObserver.observe(selectionTree, {
      attributes: true,
      characterData: true,
      childList: true,
      subtree: true,
    });
  }

  let originX = null;
  let originY = null;
  let lastTouchY = null;
  let gestureAxis = null;
  let pixelRemainder = 0;
  let nativeHoldReady = false;
  let nativeHoldTimer = null;
  let nativeSelectionMoved = false;
  let touchStartedWithNativeSelection = false;

  const hasRememberedNativeSelection = () => Boolean(
    nativeSelectionInside()
    || (
      lastNativeSelectionRange
      && Date.now() < preserveNativeSelectionUntil
    )
  );

  const clearNativeHoldTimer = () => {
    if (nativeHoldTimer === null) return;
    window.clearTimeout(nativeHoldTimer);
    nativeHoldTimer = null;
  };

  const resetTouch = () => {
    clearNativeHoldTimer();
    originX = null;
    originY = null;
    lastTouchY = null;
    gestureAxis = null;
    pixelRemainder = 0;
    nativeHoldReady = false;
    nativeSelectionMoved = false;
    touchStartedWithNativeSelection = false;
  };

  selectionLayer.addEventListener("touchstart", (event) => {
    if (event.touches.length !== 1) {
      touchLocksOutput = false;
      resetTouch();
      scheduleSelectionLayerRefresh();
      return;
    }
    touchStartedWithNativeSelection = hasRememberedNativeSelection();
    touchLocksOutput = true;
    cancelSelectionLayerRefresh();
    const touch = event.touches[0];
    originX = touch.clientX;
    originY = touch.clientY;
    lastTouchY = touch.clientY;
    gestureAxis = null;
    pixelRemainder = 0;
    nativeHoldReady = false;
    nativeSelectionMoved = false;
    clearNativeHoldTimer();
    nativeHoldTimer = window.setTimeout(() => {
      nativeHoldTimer = null;
      nativeHoldReady = true;
    }, AGENT_TOUCH_SELECTION_DELAY);
    event.stopPropagation();
  }, { passive: true });

  selectionLayer.addEventListener("touchmove", (event) => {
    if (lastTouchY === null || event.touches.length !== 1) {
      touchLocksOutput = false;
      resetTouch();
      scheduleSelectionLayerRefresh();
      return;
    }

    const touch = event.touches[0];
    const deltaY = lastTouchY - touch.clientY;
    const totalX = Math.abs(originX - touch.clientX);
    const totalY = Math.abs(originY - touch.clientY);
    lastTouchY = touch.clientY;
    if (nativeSelectionInside() && Math.max(totalX, totalY) >= 2) {
      nativeSelectionMoved = true;
    }
    event.stopPropagation();

    if (nativeHoldReady || nativeSelectionInside()) return;
    if (gestureAxis === null && Math.max(totalX, totalY) >= AGENT_TOUCH_MOVE_THRESHOLD) {
      clearNativeHoldTimer();
      gestureAxis = totalY > totalX ? "vertical" : "horizontal";
      if (gestureAxis !== null) touchLocksOutput = false;
    }
    if (gestureAxis !== "vertical" || !deltaY) return;

    const buffer = terminal.buffer.active;
    const canScroll = deltaY > 0
      ? buffer.viewportY < buffer.baseY
      : buffer.viewportY > 0;
    if (!canScroll) {
      pixelRemainder = 0;
      return;
    }

    event.preventDefault();
    if (terminal.hasSelection()) terminal.clearSelection();
    const screenHeight = surface.querySelector(".xterm-screen")?.clientHeight
      || surface.clientHeight;
    const lineHeight = Math.max(1, screenHeight / Math.max(1, terminal.rows));
    pixelRemainder += deltaY;
    const lineDelta = Math.trunc(pixelRemainder / lineHeight);
    if (!lineDelta) return;
    terminal.scrollLines(lineDelta);
    pixelRemainder -= lineDelta * lineHeight;
    scheduleSelectionLayerRefresh();
  }, { passive: false });

  const finishTouch = (event) => {
    const cancelExistingSelection = event.type === "touchend"
      && touchStartedWithNativeSelection
      && !nativeHoldReady
      && !nativeSelectionMoved
      && gestureAxis === null;
    if (cancelExistingSelection) {
      if (event.cancelable) event.preventDefault();
      touchLocksOutput = false;
      resetTouch();
      nativeController.clear();
      event.stopPropagation();
      updateAgentOutputSelectionState(terminal);
      return;
    }
    if (nativeHoldReady || nativeSelectionMoved || nativeSelectionInside()) {
      preserveNativeSelection();
    }
    touchLocksOutput = false;
    resetTouch();
    event.stopPropagation();
    if (!locksOutput()) {
      scheduleSelectionLayerRefresh();
      updateAgentOutputSelectionState(terminal);
    }
  };
  selectionLayer.addEventListener("touchend", finishTouch, { passive: false });
  selectionLayer.addEventListener("touchcancel", finishTouch, { passive: true });
}

function enableAgentOutputTouchScrolling(terminal) {
  const surface = terminal.element;
  if (!surface) return;

  const selectionTree = agentOutputAccessibilityTree(terminal);
  const nativeTouchSelection = Boolean(
    selectionTree
    && navigator.maxTouchPoints > 0
    && window.matchMedia("(pointer: coarse)").matches
    && typeof window.getSelection === "function",
  );
  if (nativeTouchSelection) {
    enableNativeAgentOutputSelection(terminal, surface, selectionTree);
    return;
  }

  let originX = null;
  let originY = null;
  let lastTouchY = null;
  let lastSelectionTouch = null;
  let selectionAnchor = null;
  let gestureMode = null;
  let selectionTimer = null;
  let selectionScrollTimer = null;
  let selectionScrollDirection = 0;
  let suppressContextMenuUntil = 0;
  let pixelRemainder = 0;

  const clearSelectionTimer = () => {
    if (selectionTimer === null) return;
    window.clearTimeout(selectionTimer);
    selectionTimer = null;
  };

  const stopSelectionAutoScroll = () => {
    if (selectionScrollTimer !== null) window.clearInterval(selectionScrollTimer);
    selectionScrollTimer = null;
    selectionScrollDirection = 0;
  };

  const terminalCellFromPoint = (clientX, clientY) => {
    const screen = surface.querySelector(".xterm-screen");
    const buffer = terminal.buffer.active;
    if (!screen || !buffer.length || !terminal.cols || !terminal.rows) return null;
    const rect = screen.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const column = Math.max(0, Math.min(
      terminal.cols - 1,
      Math.floor((clientX - rect.left) / (rect.width / terminal.cols)),
    ));
    const viewportRow = Math.max(0, Math.min(
      terminal.rows - 1,
      Math.floor((clientY - rect.top) / (rect.height / terminal.rows)),
    ));
    return {
      column,
      row: Math.max(0, Math.min(buffer.length - 1, buffer.viewportY + viewportRow)),
    };
  };

  const selectAgentOutputRange = (anchor, focus) => {
    if (!anchor || !focus) return;
    const anchorOffset = anchor.row * terminal.cols + anchor.column;
    const focusOffset = focus.row * terminal.cols + focus.column;
    const startOffset = Math.min(anchorOffset, focusOffset);
    terminal.select(
      startOffset % terminal.cols,
      Math.floor(startOffset / terminal.cols),
      Math.max(1, Math.abs(focusOffset - anchorOffset) + 1),
    );
  };

  const updateTouchSelection = () => {
    if (!selectionAnchor || !lastSelectionTouch) return;
    selectAgentOutputRange(
      selectionAnchor,
      terminalCellFromPoint(lastSelectionTouch.clientX, lastSelectionTouch.clientY),
    );
  };

  const setSelectionAutoScroll = (direction) => {
    if (selectionScrollDirection === direction) return;
    stopSelectionAutoScroll();
    if (!direction) return;
    selectionScrollDirection = direction;
    selectionScrollTimer = window.setInterval(() => {
      const buffer = terminal.buffer.active;
      const before = buffer.viewportY;
      terminal.scrollLines(direction);
      if (buffer.viewportY === before) {
        stopSelectionAutoScroll();
        return;
      }
      updateTouchSelection();
    }, AGENT_TOUCH_SELECTION_SCROLL_INTERVAL);
  };

  const updateSelectionAutoScroll = (clientY) => {
    const screen = surface.querySelector(".xterm-screen");
    if (!screen) return stopSelectionAutoScroll();
    const rect = screen.getBoundingClientRect();
    const edge = Math.min(AGENT_TOUCH_SELECTION_EDGE, rect.height / 4);
    if (clientY < rect.top + edge) setSelectionAutoScroll(-1);
    else if (clientY > rect.bottom - edge) setSelectionAutoScroll(1);
    else stopSelectionAutoScroll();
  };

  const resetTouch = () => {
    clearSelectionTimer();
    stopSelectionAutoScroll();
    originX = null;
    originY = null;
    lastTouchY = null;
    lastSelectionTouch = null;
    selectionAnchor = null;
    gestureMode = null;
    pixelRemainder = 0;
    surface.classList.remove("is-touch-selecting");
  };

  surface.addEventListener("touchstart", (event) => {
    if (event.touches.length !== 1) {
      resetTouch();
      return;
    }
    const touch = event.touches[0];
    originX = touch.clientX;
    originY = touch.clientY;
    lastTouchY = touch.clientY;
    lastSelectionTouch = {clientX: touch.clientX, clientY: touch.clientY};
    selectionAnchor = terminalCellFromPoint(touch.clientX, touch.clientY);
    gestureMode = "pending";
    pixelRemainder = 0;
    clearSelectionTimer();
    selectionTimer = window.setTimeout(() => {
      selectionTimer = null;
      if (gestureMode !== "pending" || !selectionAnchor) return;
      gestureMode = "selection";
      suppressContextMenuUntil = Date.now() + 1500;
      surface.classList.add("is-touch-selecting");
      terminal.clearSelection();
      selectAgentOutputRange(selectionAnchor, selectionAnchor);
    }, AGENT_TOUCH_SELECTION_DELAY);
    event.stopPropagation();
  }, { passive: true });

  surface.addEventListener("touchmove", (event) => {
    if (lastTouchY === null || event.touches.length !== 1) {
      resetTouch();
      return;
    }

    const touch = event.touches[0];
    const currentY = touch.clientY;
    const deltaY = lastTouchY - currentY;
    const totalX = Math.abs(originX - touch.clientX);
    const totalY = Math.abs(originY - touch.clientY);
    lastTouchY = currentY;
    event.stopPropagation();

    if (gestureMode === "pending" && Math.max(totalX, totalY) >= AGENT_TOUCH_MOVE_THRESHOLD) {
      clearSelectionTimer();
      gestureMode = "scroll";
      terminal.clearSelection();
    }

    if (gestureMode === "selection") {
      event.preventDefault();
      suppressContextMenuUntil = Date.now() + 1000;
      lastSelectionTouch = {clientX: touch.clientX, clientY: touch.clientY};
      updateTouchSelection();
      updateSelectionAutoScroll(touch.clientY);
      return;
    }

    if (gestureMode !== "scroll") return;
    if (!deltaY) return;

    const buffer = terminal.buffer.active;
    const canScroll = deltaY > 0
      ? buffer.viewportY < buffer.baseY
      : buffer.viewportY > 0;
    if (!canScroll) {
      pixelRemainder = 0;
      return;
    }

    event.preventDefault();
    const screenHeight = surface.querySelector(".xterm-screen")?.clientHeight
      || surface.clientHeight;
    const lineHeight = Math.max(1, screenHeight / Math.max(1, terminal.rows));
    pixelRemainder += deltaY;
    const lineDelta = Math.trunc(pixelRemainder / lineHeight);
    if (!lineDelta) return;
    terminal.scrollLines(lineDelta);
    pixelRemainder -= lineDelta * lineHeight;
  }, { passive: false });

  const finishTouch = (event) => {
    const selected = gestureMode === "selection" ? terminal.getSelection() : "";
    const openSelectionCopyDialog = event.type === "touchend" && Boolean(selected);
    if (gestureMode === "selection" || gestureMode === "pending") {
      event.preventDefault();
    }
    if (gestureMode === "selection") {
      suppressContextMenuUntil = Date.now() + 1000;
      if (event.type === "touchcancel") terminal.clearSelection();
    } else if (gestureMode === "pending") {
      terminal.clearSelection();
    }
    resetTouch();
    event.stopPropagation();
    if (openSelectionCopyDialog) {
      openTerminalCopyDialog({
        title: "复制选中的 Agent 输出",
        description: "已保留刚才长按拖动选择的内容，点击“复制选中”即可写入剪贴板。",
        content: selected,
        source: "agent-selection",
        status: `${selected.split("\n").length} 行 · 已选择 ${selected.length} 个字符`,
      });
    }
  };
  surface.addEventListener("touchend", finishTouch, { passive: false });
  surface.addEventListener("touchcancel", finishTouch, { passive: false });
  surface.addEventListener("contextmenu", (event) => {
    if (Date.now() >= suppressContextMenuUntil) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, {capture: true});
}

function enableWebTerminalTouchScrolling(terminal) {
  const surface = terminal.element;
  if (!surface || typeof window.WheelEvent !== "function") return;

  let originX = null;
  let originY = null;
  let lastTouchY = null;
  let gestureAxis = null;
  let pixelRemainder = 0;
  const resetTouch = () => {
    originX = null;
    originY = null;
    lastTouchY = null;
    gestureAxis = null;
    pixelRemainder = 0;
  };

  surface.addEventListener("touchstart", (event) => {
    if (event.touches.length !== 1) {
      resetTouch();
      return;
    }
    const touch = event.touches[0];
    originX = touch.clientX;
    originY = touch.clientY;
    lastTouchY = touch.clientY;
    gestureAxis = null;
    pixelRemainder = 0;
    event.stopPropagation();
  }, { passive: true });

  surface.addEventListener("touchmove", (event) => {
    if (lastTouchY === null || event.touches.length !== 1) {
      resetTouch();
      return;
    }

    const touch = event.touches[0];
    const deltaY = lastTouchY - touch.clientY;
    const totalX = Math.abs(originX - touch.clientX);
    const totalY = Math.abs(originY - touch.clientY);
    lastTouchY = touch.clientY;
    event.stopPropagation();

    if (gestureAxis === null && Math.max(totalX, totalY) >= 4) {
      gestureAxis = totalY > totalX ? "vertical" : "horizontal";
    }
    if (gestureAxis !== "vertical" || !deltaY) return;

    event.preventDefault();

    const buffer = terminal.buffer.active;
    if (terminal.modes.mouseTrackingMode === "none" && buffer.baseY > 0) {
      const screenHeight = surface.querySelector(".xterm-screen")?.clientHeight
        || surface.clientHeight;
      const lineHeight = Math.max(1, screenHeight / Math.max(1, terminal.rows));
      pixelRemainder += deltaY;
      const lineDelta = Math.trunc(pixelRemainder / lineHeight);
      if (!lineDelta) return;
      terminal.scrollLines(lineDelta);
      pixelRemainder -= lineDelta * lineHeight;
      return;
    }

    pixelRemainder = 0;
    surface.dispatchEvent(new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      composed: true,
      clientX: touch.clientX,
      clientY: touch.clientY,
      screenX: touch.screenX,
      screenY: touch.screenY,
      deltaX: 0,
      deltaY,
      deltaMode: 0,
      ctrlKey: event.ctrlKey,
      altKey: event.altKey,
      shiftKey: event.shiftKey,
      metaKey: event.metaKey,
    }));
  }, { passive: false });

  const finishTouch = (event) => {
    resetTouch();
    event.stopPropagation();
  };
  surface.addEventListener("touchend", finishTouch, { passive: true });
  surface.addEventListener("touchcancel", finishTouch, { passive: true });
}

function agentOutputScrollMetrics() {
  const terminal = state.outputTerminalInstance;
  if (terminal && !elements.agentOutputTerminal.hidden) {
    const buffer = terminal.buffer.active;
    return {
      kind: "terminal",
      position: Math.max(0, buffer.viewportY),
      maximum: Math.max(0, buffer.baseY),
      visible: Math.max(1, terminal.rows),
      total: Math.max(1, buffer.length),
    };
  }

  const output = elements.terminalOutputFallback;
  const maximum = Math.max(0, output.scrollHeight - output.clientHeight);
  return {
    kind: "fallback",
    position: Math.max(0, Math.min(maximum, output.scrollTop)),
    maximum,
    visible: Math.max(1, output.clientHeight),
    total: Math.max(1, output.scrollHeight),
  };
}

function updateAgentOutputNavigation() {
  state.outputNavigationFrame = null;
  const metrics = agentOutputScrollMetrics();
  const track = elements.agentOutputScrollTrack;
  const thumb = elements.agentOutputScrollThumb;
  const trackHeight = Math.max(0, track.clientHeight);
  const scrollable = metrics.maximum > 0 && trackHeight > 0;
  const progress = scrollable ? metrics.position / metrics.maximum : 0;
  const visibleRatio = Math.max(0, Math.min(1, metrics.visible / metrics.total));
  const thumbHeight = scrollable
    ? Math.min(
      trackHeight,
      Math.max(AGENT_OUTPUT_SCROLL_THUMB_MIN_PX, trackHeight * visibleRatio),
    )
    : trackHeight;
  const travel = Math.max(0, trackHeight - thumbHeight);
  const thumbTop = travel * Math.max(0, Math.min(1, progress));

  track.classList.toggle("is-scrollable", scrollable);
  track.setAttribute("aria-valuemax", String(Math.round(metrics.maximum)));
  track.setAttribute("aria-valuenow", String(Math.round(metrics.position)));
  track.setAttribute(
    "aria-valuetext",
    scrollable ? `已滚动 ${Math.round(progress * 100)}%` : "无需滚动",
  );
  thumb.style.height = `${Math.max(0, thumbHeight)}px`;
  thumb.style.transform = `translateY(${Math.max(0, thumbTop)}px)`;
  elements.agentOutputTopButton.disabled = !scrollable || metrics.position <= 0;
  elements.agentOutputBottomButton.disabled = !scrollable
    || metrics.position >= metrics.maximum;
}

function scheduleAgentOutputNavigationUpdate() {
  if (state.outputNavigationFrame !== null) return;
  state.outputNavigationFrame = window.requestAnimationFrame(updateAgentOutputNavigation);
}

function setAgentOutputScrollPosition(position) {
  const metrics = agentOutputScrollMetrics();
  const target = Math.max(0, Math.min(metrics.maximum, Number(position) || 0));
  const terminal = state.outputTerminalInstance;
  if (metrics.kind === "terminal" && terminal) {
    if (target >= metrics.maximum) terminal.scrollToBottom();
    else terminal.scrollToLine(Math.round(target));
    state.userScrolledUp = metrics.maximum - target > 2;
  } else {
    elements.terminalOutputFallback.scrollTop = target;
    state.userScrolledUp = metrics.maximum - target > 80;
  }
  scheduleAgentOutputNavigationUpdate();
}

function scrollAgentOutputToEdge(edge) {
  const terminal = state.outputTerminalInstance;
  if (terminal && agentOutputHasSelection(terminal)) clearAgentOutputSelection(terminal);
  const metrics = agentOutputScrollMetrics();
  setAgentOutputScrollPosition(edge === "top" ? 0 : metrics.maximum);
}

function bindAgentOutputNavigation() {
  const track = elements.agentOutputScrollTrack;
  const thumb = elements.agentOutputScrollThumb;
  let activePointerId = null;
  let pointerOffset = 0;

  const scrollFromPointer = (clientY) => {
    const metrics = agentOutputScrollMetrics();
    const trackBounds = track.getBoundingClientRect();
    const thumbHeight = Math.min(trackBounds.height, thumb.getBoundingClientRect().height);
    const travel = Math.max(0, trackBounds.height - thumbHeight);
    if (!travel || !metrics.maximum) return;
    const pixel = Math.max(0, Math.min(
      travel,
      clientY - trackBounds.top - pointerOffset,
    ));
    setAgentOutputScrollPosition((pixel / travel) * metrics.maximum);
  };

  const finishPointer = (event) => {
    if (activePointerId === null || event.pointerId !== activePointerId) return;
    activePointerId = null;
    track.classList.remove("is-dragging");
    if (track.hasPointerCapture?.(event.pointerId)) track.releasePointerCapture(event.pointerId);
  };

  elements.agentOutputTopButton.addEventListener("click", () => scrollAgentOutputToEdge("top"));
  elements.agentOutputBottomButton.addEventListener("click", () => scrollAgentOutputToEdge("bottom"));
  track.addEventListener("pointerdown", (event) => {
    if (!track.classList.contains("is-scrollable") || event.button > 0) return;
    const terminal = state.outputTerminalInstance;
    if (terminal && agentOutputHasSelection(terminal)) clearAgentOutputSelection(terminal);
    const thumbBounds = thumb.getBoundingClientRect();
    pointerOffset = event.target === thumb
      ? event.clientY - thumbBounds.top
      : thumbBounds.height / 2;
    activePointerId = event.pointerId;
    track.classList.add("is-dragging");
    track.setPointerCapture?.(event.pointerId);
    scrollFromPointer(event.clientY);
    event.preventDefault();
    event.stopPropagation();
  });
  track.addEventListener("pointermove", (event) => {
    if (activePointerId === null || event.pointerId !== activePointerId) return;
    scrollFromPointer(event.clientY);
    event.preventDefault();
    event.stopPropagation();
  });
  track.addEventListener("pointerup", finishPointer);
  track.addEventListener("pointercancel", finishPointer);
  track.addEventListener("lostpointercapture", (event) => {
    if (event.pointerId === activePointerId) {
      activePointerId = null;
      track.classList.remove("is-dragging");
    }
  });
  track.addEventListener("keydown", (event) => {
    const metrics = agentOutputScrollMetrics();
    const step = metrics.kind === "terminal" ? 3 : 48;
    const page = Math.max(step, metrics.visible * 0.85);
    let target = null;
    if (event.key === "Home") target = 0;
    else if (event.key === "End") target = metrics.maximum;
    else if (event.key === "ArrowUp") target = metrics.position - step;
    else if (event.key === "ArrowDown") target = metrics.position + step;
    else if (event.key === "PageUp") target = metrics.position - page;
    else if (event.key === "PageDown") target = metrics.position + page;
    if (target === null) return;
    event.preventDefault();
    setAgentOutputScrollPosition(target);
  });
  scheduleAgentOutputNavigationUpdate();
}

function ensureAgentOutputTerminal() {
  if (state.outputTerminalInstance) return true;
  if (typeof window.Terminal !== "function" || typeof window.FitAddon?.FitAddon !== "function") {
    return false;
  }

  let terminal;
  try {
    terminal = new window.Terminal({
      convertEol: true,
      cursorBlink: false,
      cursorInactiveStyle: "none",
      disableStdin: true,
      fontFamily: document.fonts?.check('400 13px "Herdr FiraCode Nerd"')
        ? TERMINAL_FONT_FAMILY
        : TERMINAL_FALLBACK_FONT_FAMILY,
      fontSize: isDesktop() ? 13 : 11,
      lineHeight: 1.1,
      scrollback: AGENT_OUTPUT_SCROLLBACK_LINES,
      screenReaderMode: true,
      theme: terminalTheme(),
      allowTransparency: false,
    });
    const fitAddon = new window.FitAddon.FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(elements.agentOutputTerminal);
    attachTerminalCopyShortcut(terminal, "Agent 输出选区", true);
    enableAgentOutputTouchScrolling(terminal);
    terminal.onSelectionChange(() => updateAgentOutputSelectionState(terminal));
    if (terminal.textarea) {
      terminal.textarea.setAttribute("aria-label", "Agent 最近输出（只读）");
      terminal.textarea.setAttribute("aria-readonly", "true");
    }
    terminal.element?.querySelector(".live-region")?.setAttribute("aria-live", "off");
    terminal.onScroll((position) => {
      state.userScrolledUp = terminal.buffer.active.baseY - position > 2;
      scheduleAgentOutputNavigationUpdate();
    });
    terminal.onResize(scheduleAgentOutputNavigationUpdate);
    state.outputTerminalInstance = terminal;
    state.outputTerminalFitAddon = fitAddon;
    elements.agentOutputTerminal.hidden = false;
    elements.terminalOutputFallback.hidden = true;
    scheduleAgentOutputNavigationUpdate();

    terminalFontReady.then(() => {
      if (state.outputTerminalInstance !== terminal) return;
      terminal.options.fontFamily = TERMINAL_FONT_FAMILY;
      terminal.refresh(0, Math.max(0, terminal.rows - 1));
      window.requestAnimationFrame(() => refitAgentOutputTerminal(true));
    });

    if ("ResizeObserver" in window) {
      state.outputTerminalResizeObserver = new ResizeObserver(() => refitAgentOutputTerminal());
      state.outputTerminalResizeObserver.observe(elements.agentOutputTerminal);
    }
    window.requestAnimationFrame(fitAgentOutputTerminal);
    return true;
  } catch (_) {
    terminal?.dispose();
    elements.agentOutputTerminal.hidden = true;
    elements.terminalOutputFallback.hidden = false;
    return false;
  }
}

function agentOutputTextCellWidth(text) {
  let width = 0;
  for (const character of String(text || "")) {
    const codePoint = character.codePointAt(0);
    if (UNICODE_MARK_RE.test(character) || codePoint === 0x200d || codePoint === 0xfeff) continue;
    const wide = (
      (codePoint >= 0x1100 && codePoint <= 0x115f)
      || codePoint === 0x2329
      || codePoint === 0x232a
      || (codePoint >= 0x2e80 && codePoint <= 0xa4cf && codePoint !== 0x303f)
      || (codePoint >= 0xac00 && codePoint <= 0xd7a3)
      || (codePoint >= 0xf900 && codePoint <= 0xfaff)
      || (codePoint >= 0xfe10 && codePoint <= 0xfe19)
      || (codePoint >= 0xfe30 && codePoint <= 0xfe6f)
      || (codePoint >= 0xff00 && codePoint <= 0xff60)
      || (codePoint >= 0xffe0 && codePoint <= 0xffe6)
      || (codePoint >= 0x1f300 && codePoint <= 0x1faff)
      || (codePoint >= 0x20000 && codePoint <= 0x3fffd)
    );
    width += wide ? 2 : 1;
  }
  return width;
}

function fitAgentOutputDividers(snapshot, columns) {
  const width = Math.max(1, Math.trunc(Number(columns) || 0));
  return String(snapshot || "").replace(/[^\r\n]+/g, (line) => {
    const visible = line.replace(ANSI_ESCAPE_SEQUENCE_RE, "");
    const dividerCells = [...visible.matchAll(AGENT_DIVIDER_GLYPH_RE)].length;
    const dividerDominated = dividerCells >= 20 && dividerCells >= [...visible].length * 0.6;
    if (!AGENT_DIVIDER_TEXT_RE.test(visible) && !dividerDominated) return line;

    let divider = null;
    for (const match of line.matchAll(AGENT_DIVIDER_RUN_RE)) {
      if (!divider || match[0].length > divider[0].length) divider = match;
    }
    if (!divider) return line;
    const glyph = [...divider[0]][0] || "─";
    const fixedWidth = agentOutputTextCellWidth(visible) - [...divider[0]].length;
    const dividerWidth = Math.max(1, width - fixedWidth);
    return `${line.slice(0, divider.index)}${glyph.repeat(dividerWidth)}${line.slice(divider.index + divider[0].length)}`;
  });
}

function fitAgentOutputTerminal() {
  const terminal = state.outputTerminalInstance;
  if (!terminal || !state.outputTerminalFitAddon || elements.agentOutputTerminal.hidden) return false;
  if (agentOutputSelectionLocked(terminal)) return false;
  if (elements.agentOutputTerminal.clientWidth < 80 || elements.agentOutputTerminal.clientHeight < 80) return false;
  const fontSize = isDesktop() ? 13 : 11;
  if (terminal.options.fontSize !== fontSize) terminal.options.fontSize = fontSize;
  const previousColumns = terminal.cols;
  try {
    state.outputTerminalFitAddon.fit();
  } catch (_) {
    return false;
  }
  scheduleAgentOutputNavigationUpdate();
  return terminal.cols !== previousColumns;
}

function refitAgentOutputTerminal(force = false) {
  const terminal = state.outputTerminalInstance;
  if (!terminal) return;
  const bounds = elements.agentOutputTerminal.getBoundingClientRect();
  const surfaceWidth = Math.round(bounds.width);
  const surfaceHeight = Math.round(bounds.height);
  if (
    !force
    && surfaceWidth === state.outputTerminalSurfaceWidth
    && surfaceHeight === state.outputTerminalSurfaceHeight
  ) return;
  state.outputTerminalSurfaceWidth = surfaceWidth;
  state.outputTerminalSurfaceHeight = surfaceHeight;
  const columnsChanged = fitAgentOutputTerminal();
  scheduleAgentOutputNavigationUpdate();
  if (!columnsChanged || state.outputRenderedSnapshot === null) return;
  state.outputRenderedColumns = 0;
  renderOutput();
}

function clearTerminalResizeAckTimer() {
  if (state.terminalResizeAckTimer !== null) {
    window.clearTimeout(state.terminalResizeAckTimer);
    state.terminalResizeAckTimer = null;
  }
}

function resetTerminalResizeSync() {
  clearTerminalResizeAckTimer();
  state.terminalResizeDirty = false;
  state.terminalResizeSequence = 0;
  state.terminalResizePendingId = null;
  state.terminalResizeSentCols = 0;
  state.terminalResizeSentRows = 0;
  state.terminalResizeAckCols = 0;
  state.terminalResizeAckRows = 0;
  state.terminalPendingInput = [];
  state.terminalPendingInputBytes = 0;
}

function completeTerminalResize(resizeId, cols, rows) {
  if (resizeId !== state.terminalResizePendingId) return;
  clearTerminalResizeAckTimer();
  state.terminalResizePendingId = null;
  state.terminalResizeAckCols = cols;
  state.terminalResizeAckRows = rows;

  if (state.terminalResizeDirty) {
    fitWebTerminal();
    if (state.terminalResizePendingId !== null) return;
  }

  const terminal = state.terminalInstance;
  if (terminal && (terminal.cols !== cols || terminal.rows !== rows)) {
    // The viewport changed again while the previous resize crossed the
    // WebSocket. Treat the acknowledged size as the new baseline and send
    // the current grid before releasing any queued input.
    state.terminalResizeSentCols = cols;
    state.terminalResizeSentRows = rows;
    requestWebTerminalResize(terminal.cols, terminal.rows);
    return;
  }
  flushPendingTerminalInput();
}

function requestWebTerminalResize(cols, rows) {
  if (!state.terminalSessionId || !socketReady()) return false;
  if (
    cols === state.terminalResizeSentCols
    && rows === state.terminalResizeSentRows
  ) return false;

  const resizeId = state.terminalResizeSequence + 1;
  const delivered = send({
    type: "terminal_resize",
    session_id: state.terminalSessionId,
    resize_id: resizeId,
    cols,
    rows,
  });
  if (!delivered) return false;

  state.terminalResizeSequence = resizeId;
  state.terminalResizePendingId = resizeId;
  state.terminalResizeSentCols = cols;
  state.terminalResizeSentRows = rows;
  clearTerminalResizeAckTimer();
  state.terminalResizeAckTimer = window.setTimeout(() => {
    // A cached older Relay may apply terminal_resize without returning an
    // acknowledgement. WebSocket ordering still guarantees that any input
    // sent now follows that resize, so keep compatibility without hanging
    // the Android keyboard indefinitely.
    completeTerminalResize(resizeId, cols, rows);
  }, TERMINAL_RESIZE_ACK_TIMEOUT);
  return true;
}

function fitWebTerminal() {
  if (!state.terminalInstance || !state.terminalFitAddon || elements.terminalWorkspace.hidden) return;
  if (elements.webTerminal.clientWidth < 80 || elements.webTerminal.clientHeight < 80) return;
  try {
    state.terminalFitAddon.fit();
    alignWebTerminalGrid();
    state.terminalFitAddon.fit();
  } catch (_) {
    return;
  }
  state.terminalResizeDirty = false;
  requestWebTerminalResize(state.terminalInstance.cols, state.terminalInstance.rows);
}

function scheduleWebTerminalFit(scrollToBottom = false) {
  state.terminalResizeDirty = true;
  state.terminalFitScrollToBottom ||= scrollToBottom;
  if (state.terminalFitFrame !== null) window.cancelAnimationFrame(state.terminalFitFrame);
  if (state.terminalFitTimer !== null) window.clearTimeout(state.terminalFitTimer);
  const fit = () => {
    state.terminalFitFrame = null;
    fitWebTerminal();
    if (state.terminalFitScrollToBottom) state.terminalInstance?.scrollToBottom();
  };
  state.terminalFitFrame = window.requestAnimationFrame(fit);
  state.terminalFitTimer = window.setTimeout(() => {
    state.terminalFitTimer = null;
    fitWebTerminal();
    if (state.terminalFitScrollToBottom) state.terminalInstance?.scrollToBottom();
    state.terminalFitScrollToBottom = false;
  }, 280);
}

function webTerminalHasFocus() {
  const active = document.activeElement;
  return Boolean(active && (
    active === state.terminalInstance?.textarea
    || elements.webTerminal.contains(active)
  ));
}

function setShellKeyboardOpen(open) {
  const keyboardOpen = open === true;
  document.body.classList.toggle("shell-keyboard-open", keyboardOpen);
  const keyboardChanged = keyboardOpen !== state.shellKeyboardOpen;
  if (keyboardChanged) {
    state.shellKeyboardOpen = keyboardOpen;
    scheduleWebTerminalFit(keyboardOpen);
  }
  return keyboardChanged;
}

function syncVisualViewportLayout() {
  const viewport = window.visualViewport;
  const viewportWidth = Math.round(viewport?.width || window.innerWidth);
  const viewportHeight = Math.round(viewport?.height || window.innerHeight);
  const viewportOffsetTop = Math.max(0, Math.round(viewport?.offsetTop || 0));
  const widthChanged = !state.visualViewportWidth
    || Math.abs(viewportWidth - state.visualViewportWidth) > MOBILE_VIEWPORT_WIDTH_TOLERANCE;

  if (widthChanged) {
    state.visualViewportWidth = viewportWidth;
    state.visualViewportBaselineHeight = viewportHeight;
  } else if (!webTerminalHasFocus() && viewportHeight > state.visualViewportBaselineHeight) {
    state.visualViewportBaselineHeight = viewportHeight;
  }

  const baselineHeight = Math.max(state.visualViewportBaselineHeight, viewportHeight);
  const keyboardThreshold = Math.max(
    MOBILE_KEYBOARD_MIN_HEIGHT,
    Math.round(baselineHeight * MOBILE_KEYBOARD_HEIGHT_RATIO),
  );
  const keyboardOpen = !isDesktop()
    && state.activeView === "terminal"
    && document.body.classList.contains("shell-open")
    && webTerminalHasFocus()
    && baselineHeight - viewportHeight >= keyboardThreshold;

  document.documentElement.style.setProperty("--app-viewport-height", `${viewportHeight}px`);
  document.documentElement.style.setProperty("--app-viewport-offset-top", `${viewportOffsetTop}px`);
  const keyboardChanged = setShellKeyboardOpen(keyboardOpen);
  if (!keyboardChanged) scheduleWebTerminalFit();
  if (state.activeView !== "terminal") refitAgentOutputTerminal();
}

function initializeMobileViewportHandling() {
  if (navigator.virtualKeyboard && "overlaysContent" in navigator.virtualKeyboard) {
    try {
      navigator.virtualKeyboard.overlaysContent = false;
    } catch (_) {
      // Some browsers expose the API without allowing this preference to change.
    }
  }

  const viewport = window.visualViewport;
  state.visualViewportWidth = Math.round(viewport?.width || window.innerWidth);
  state.visualViewportBaselineHeight = Math.round(viewport?.height || window.innerHeight);
  viewport?.addEventListener("resize", syncVisualViewportLayout);
  viewport?.addEventListener("scroll", syncVisualViewportLayout);
  document.addEventListener("focusin", syncVisualViewportLayout);
  document.addEventListener("focusout", () => window.setTimeout(syncVisualViewportLayout, 0));
  syncVisualViewportLayout();
}

function alignWebTerminalGrid() {
  const cellHeight = state.terminalInstance?._core?._renderService?.dimensions?.css?.cell?.height;
  const terminalFrame = elements.webTerminal.parentElement;
  if (!terminalFrame || !elements.terminalKeybar || !Number.isFinite(cellHeight) || cellHeight <= 0) return;

  // FitAddon floors the available height to whole rows. Give the sub-row
  // remainder to the shortcut bar so the terminal itself has no blank edge.
  const availableHeight = terminalFrame.getBoundingClientRect().height
    + elements.terminalKeybar.getBoundingClientRect().height;
  let rows = Math.max(1, Math.round(
    (availableHeight - TERMINAL_KEYBAR_PREFERRED_HEIGHT) / cellHeight,
  ));
  let keybarHeight = availableHeight - Math.ceil(rows * cellHeight);
  if (keybarHeight < TERMINAL_KEYBAR_MIN_HEIGHT && rows > 1) {
    rows -= 1;
    keybarHeight = availableHeight - Math.ceil(rows * cellHeight);
  }
  const normalizedHeight = Math.max(TERMINAL_KEYBAR_MIN_HEIGHT, keybarHeight);
  const cssHeight = `${Math.round(normalizedHeight * 100) / 100}px`;
  if (elements.terminalKeybar.style.getPropertyValue("--terminal-keybar-height") === cssHeight) return;
  elements.terminalKeybar.style.setProperty("--terminal-keybar-height", cssHeight);
}

function openTerminalProfile(profileId) {
  const profile = terminalProfileById(profileId);
  if (!state.terminalAuthorized || !profile) {
    showToast("终端不可用", "请先启用 Remote Shell，或刷新服务器列表。", "error");
    return;
  }
  if (!socketReady()) {
    showToast("Relay 尚未连接", "连接恢复后会重新附着到 tmux 会话。", "error");
    return;
  }
  state.activeView = "terminal";
  state.activeTerminalProfile = profile.id;
  state.terminalRequestedProfile = null;
  state.terminalShouldReconnect = true;
  state.terminalPending = true;
  state.terminalConnected = false;
  state.terminalPersistent = false;
  state.terminalSessionId = null;
  resetTerminalResizeSync();
  document.body.classList.add("shell-open");
  setAppView("terminal");
  if (!ensureWebTerminal()) {
    state.terminalPending = false;
    return;
  }
  state.terminalInstance.reset();
  renderRemoteAccess();
  renderTerminalConnection();
  window.requestAnimationFrame(() => {
    fitWebTerminal();
    const delivered = send({
      type: "terminal_open",
      profile_id: profile.id,
      cols: state.terminalInstance.cols || 100,
      rows: state.terminalInstance.rows || 30,
    });
    if (!delivered) {
      state.terminalPending = false;
      renderTerminalConnection();
    }
  });
}

function handleTerminalOpened(message) {
  if (!message.session_id || !message.profile) return;
  resetTerminalResizeSync();
  state.activeTerminalProfile = message.profile.id;
  state.terminalSessionId = message.session_id;
  state.terminalPending = false;
  state.terminalConnected = true;
  state.terminalPersistent = message.persistent === true;
  state.terminalShouldReconnect = true;
  const openedCols = Number(message.cols);
  const openedRows = Number(message.rows);
  if (Number.isInteger(openedCols) && Number.isInteger(openedRows)) {
    state.terminalResizeSentCols = openedCols;
    state.terminalResizeSentRows = openedRows;
    state.terminalResizeAckCols = openedCols;
    state.terminalResizeAckRows = openedRows;
  }
  if (!terminalProfileById(message.profile.id)) state.terminalProfiles.unshift(message.profile);
  ensureWebTerminal();
  renderRemoteAccess();
  renderTerminalConnection();
  updateAppUrl();
  window.requestAnimationFrame(() => {
    fitWebTerminal();
    state.terminalInstance?.focus();
  });
}

function handleTerminalResized(message) {
  if (message.session_id !== state.terminalSessionId) return;
  const resizeId = Number(message.resize_id);
  const cols = Number(message.cols);
  const rows = Number(message.rows);
  if (
    !Number.isInteger(resizeId)
    || !Number.isInteger(cols)
    || !Number.isInteger(rows)
  ) return;
  completeTerminalResize(resizeId, cols, rows);
}

function handleTerminalOutput(message) {
  if (message.session_id !== state.terminalSessionId || !message.data) return;
  if (!ensureWebTerminal()) return;
  try {
    const binary = window.atob(message.data);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    state.terminalInstance.write(bytes);
  } catch (_) {
    showToast("终端输出损坏", "Relay 返回了无法解码的数据。", "error");
  }
}

function handleTerminalCapture(message) {
  if (message.session_id !== state.terminalSessionId) return;
  if (Number(message.capture_id) !== state.terminalCaptureId) return;
  state.terminalCapturePending = false;
  if (!elements.terminalCopyDialog.open || state.copyDialogSource !== "terminal") return;
  const content = String(message.content || "");
  if (!content) {
    elements.terminalCopyMeta.textContent = "tmux 历史为空，显示浏览器缓冲区";
    return;
  }
  setTerminalCopyContent(
    content,
    message.truncated === true
      ? "tmux 历史较长，已保留最近 1 MiB"
      : `${content.split("\n").length} 行 · tmux Pane 完整历史`,
  );
}

function handleTerminalExit(message) {
  if (message.session_id !== state.terminalSessionId) return;
  state.terminalInstance?.blur();
  setShellKeyboardOpen(false);
  resetTerminalResizeSync();
  state.terminalSessionId = null;
  state.terminalPending = false;
  state.terminalConnected = false;
  state.terminalPersistent = false;
  state.terminalShouldReconnect = false;
  state.terminalInstance?.write(`\r\n\x1b[38;5;245m[会话已结束${Number.isInteger(message.exit_code) ? `，退出码 ${message.exit_code}` : ""}]\x1b[0m\r\n`);
  renderTerminalConnection();
}

function handleTerminalError(message) {
  const operation = message.operation || "terminal";
  if (operation === "terminal_capture") {
    const awaitingCapture = state.terminalCapturePending;
    state.terminalCapturePending = false;
    if (!awaitingCapture) return;
    if (elements.terminalCopyDialog.open && state.copyDialogSource === "terminal") {
      elements.terminalCopyMeta.textContent = "无法读取 tmux 历史，显示浏览器缓冲区";
    }
    showToast("无法读取完整 tmux 历史", "仍可复制浏览器当前保留的终端内容。", "error");
    return;
  }
  if (operation === "terminal_resize") {
    const discardedInput = state.terminalPendingInputBytes > 0;
    resetTerminalResizeSync();
    state.terminalResizeDirty = true;
    showToast(
      "终端尺寸同步失败",
      discardedInput
        ? "为避免命令错位执行，未发送同步期间的输入；请重新输入。"
        : "下一次输入会自动重试尺寸同步；若仍失败请重新连接。",
      "error",
    );
    return;
  }
  if (operation === "ssh_profile") {
    state.sshProfilePending = false;
    renderSshProfileForm();
  } else {
    resetTerminalResizeSync();
    state.terminalSessionId = null;
    state.terminalPending = false;
    state.terminalConnected = false;
    state.terminalPersistent = false;
    state.terminalShouldReconnect = false;
    renderTerminalConnection();
  }
  showToast("远程操作失败", message.message || "终端操作失败", "error");
}

function deliverTerminalBytes(bytes) {
  if (!state.terminalSessionId || !socketReady() || !bytes.length) return;
  for (let offset = 0; offset < bytes.length; offset += 12 * 1024) {
    const chunk = bytes.subarray(offset, offset + 12 * 1024);
    let binary = "";
    for (const byte of chunk) binary += String.fromCharCode(byte);
    send({
      type: "terminal_input",
      session_id: state.terminalSessionId,
      data: window.btoa(binary),
    });
  }
}

function flushPendingTerminalInput() {
  if (state.terminalResizePendingId !== null || !state.terminalPendingInput.length) return;
  const pending = state.terminalPendingInput;
  state.terminalPendingInput = [];
  state.terminalPendingInputBytes = 0;
  if (!state.terminalSessionId || !socketReady()) return;
  for (const bytes of pending) deliverTerminalBytes(bytes);
}

function sendTerminalText(text) {
  if (!state.terminalSessionId || !socketReady() || typeof text !== "string" || !text) return;
  const bytes = new TextEncoder().encode(text);
  if (bytes.length > TERMINAL_PENDING_INPUT_MAX_BYTES) {
    showToast("输入内容过长", "单次终端粘贴最多允许 64 KiB。", "error");
    return;
  }

  // A visualViewport or ResizeObserver callback can be waiting for its next
  // animation frame when Android already emits the first IME character. Fit
  // synchronously here so the resize message is placed before that input.
  if (state.terminalResizeDirty) fitWebTerminal();
  else if (state.terminalInstance) {
    requestWebTerminalResize(state.terminalInstance.cols, state.terminalInstance.rows);
  }

  if (state.terminalResizePendingId !== null) {
    if (state.terminalPendingInputBytes + bytes.length > TERMINAL_PENDING_INPUT_MAX_BYTES) {
      showToast(
        "终端正在调整尺寸",
        "等待 tmux 确认新窗口大小后再粘贴较长内容。",
        "error",
      );
      return;
    }
    state.terminalPendingInput.push(bytes);
    state.terminalPendingInputBytes += bytes.length;
    return;
  }
  deliverTerminalBytes(bytes);
}

function sendTerminalShortcutText(text) {
  // Shortcut controls write directly to the PTY. Refocusing xterm here would
  // reopen the Android keyboard when it is hidden, while the keybar pointer
  // guard below keeps the existing xterm focus when the keyboard is open.
  sendTerminalText(text);
}

function preserveTerminalFocusForKeybar(event) {
  if (event.target instanceof Element && event.target.closest("button")) {
    event.preventDefault();
  }
}

function setTerminalCtrlPending(pending) {
  const active = pending === true && state.terminalConnected;
  if (active) setTerminalTmuxPrefixPending(false);
  state.terminalCtrlPending = active;
  elements.terminalCtrlButton.classList.toggle("is-active", active);
  elements.terminalCtrlButton.setAttribute("aria-pressed", String(active));
  elements.terminalCtrlButton.title = active
    ? "Ctrl 已启用；下一次终端输入将使用 Ctrl 组合"
    : "作用于下一次终端输入";
}

function setTerminalTmuxPrefixPending(pending) {
  const active = pending === true && state.terminalConnected && state.terminalPersistent;
  if (active) setTerminalCtrlPending(false);
  state.terminalTmuxPrefixPending = active;
  elements.terminalTmuxPrefixButton.classList.toggle("is-active", active);
  elements.terminalTmuxPrefixButton.setAttribute("aria-pressed", String(active));
  elements.terminalTmuxPrefixButton.title = active
    ? "tmux Prefix 已启用；下一次按键将与 Ctrl+B 一次发送"
    : "作用于下一次终端按键";
}

function clearTerminalModifierState() {
  setTerminalCtrlPending(false);
  setTerminalTmuxPrefixPending(false);
}

function ctrlSequenceForInput(input) {
  const modifiedSequence = Object.entries(TERMINAL_KEY_SEQUENCES)
    .find(([key, sequence]) => sequence === input && TERMINAL_CTRL_KEY_SEQUENCES[key]);
  if (modifiedSequence) return TERMINAL_CTRL_KEY_SEQUENCES[modifiedSequence[0]];
  if (input === " ") return "\x00";
  if (input === "?") return "\x7f";
  if (input.length !== 1) return null;
  let code = input.charCodeAt(0);
  if (code >= 97 && code <= 122) code -= 32;
  return code >= 64 && code <= 95 ? String.fromCharCode(code - 64) : null;
}

function terminalSequenceWithPendingModifiers(key, sequence) {
  if (state.terminalTmuxPrefixPending) {
    setTerminalTmuxPrefixPending(false);
    return `${TMUX_PREFIX_SEQUENCE}${sequence}`;
  }
  if (!state.terminalCtrlPending) return sequence;
  setTerminalCtrlPending(false);
  return TERMINAL_CTRL_KEY_SEQUENCES[key] || sequence;
}

function applyTerminalModifiersToInput(input) {
  if (TERMINAL_FOCUS_SEQUENCES.has(input)) return input;
  if (state.terminalTmuxPrefixPending) {
    setTerminalTmuxPrefixPending(false);
    return `${TMUX_PREFIX_SEQUENCE}${input}`;
  }
  if (!state.terminalCtrlPending) return input;
  const modified = ctrlSequenceForInput(input);
  setTerminalCtrlPending(false);
  return modified ?? input;
}

function closeTerminalSelection() {
  state.terminalInstance?.blur();
  if (state.terminalSessionId && socketReady()) {
    send({type: "terminal_close", session_id: state.terminalSessionId});
  }
  resetTerminalResizeSync();
  state.terminalSessionId = null;
  state.terminalPending = false;
  state.terminalConnected = false;
  state.terminalPersistent = false;
  state.terminalShouldReconnect = false;
  state.activeTerminalProfile = null;
  document.body.classList.remove("shell-open");
  setShellKeyboardOpen(false);
  renderRemoteAccess();
  renderTerminalConnection();
  updateAppUrl();
}

function renderRemoteAccess() {
  const connected = socketReady();
  const profiles = state.terminalAuthorized ? state.terminalProfiles : [];
  const activeProfile = terminalProfileById(state.activeTerminalProfile);
  if (state.activeTerminalProfile && !activeProfile) {
    state.activeTerminalProfile = null;
    resetTerminalResizeSync();
    state.terminalSessionId = null;
    state.terminalConnected = false;
    state.terminalPersistent = false;
    state.terminalPending = false;
    document.body.classList.remove("shell-open");
    updateAppUrl();
  }

  elements.addSshHostButton.disabled = !connected || !state.terminalAuthorized;
  elements.refreshTerminalProfilesButton.disabled = !connected || !state.terminalAuthorized;
  elements.terminalProfileCount.textContent = state.terminalAuthorized
    ? `${profiles.length} 个可用入口`
    : "Remote Shell 未授权";
  elements.terminalProfileList.replaceChildren();

  if (!state.terminalAuthorized) {
    const unavailable = document.createElement("div");
    unavailable.className = "terminal-profile-empty";
    const strong = document.createElement("strong");
    strong.textContent = "完整终端尚未启用";
    const span = document.createElement("span");
    span.textContent = "运行安装器的 --remote-shell 模式后，这里会显示本机和局域网服务器。";
    unavailable.append(strong, span);
    elements.terminalProfileList.append(unavailable);
  } else {
    for (const profile of profiles) {
      elements.terminalProfileList.append(createTerminalProfileCard(profile));
    }
  }

  const sshCommand = nativeSshCommand();
  elements.nativeSshStatus.textContent = state.nativeSshEnabled
    ? (sshCommand || "已启用，可使用普通 SSH 客户端连接")
    : "尚未启用；Remote Shell 安装模式可同时开启";
  elements.copyNativeSshButton.disabled = !state.nativeSshEnabled || !sshCommand;

  const hasActiveProfile = Boolean(terminalProfileById(state.activeTerminalProfile));
  elements.terminalEmpty.hidden = hasActiveProfile;
  elements.shellConsole.hidden = !hasActiveProfile;
  elements.terminalSetupCommand.hidden = state.terminalAuthorized;
  elements.copyTerminalSetupButton.hidden = state.terminalAuthorized;
  elements.terminalEmptyTitle.textContent = state.terminalAuthorized
    ? "选择一个终端入口"
    : "启用安全 Remote Shell";
  elements.terminalEmptyCopy.textContent = state.terminalAuthorized
    ? "使用完整 Shell 查看代码、运行 Git、维护系统，或连接局域网中的其他服务器。"
    : "安装器会启用官方 Tailscale SSH，并为单独的 Tailscale 用户白名单开启 Web Terminal。";
  document.body.classList.toggle("shell-open", hasActiveProfile);
  renderTerminalConnection();
  renderRemoteAccessStatus();
}

function createTerminalProfileCard(profile) {
  const item = document.createElement("article");
  item.className = `terminal-profile-item${profile.id === state.activeTerminalProfile ? " is-selected" : ""}`;

  const openButton = document.createElement("button");
  openButton.type = "button";
  openButton.className = "terminal-profile-card";
  openButton.setAttribute("aria-label", `打开 ${profile.label}`);
  openButton.addEventListener("click", () => openTerminalProfile(profile.id));

  const avatar = document.createElement("span");
  avatar.className = `machine-avatar is-${profile.color || "cyan"}`;
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = profile.kind === "local" ? ">_" : (profile.label.trim().charAt(0) || "S");

  const copy = document.createElement("span");
  copy.className = "terminal-profile-copy";
  const title = document.createElement("strong");
  title.textContent = profile.label;
  const target = document.createElement("span");
  const port = profile.kind === "ssh" && Number(profile.port) !== 22 ? `:${profile.port}` : "";
  target.textContent = profile.kind === "local" ? profile.target : `${profile.target}${port}`;
  const description = document.createElement("small");
  description.textContent = profile.kind === "local"
    ? (state.machine?.tmux ? "tmux 持久会话" : "临时 Shell 会话")
    : (profile.description || "通过本机连接局域网");
  copy.append(title, target, description);

  const trailing = document.createElement("span");
  trailing.className = "terminal-profile-trailing";
  const badge = document.createElement("span");
  badge.className = "machine-kind-badge";
  badge.textContent = profile.kind === "local"
    ? "LOCAL"
    : (profile.agent_enabled ? "SSH + AGENTS" : "SSH");
  const chevron = document.createElement("span");
  chevron.className = "agent-card-chevron";
  chevron.setAttribute("aria-hidden", "true");
  chevron.textContent = "›";
  trailing.append(badge, chevron);
  openButton.append(avatar, copy, trailing);
  item.append(openButton);

  if (profile.kind === "ssh") {
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "profile-edit-button";
    edit.setAttribute("aria-label", `编辑 ${profile.label}`);
    edit.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"/></svg>';
    edit.addEventListener("click", () => openSshHostDialog(profile.id));
    item.append(edit);
  }
  return item;
}

function renderTerminalConnection() {
  const profile = terminalProfileById(state.activeTerminalProfile);
  if (!state.terminalConnected) clearTerminalModifierState();
  elements.terminalCtrlButton.disabled = !state.terminalConnected;
  elements.copyWebTerminalButton.disabled = !state.terminalInstance;
  elements.terminalCopyButton.disabled = !state.terminalInstance;
  elements.tmuxKeybar.hidden = !state.terminalPersistent;
  elements.tmuxKeybar.querySelectorAll("button").forEach((button) => {
    button.disabled = !state.terminalConnected || !state.terminalPersistent;
  });
  if (!profile) return;
  elements.terminalProfileAvatar.className = `machine-avatar is-${profile.color || "cyan"}`;
  elements.terminalProfileAvatar.textContent = profile.kind === "local" ? ">_" : (profile.label.trim().charAt(0) || "S");
  elements.terminalProfileTitle.textContent = profile.label;
  const port = profile.kind === "ssh" && Number(profile.port) !== 22 ? `:${profile.port}` : "";
  elements.terminalProfileMeta.textContent = profile.kind === "local"
    ? `${profile.target} · ${state.machine?.tmux ? "tmux 持久会话" : "临时 Shell"}`
    : `${profile.target}${port} · 经本机局域网连接`;
  elements.editSshHostButton.hidden = profile.kind !== "ssh";
  elements.copyJumpCommandButton.disabled = !proxyJumpCommand(profile);
  elements.disconnectTerminalButton.disabled = state.terminalPending && !state.terminalSessionId;
  elements.reconnectTerminalButton.disabled = !socketReady() || state.terminalPending || state.terminalConnected;
  elements.clearWebTerminalButton.disabled = !state.terminalInstance;
  elements.terminalPasteButton.disabled = !state.terminalConnected;

  if (state.terminalConnected) {
    elements.terminalSessionStatus.textContent = "已连接";
    elements.terminalSessionStatus.className = "status-pill status-working";
    elements.terminalConnectionOverlay.hidden = true;
  } else {
    elements.terminalSessionStatus.textContent = state.terminalPending ? "连接中" : "已断开";
    elements.terminalSessionStatus.className = "status-pill";
    elements.terminalConnectionOverlay.hidden = false;
    elements.terminalOverlayTitle.textContent = state.terminalPending ? "正在附着会话" : "终端已断开";
    elements.terminalOverlayCopy.textContent = state.terminalPending
      ? "tmux 会保留之前的工作现场"
      : "点击重新连接可回到持久会话";
  }
}

function renderRemoteAccessStatus() {
  if (!state.session) {
    elements.remoteAccessStatus.textContent = "正在读取 Tailscale SSH 与 Web Terminal 状态…";
    return;
  }
  const webTerminal = state.terminalAuthorized ? "Web Terminal 已授权" : "Web Terminal 未启用";
  const nativeSsh = state.nativeSshEnabled ? "Tailscale SSH 已启用" : "Tailscale SSH 未启用";
  elements.remoteAccessStatus.textContent = `${nativeSsh}；${webTerminal}。`;
}

function openSshHostDialog(profileId = "") {
  if (!state.terminalAuthorized) {
    showToast("终端未授权", "先使用 Remote Shell 安装模式启用该功能。", "error");
    return;
  }
  const profile = terminalProfileById(profileId);
  elements.sshHostId.value = profile?.id || "";
  elements.sshHostLabel.value = profile?.label || "";
  elements.sshHostTarget.value = profile?.target || "";
  elements.sshHostPort.value = String(profile?.port || 22);
  elements.sshHostColor.value = profile?.color || "cyan";
  elements.sshHostDescription.value = profile?.description || "";
  elements.sshHostAgentEnabled.checked = profile?.agent_enabled === true;
  elements.sshHostHerdrBin.value = profile?.herdr_bin || "herdr";
  elements.sshHostWorkspaceRoot.value = Array.isArray(profile?.workspace_roots)
    ? profile.workspace_roots.join("\n")
    : (profile?.workspace_root || "~");
  elements.sshHostTitle.textContent = profile ? "编辑 SSH 服务器" : "添加 SSH 服务器";
  elements.deleteSshHostButton.hidden = !profile;
  state.sshProfilePending = false;
  renderSshProfileForm();
  elements.sshHostDialog.showModal();
  window.setTimeout(() => elements.sshHostLabel.focus(), 40);
}

function renderSshProfileForm() {
  const agentEnabled = elements.sshHostAgentEnabled.checked;
  elements.saveSshHostButton.disabled = state.sshProfilePending || !socketReady();
  elements.deleteSshHostButton.disabled = state.sshProfilePending || !socketReady();
  elements.sshHostAgentEnabled.disabled = state.sshProfilePending;
  elements.sshHostHerdrBin.disabled = state.sshProfilePending || !agentEnabled;
  elements.sshHostWorkspaceRoot.disabled = state.sshProfilePending;
  elements.sshHostHerdrBin.required = agentEnabled;
  elements.sshHostWorkspaceRoot.required = true;
  elements.saveSshHostButton.textContent = state.sshProfilePending ? "正在保存…" : "保存并连接";
}

function saveSshHost() {
  if (state.sshProfilePending || !elements.sshHostForm.reportValidity()) return;
  const workspaceRoots = elements.sshHostWorkspaceRoot.value
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  const profile = {
    id: elements.sshHostId.value,
    label: elements.sshHostLabel.value.trim(),
    target: elements.sshHostTarget.value.trim(),
    port: Number(elements.sshHostPort.value),
    color: elements.sshHostColor.value,
    description: elements.sshHostDescription.value.trim(),
    agent_enabled: elements.sshHostAgentEnabled.checked,
    herdr_bin: elements.sshHostHerdrBin.value.trim() || "herdr",
    workspace_roots: workspaceRoots.length ? workspaceRoots : ["~"],
  };
  if (!send({type: "ssh_profile_save", profile})) return;
  state.sshProfilePending = true;
  renderSshProfileForm();
}

function requestDeleteSshHost() {
  const profile = terminalProfileById(elements.sshHostId.value);
  if (!profile || profile.kind !== "ssh") return;
  state.pendingDeleteProfile = profile;
  elements.deleteSshHostDialog.showModal();
}

function confirmDeleteSshHost() {
  const profile = state.pendingDeleteProfile;
  if (!profile || state.sshProfilePending) return;
  if (!send({type: "ssh_profile_delete", profile_id: profile.id})) return;
  state.sshProfilePending = true;
  elements.confirmDeleteSshHostButton.disabled = true;
}

function legacyCopyText(text) {
  if (typeof document.execCommand !== "function") return false;
  const active = document.activeElement;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.readOnly = true;
  textarea.setAttribute("aria-hidden", "true");
  textarea.style.position = "fixed";
  textarea.style.top = "0";
  textarea.style.left = "-10000px";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  let copied = false;
  try {
    textarea.focus({preventScroll: true});
    textarea.select();
    copied = document.execCommand("copy");
  } catch (_) {
    copied = false;
  } finally {
    textarea.remove();
    const mobileShellInput = !isDesktop() && active === state.terminalInstance?.textarea;
    if (active instanceof HTMLElement && !mobileShellInput) {
      try {
        active.focus({preventScroll: true});
      } catch (_) {
        active.focus();
      }
    }
  }
  return copied;
}

async function copyText(text, title, detail) {
  if (!text) {
    showToast("没有可复制内容", "Relay 尚未提供完整连接信息。", "error");
    return false;
  }
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
    await navigator.clipboard.writeText(text);
    showToast(title, detail, "success");
    return true;
  } catch (_) {
    if (legacyCopyText(text)) {
      showToast(title, detail, "success");
      return true;
    }
    showToast("复制失败", "浏览器未授予剪贴板权限。", "error");
    return false;
  }
}

function terminalBufferText(terminal) {
  const buffer = terminal?.buffer?.active;
  if (!buffer) return "";
  const lines = [];
  for (let index = 0; index < buffer.length; index += 1) {
    const line = buffer.getLine(index);
    if (!line) continue;
    const text = line.translateToString(true);
    if (line.isWrapped && lines.length) lines[lines.length - 1] += text;
    else lines.push(text);
  }
  return lines.join("\n").replace(/\n+$/, "");
}

function terminalSelectedText(terminal) {
  const nativeText = nativeAgentSelectionControllers.get(terminal)?.selectedText() || "";
  if (nativeText) return nativeText;
  const browserText = browserSelectionTextInside(agentOutputSelectionTree(terminal));
  return browserText || (terminal?.hasSelection() ? terminal.getSelection() : "");
}

function terminalCopySelection() {
  const start = elements.terminalCopyText.selectionStart;
  const end = elements.terminalCopyText.selectionEnd;
  if (!Number.isInteger(start) || !Number.isInteger(end) || end <= start) return "";
  return elements.terminalCopyText.value.slice(start, end);
}

function renderTerminalCopyAction() {
  const selected = terminalCopySelection();
  const selectedAgentOutput = state.copyDialogSource === "agent-selection";
  elements.terminalCopyConfirmButton.disabled = !elements.terminalCopyText.value;
  elements.terminalCopyConfirmButton.textContent = selected || selectedAgentOutput
    ? "复制选中"
    : "复制全部";
}

function setTerminalCopyContent(text, status = "") {
  const content = String(text || "");
  elements.terminalCopyText.value = content;
  const lineCount = content ? content.split("\n").length : 0;
  elements.terminalCopyMeta.textContent = status || `${lineCount} 行 · ${content.length} 字符`;
  renderTerminalCopyAction();
  window.requestAnimationFrame(() => {
    elements.terminalCopyText.scrollTop = elements.terminalCopyText.scrollHeight;
    elements.terminalCopyText.scrollLeft = 0;
  });
}

function openTerminalCopyDialog({title, description, content, source, status = ""}) {
  state.copyDialogSource = source;
  elements.terminalCopyTitle.textContent = title;
  elements.terminalCopyDescription.textContent = description;
  setTerminalCopyContent(content, status);
  if (source === "terminal") {
    state.terminalInstance?.blur();
    setShellKeyboardOpen(false);
  }
  if (!elements.terminalCopyDialog.open) elements.terminalCopyDialog.showModal();
  if (source === "agent-selection") {
    window.requestAnimationFrame(() => {
      elements.terminalCopyText.scrollTop = 0;
      elements.terminalCopyText.scrollLeft = 0;
    });
  }
}

async function copyFromTerminalDialog() {
  const selection = terminalCopySelection();
  const text = selection || elements.terminalCopyText.value;
  if (!text) {
    showToast("暂无可复制内容", "终端缓冲区当前为空。", "error");
    return;
  }
  const selectedAction = Boolean(selection) || state.copyDialogSource === "agent-selection";
  const copied = await copyText(
    text,
    selectedAction ? "已复制选中内容" : "已复制全部内容",
    selectedAction ? "选择的终端文本已复制。" : "终端纯文本已复制。",
  );
  if (copied && state.copyDialogSource === "agent-selection") {
    elements.terminalCopyDialog.close();
  }
}

function selectAllTerminalCopyText() {
  elements.terminalCopyText.focus({preventScroll: true});
  elements.terminalCopyText.select();
  renderTerminalCopyAction();
}

async function copyWebTerminalOutput() {
  const terminal = state.terminalInstance;
  const selection = terminalSelectedText(terminal);
  if (selection) {
    await copyText(selection, "已复制选区", "Shell 终端选区已复制到剪贴板。");
    return;
  }

  const localContent = terminalBufferText(terminal);
  const canCaptureTmux = state.terminalPersistent
    && Boolean(state.terminalSessionId)
    && socketReady();
  if (!localContent && !canCaptureTmux) {
    showToast("暂无可复制内容", "终端缓冲区当前为空。", "error");
    return;
  }
  const profile = terminalProfileById(state.activeTerminalProfile);
  openTerminalCopyDialog({
    title: `${profile?.label || "Shell"} · 复制内容`,
    description: canCaptureTmux
      ? "正在读取当前 tmux Pane 的纯文本历史；手机可长按选择，PC 可拖选或使用 Ctrl/⌘+C。"
      : "这是浏览器终端缓冲区的纯文本；手机可长按选择，PC 可拖选或使用 Ctrl/⌘+C。",
    content: localContent,
    source: "terminal",
    status: canCaptureTmux ? "正在读取 tmux 完整历史…" : "浏览器终端缓冲区",
  });

  if (!canCaptureTmux) return;
  const captureId = state.terminalCaptureId + 1;
  state.terminalCaptureId = captureId;
  state.terminalCapturePending = send({
    type: "terminal_capture",
    session_id: state.terminalSessionId,
    capture_id: captureId,
  });
  if (!state.terminalCapturePending) {
    elements.terminalCopyMeta.textContent = "无法读取 tmux 历史，显示浏览器缓冲区";
  }
}

async function pasteIntoTerminal() {
  if (!state.terminalConnected) return;
  clearTerminalModifierState();
  try {
    const text = await navigator.clipboard.readText();
    if (text) sendTerminalText(text);
  } catch (_) {
    showToast("无法读取剪贴板", "请长按终端并使用系统粘贴菜单。", "error");
  }
}

function handleCommandResult(message) {
  if (message.ok === false) return;
  const pendingAgentKey = state.agentKeyPending;
  if (
    message.command === "send_keys"
    && pendingAgentKey
    && (
      message.request_id === pendingAgentKey.requestId
      // Relays installed before correlated key acknowledgements omit the ID.
      // Only one Web Agent send_keys operation can be pending, so accepting
      // that legacy acknowledgement cannot collide with Interrupt.
      || !message.request_id
    )
  ) {
    const key = AGENT_INTERACTION_KEYS[pendingAgentKey.key];
    window.clearTimeout(state.agentKeyPendingTimer);
    state.agentKeyPendingTimer = null;
    state.agentKeyPending = null;
    setAgentKeyFeedback(
      `${key.keycap} ${key.label} 已发送`,
      "success",
      pendingAgentKey.paneId,
    );
    refreshActionAvailability();
    showToast(
      "交互按键已发送",
      `${key.label} 已送达 ${pendingAgentKey.agentLabel}。`,
      "success",
    );
    window.setTimeout(() => {
      if (state.activePane === pendingAgentKey.paneId) refreshOutput();
    }, 220);
  }
  const promptResponse = message.command === "respond" && state.promptPending;
  if (
    message.command === "agent_prompt"
    || message.command === "agent_prompt_queue"
    || promptResponse
  ) {
    const submittedImageIds = state.pendingPromptImageIds;
    state.promptPending = false;
    if (elements.promptInput.value.trim() === state.pendingPromptText) {
      elements.promptInput.value = "";
      updateCounter(elements.promptInput, elements.promptCount);
    }
    if (
      submittedImageIds.length
      && submittedImageIds.length === state.promptImages.length
      && submittedImageIds.every((imageId, index) => state.promptImages[index]?.id === imageId)
    ) {
      for (const image of state.promptImages) URL.revokeObjectURL(image.url);
      state.promptImages = [];
      elements.promptImageInput.value = "";
    }
    state.pendingPromptText = "";
    state.pendingPromptMode = "";
    state.pendingPromptImageIds = [];
    state.pendingPromptSubmissionId = 0;
    renderPromptAttachments();
    renderPromptState();
    if (promptResponse) {
      showToast("回答已发送", "Agent 已收到当前 Prompt 的回答。", "success");
    } else {
      const cached = message.command === "agent_prompt_queue" || message.delivery === "cached";
      const queued = message.delivery === "queued";
      showToast(
        cached ? "Prompt 已缓存" : (queued ? "Prompt 已排队" : "Prompt 已发送"),
        cached
          ? "已通过 Tab 加入 Codex 队列，将在当前任务完成后处理。"
          : (queued ? "Agent 正在工作，新任务已提交并将在当前任务后处理。" : "Agent 已收到新的任务。"),
        "success",
      );
    }
    window.setTimeout(refreshOutput, 500);
  }
  if (message.command === "respond" && !promptResponse) {
    showToast(
      "操作已发送",
      state.pendingApprovalLabel || "Agent 已收到回答。",
      "success",
    );
    state.pendingApprovalLabel = "";
    window.setTimeout(refreshOutput, 450);
  }
  if (message.command === "question_toggle") {
    window.setTimeout(refreshOutput, 250);
  }
  if (message.command === "question_submit") {
    showToast("选择已提交", "Agent 将继续处理。", "success");
    window.setTimeout(refreshOutput, 350);
  }
  if (message.command === "send_keys" && state.interruptPending && !message.request_id) {
    state.interruptPending = false;
    refreshActionAvailability();
    if (elements.interruptDialog.open) elements.interruptDialog.close();
    showToast("Interrupt 已发送", "已向当前 Agent 发送 Ctrl+C。", "success");
    window.setTimeout(refreshOutput, 350);
  }
  if (message.command === "ssh_profile_save") {
    state.sshProfilePending = false;
    if (elements.sshHostDialog.open) elements.sshHostDialog.close();
    showToast("服务器已保存", "正在打开持久 SSH 终端。", "success");
    if (message.profile_id) window.setTimeout(() => openTerminalProfile(message.profile_id), 80);
  }
  if (message.command === "ssh_profile_delete") {
    const removedId = message.profile_id;
    const selectedInUrl = new URL(window.location.href).searchParams.get("terminal") === removedId;
    state.sshProfilePending = false;
    state.pendingDeleteProfile = null;
    elements.confirmDeleteSshHostButton.disabled = false;
    if (elements.deleteSshHostDialog.open) elements.deleteSshHostDialog.close();
    if (elements.sshHostDialog.open) elements.sshHostDialog.close();
    if (state.activeTerminalProfile === removedId || selectedInUrl) closeTerminalSelection();
    showToast("服务器入口已删除", "远端服务器和 SSH 配置未被修改。", "success");
  }
}

function handleRelayError(payload) {
  const message = typeof payload === "string"
    ? payload
    : (payload?.message || "Relay 请求失败");
  if (
    state.agentKeyPending
    && (
      payload?.request_id === state.agentKeyPending.requestId
      || !payload?.request_id
    )
  ) {
    const pendingPaneId = state.agentKeyPending.paneId;
    window.clearTimeout(state.agentKeyPendingTimer);
    state.agentKeyPendingTimer = null;
    state.agentKeyPending = null;
    setAgentKeyFeedback("发送失败，请重试", "error", pendingPaneId);
    refreshActionAvailability();
    showToast("交互按键发送失败", message, "error");
    return;
  }
  state.promptPending = false;
  state.pendingPromptText = "";
  state.pendingPromptMode = "";
  state.pendingPromptImageIds = [];
  state.pendingPromptSubmissionId = 0;
  state.pendingApprovalLabel = "";
  window.clearTimeout(state.agentKeyPendingTimer);
  state.agentKeyPendingTimer = null;
  window.clearTimeout(state.agentKeyFeedbackTimer);
  state.agentKeyPending = null;
  state.agentKeyFeedback = "";
  state.agentKeyFeedbackType = "";
  state.agentKeyFeedbackPane = "";
  state.interruptPending = false;
  state.startPending = false;
  state.directoryPending = false;
  state.sshProfilePending = false;
  renderPromptAttachments();
  renderPromptState();
  renderInterruptState();
  renderDirectoryBrowser();
  renderSshProfileForm();
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
  button.addEventListener("click", () => selectAgent(agent.pane_id, true, true));

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
  if (state.activeView !== "agents") return;
  if (state.activePane && activeAgent()) return;
  const requestedPane = paneIdFromUrl();
  if (requestedPane && state.agents.some((agent) => agent.pane_id === requestedPane)) {
    selectAgent(requestedPane, false);
    return;
  }
  if (isDesktop() && state.agents.length) {
    const first = [...state.agents].sort(
      (left, right) => STATUS[normalizedStatus(left)].rank - STATUS[normalizedStatus(right)].rank,
    )[0];
    selectAgent(first.pane_id, false);
  }
}

function selectAgent(paneId, updateHistory = true, markSeen = false) {
  const shouldMarkSeen = markSeen
    && normalizedStatus(state.agents.find((agent) => agent.pane_id === paneId)) === "done";
  const changedPane = state.activePane !== paneId;
  if (changedPane) state.agentKeyPanelOpen = false;
  state.activePane = paneId;
  if (changedPane && state.promptImages.length) clearPromptImages();
  state.userScrolledUp = false;
  document.body.classList.add("agent-open");
  if (updateHistory) updatePaneUrl(paneId);
  renderAll();
  refreshOutput();
  if (shouldMarkSeen && socketReady()) {
    send({ type: "agent_seen", pane_id: paneId });
  }
}

function clearAgentSelection() {
  state.activePane = null;
  state.agentKeyPanelOpen = false;
  if (state.promptImages.length) clearPromptImages();
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

function paneIdFromUrl() {
  // Mobile Back can remove a deep-linked pane before the next Agent snapshot.
  // Always read the live URL so a stale startup value cannot reopen the detail.
  return new URL(window.location.href).searchParams.get("pane");
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
  renderAgentKeyControls();
  renderPromptState();
  renderInterruptState();
}

function renderBlockedBanner(agent) {
  const blocked = normalizedStatus(agent) === "blocked";
  elements.detailConsole.classList.toggle("has-blocked-banner", blocked);
  elements.blockedBanner.hidden = !blocked;
  elements.approvalActions.replaceChildren();
  if (!blocked) return;

  elements.blockedPrompt.textContent = agent.blockedPrompt || "Agent 需要你的确认后才能继续。";
  const isMultiQuestion = agent.interaction === "omp_question" && agent.multi;
  const options = isMultiQuestion
    ? (agent.multiOptions || [])
    : (agent.options || []);
  const selectedOptions = new Set(agent.selectedOptions || []);
  for (const option of options) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `approval-button ${approvalClass(option)}`;
    button.textContent = approvalLabel(option);
    if (isMultiQuestion) {
      const selected = selectedOptions.has(option);
      button.classList.toggle("is-selected", selected);
      button.setAttribute("aria-pressed", String(selected));
      button.addEventListener("click", () => toggleBlockedOption(option, button));
    } else {
      button.addEventListener("click", () => respondToBlocked(option));
    }
    elements.approvalActions.append(button);
  }
  if (isMultiQuestion) {
    const submit = document.createElement("button");
    submit.type = "button";
    submit.className = "approval-button is-approve";
    submit.textContent = "提交选择";
    submit.addEventListener("click", submitBlockedQuestion);
    elements.approvalActions.append(submit);
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
  const agent = activeAgent();
  if (!agent?.promptId) {
    showToast("Prompt 已变化", "请刷新 Agent 输出后再操作。", "error");
    return;
  }
  if (!send({
    type: "respond",
    pane_id: agent.pane_id,
    prompt_id: agent.promptId,
    text,
  })) return;
  state.pendingApprovalLabel = approvalLabel(text);
}

function toggleBlockedOption(option, button) {
  const agent = activeAgent();
  if (!agent?.promptId) return;
  if (!send({
    type: "question_toggle",
    pane_id: agent.pane_id,
    prompt_id: agent.promptId,
    option,
  })) return;
  const selected = button.getAttribute("aria-pressed") !== "true";
  button.setAttribute("aria-pressed", String(selected));
  button.classList.toggle("is-selected", selected);
}

function submitBlockedQuestion() {
  const agent = activeAgent();
  if (!agent?.promptId) return;
  if (!send({
    type: "question_submit",
    pane_id: agent.pane_id,
    prompt_id: agent.promptId,
  })) return;
  showToast("正在提交", "已将多选结果发送给 Agent。", "success");
  window.setTimeout(refreshOutput, 450);
}

function renderOutput() {
  if (!state.activePane) return;
  const content = state.outputs.get(state.activePane);
  const displayContent = content === undefined
    ? "正在读取 Agent 输出…"
    : (content || "当前 Pane 暂无输出。 ");
  const ansiContent = state.ansiOutputs.get(state.activePane) || "";
  const snapshot = ansiContent || displayContent;

  if (ensureAgentOutputTerminal()) {
    const terminal = state.outputTerminalInstance;
    fitAgentOutputTerminal();
    const unchanged = state.outputRenderedPane === state.activePane
      && state.outputRenderedSnapshot === snapshot
      && state.outputRenderedColumns === terminal.cols;
    const selectionLocksSnapshot = agentOutputSelectionLocked(terminal)
      && state.outputRenderedPane === state.activePane;
    if (!unchanged && !selectionLocksSnapshot) {
      clearAgentOutputSelection(terminal);
      const preserveScroll = state.userScrolledUp;
      const wasAtTop = terminal.buffer.active.viewportY <= 1;
      const distanceFromBottom = Math.max(
        0,
        terminal.buffer.active.baseY - terminal.buffer.active.viewportY,
      );
      const renderId = ++state.outputRenderId;
      state.outputRenderedPane = state.activePane;
      state.outputRenderedSnapshot = snapshot;
      state.outputRenderedColumns = terminal.cols;
      window.requestAnimationFrame(() => {
        if (renderId !== state.outputRenderId) return;
        fitAgentOutputTerminal();
        const fittedSnapshot = fitAgentOutputDividers(snapshot, terminal.cols);
        state.outputRenderedColumns = terminal.cols;
        terminal.write(`\x1bc${fittedSnapshot}`, () => {
          if (renderId !== state.outputRenderId) return;
          if (preserveScroll && distanceFromBottom > 0) {
            terminal.scrollToLine(
              wasAtTop ? 0 : Math.max(0, terminal.buffer.active.baseY - distanceFromBottom),
            );
            scheduleAgentOutputNavigationUpdate();
            return;
          }
          terminal.scrollToBottom();
          scheduleAgentOutputNavigationUpdate();
        });
      });
    }
    scheduleAgentOutputNavigationUpdate();
  } else {
    elements.terminalOutputFallback.hidden = false;
    elements.terminalOutputFallback.textContent = displayContent;
    if (!state.userScrolledUp) {
      window.requestAnimationFrame(() => {
        elements.terminalOutputFallback.scrollTop = elements.terminalOutputFallback.scrollHeight;
        scheduleAgentOutputNavigationUpdate();
      });
    }
    scheduleAgentOutputNavigationUpdate();
  }
  const updatedAt = state.outputUpdatedAt.get(state.activePane);
  elements.lastUpdated.textContent = updatedAt
    ? `更新于 ${updatedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`
    : "等待数据";
}

function refreshOutput() {
  if (!state.activePane || !socketReady()) return;
  send({ type: "read_pane", pane_id: state.activePane, lines: state.lines });
}

function formatPromptImageSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderPromptAttachments() {
  elements.promptAttachments.replaceChildren();
  elements.promptAttachments.hidden = state.promptImages.length === 0;
  elements.attachImageButton.querySelector("span").textContent = state.promptImages.length
    ? `图片 ${state.promptImages.length}`
    : "图片";

  for (const image of state.promptImages) {
    const item = document.createElement("div");
    item.className = "prompt-attachment";

    const thumbnail = document.createElement("img");
    thumbnail.src = image.url;
    thumbnail.alt = "";

    const copy = document.createElement("div");
    copy.className = "prompt-attachment-copy";
    const name = document.createElement("strong");
    name.textContent = image.file.name || "剪贴板图片";
    name.title = name.textContent;
    const meta = document.createElement("span");
    meta.textContent = `${PROMPT_IMAGE_TYPES[image.mediaType]} · ${formatPromptImageSize(image.file.size)}`;
    copy.append(name, meta);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "prompt-attachment-remove";
    remove.textContent = "×";
    remove.disabled = state.promptPending;
    remove.setAttribute("aria-label", `移除图片 ${name.textContent}`);
    remove.addEventListener("click", () => removePromptImage(image.id));

    item.append(thumbnail, copy, remove);
    elements.promptAttachments.append(item);
  }
}

function clearPromptImages() {
  for (const image of state.promptImages) URL.revokeObjectURL(image.url);
  state.promptImages = [];
  elements.promptImageInput.value = "";
  renderPromptAttachments();
  renderPromptState();
}

function removePromptImage(imageId) {
  if (state.promptPending) return;
  const image = state.promptImages.find((item) => item.id === imageId);
  if (!image) return;
  URL.revokeObjectURL(image.url);
  state.promptImages = state.promptImages.filter((item) => item.id !== imageId);
  renderPromptAttachments();
  renderPromptState();
}

function addPromptImages(files) {
  const agent = activeAgent();
  if (String(agent?.agent || "").toLowerCase() !== "codex") {
    showToast("无法添加图片", "图片 Prompt 仅适用于 Codex Agent。", "error");
    return;
  }

  const reasons = new Set();
  let totalBytes = state.promptImages.reduce((total, image) => total + image.file.size, 0);
  for (const file of Array.from(files || [])) {
    if (state.promptImages.length >= PROMPT_IMAGE_MAX_COUNT) {
      reasons.add(`一次最多添加 ${PROMPT_IMAGE_MAX_COUNT} 张图片`);
      break;
    }
    const mediaType = String(file?.type || "").toLowerCase();
    if (!Object.hasOwn(PROMPT_IMAGE_TYPES, mediaType)) {
      reasons.add("仅支持 PNG、JPEG 和 WebP");
      continue;
    }
    if (!Number.isFinite(file.size) || file.size <= 0 || file.size > PROMPT_IMAGE_MAX_BYTES) {
      reasons.add(`单张图片不能超过 ${PROMPT_IMAGE_MAX_BYTES / (1024 * 1024)} MB`);
      continue;
    }
    if (totalBytes + file.size > PROMPT_IMAGE_TOTAL_MAX_BYTES) {
      reasons.add(`图片总大小不能超过 ${PROMPT_IMAGE_TOTAL_MAX_BYTES / (1024 * 1024)} MB`);
      continue;
    }
    state.promptImageSequence += 1;
    state.promptImages.push({
      id: state.promptImageSequence,
      file,
      mediaType,
      url: URL.createObjectURL(file),
    });
    totalBytes += file.size;
  }
  elements.promptImageInput.value = "";
  renderPromptAttachments();
  renderPromptState();
  if (reasons.size) showToast("部分图片未添加", [...reasons].join("；"), "error");
}

function encodePromptImage(image) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const value = typeof reader.result === "string" ? reader.result : "";
      const separator = value.indexOf(",");
      if (separator < 0) {
        reject(new Error("图片编码失败"));
        return;
      }
      resolve({
        name: image.file.name || "image",
        media_type: image.mediaType,
        data: value.slice(separator + 1),
      });
    }, { once: true });
    reader.addEventListener("error", () => reject(reader.error || new Error("图片读取失败")), { once: true });
    reader.readAsDataURL(image.file);
  });
}

function resetPendingPromptState() {
  state.promptPending = false;
  state.pendingPromptText = "";
  state.pendingPromptMode = "";
  state.pendingPromptImageIds = [];
  state.pendingPromptSubmissionId = 0;
  renderPromptAttachments();
  renderPromptState();
}

function renderPromptState() {
  const agent = activeAgent();
  const ready = Boolean(agent) && socketReady() && !state.promptPending;
  const hasText = Boolean(elements.promptInput.value.trim());
  const hasImages = state.promptImages.length > 0;
  const isCodex = String(agent?.agent || "").toLowerCase() === "codex";
  const blocked = normalizedStatus(agent) === "blocked";
  const canCache = ready && isCodex && !hasImages && normalizedStatus(agent) === "working" && hasText;
  elements.promptInput.disabled = !Boolean(agent) || state.promptPending;
  elements.promptImageInput.disabled = !Boolean(agent) || !isCodex || state.promptPending;
  elements.attachImageButton.disabled = elements.promptImageInput.disabled;
  elements.attachImageButton.title = !isCodex
    ? "图片 Prompt 仅适用于 Codex Agent"
    : `最多 ${PROMPT_IMAGE_MAX_COUNT} 张，每张 ${PROMPT_IMAGE_MAX_BYTES / (1024 * 1024)} MB`;
  elements.sendPromptButton.disabled = !ready || (!hasText && !hasImages) || (blocked && hasImages);
  elements.queuePromptButton.disabled = !canCache;
  elements.sendPromptButton.querySelector("span").textContent = state.promptPending && state.pendingPromptMode !== "queue"
    ? "正在发送…"
    : (blocked ? "回答 Prompt" : "发送 Prompt");
  elements.queuePromptButton.querySelector("span").textContent = state.promptPending && state.pendingPromptMode === "queue"
    ? "正在缓存…"
    : "Tab 缓存";
  elements.queuePromptButton.title = !isCodex
    ? "Tab 缓存仅适用于 Codex Agent"
    : (hasImages
      ? "附图 Prompt 请使用发送按钮"
    : (normalizedStatus(agent) === "working"
      ? "通过 Tab 缓存到 Codex 队列"
      : "仅在 Agent 工作中可用"));
}

async function submitPrompt(mode = "send") {
  const text = elements.promptInput.value.trim();
  const images = [...state.promptImages];
  if ((!text && !images.length) || !state.activePane || state.promptPending) return;
  const queue = mode === "queue";
  const agent = activeAgent();
  const isCodex = String(agent?.agent || "").toLowerCase() === "codex";
  if (queue && String(agent?.agent || "").toLowerCase() !== "codex") {
    showToast("暂时无法缓存", "Tab 缓存仅适用于 Codex Agent。", "error");
    return;
  }
  if (images.length && !isCodex) {
    showToast("无法发送图片", "图片 Prompt 仅适用于 Codex Agent。", "error");
    return;
  }
  if (queue && images.length) {
    showToast("无法缓存图片", "附图 Prompt 请使用发送按钮。", "error");
    return;
  }
  if (queue && normalizedStatus(agent) !== "working") {
    showToast("暂时无法缓存", "Agent 当前不在工作中，请使用发送 Prompt。", "error");
    return;
  }
  const blockedResponse = !queue && normalizedStatus(agent) === "blocked";
  if (blockedResponse && images.length) {
    showToast("当前不能发送图片", "Agent 正在等待交互回答，请先用文字完成当前 Prompt。", "error");
    return;
  }
  if (blockedResponse && !agent?.promptId) {
    showToast("Prompt 尚未就绪", "请刷新最近输出，等待 Relay 返回当前 Prompt 后再回答。", "error");
    return;
  }
  const type = queue
    ? "agent_prompt_queue"
    : (blockedResponse ? "respond" : "agent_prompt");
  const paneId = state.activePane;
  const submissionId = state.promptSubmissionSequence + 1;
  state.promptSubmissionSequence = submissionId;
  state.promptPending = true;
  state.pendingPromptText = text;
  state.pendingPromptMode = blockedResponse ? "respond" : mode;
  state.pendingPromptImageIds = images.map((image) => image.id);
  state.pendingPromptSubmissionId = submissionId;
  renderPromptAttachments();
  renderPromptState();

  let encodedImages = [];
  try {
    encodedImages = await Promise.all(images.map(encodePromptImage));
  } catch (_) {
    if (state.pendingPromptSubmissionId !== submissionId) return;
    resetPendingPromptState();
    showToast("图片读取失败", "浏览器无法读取所选图片，请移除后重试。", "error");
    return;
  }
  if (!state.promptPending || state.pendingPromptSubmissionId !== submissionId) return;

  const message = { type, pane_id: paneId, text };
  if (encodedImages.length) message.images = encodedImages;
  if (blockedResponse) message.prompt_id = agent.promptId;
  if (!send(message)) resetPendingPromptState();
}

function renderInterruptState() {
  elements.confirmInterruptButton.disabled = state.interruptPending
    || Boolean(state.agentKeyPending)
    || !socketReady();
  elements.confirmInterruptButton.textContent = state.interruptPending ? "正在发送…" : "确认 Interrupt";
}

function confirmInterrupt() {
  if (!state.activePane || state.interruptPending || state.agentKeyPending) return;
  if (!send({ type: "send_keys", pane_id: state.activePane, keys: ["C-c"] })) return;
  state.interruptPending = true;
  renderInterruptState();
  renderAgentKeyControls();
}

function setAgentKeyFeedback(message, type = "", paneId = state.activePane) {
  window.clearTimeout(state.agentKeyFeedbackTimer);
  state.agentKeyFeedback = message;
  state.agentKeyFeedbackType = type;
  state.agentKeyFeedbackPane = message ? paneId : "";
  renderAgentKeyControls();
  if (!message) return;
  state.agentKeyFeedbackTimer = window.setTimeout(() => {
    state.agentKeyFeedback = "";
    state.agentKeyFeedbackType = "";
    state.agentKeyFeedbackPane = "";
    renderAgentKeyControls();
  }, 2200);
}

function setAgentKeyPanelOpen(open) {
  state.agentKeyPanelOpen = Boolean(open && activeAgent());
  if (state.agentKeyPanelOpen) elements.promptInput.blur();
  renderAgentKeyControls();
  if (state.agentKeyPanelOpen && isDesktop()) {
    window.requestAnimationFrame(() => {
      elements.agentKeyButtons[0]?.focus({ preventScroll: true });
    });
  }
}

function handleAgentKeyTimeout(requestId) {
  const pending = state.agentKeyPending;
  if (!pending || pending.requestId !== requestId) return;
  state.agentKeyPending = null;
  state.agentKeyPendingTimer = null;
  setAgentKeyFeedback("未收到 Relay 确认", "error", pending.paneId);
  refreshActionAvailability();
  showToast(
    "未收到按键确认",
    "按键可能已经送达。已解除按钮锁定，请先查看 Agent 输出，再决定是否重试。",
    "error",
  );
  if (state.activePane === pending.paneId) refreshOutput();
}

function renderAgentKeyControls() {
  const agent = activeAgent();
  const pending = state.agentKeyPending;
  const connected = socketReady();
  const enabled = Boolean(agent) && connected && !pending && !state.interruptPending;
  const panelOpen = Boolean(agent) && state.agentKeyPanelOpen;
  let status = panelOpen ? "Agent 交互按键已展开" : "Agent 交互按键已收起";
  let statusType = "";

  if (!connected) status = "Relay 未连接，暂时无法发送按键";
  else if (pending) {
    const key = AGENT_INTERACTION_KEYS[pending.key];
    status = pending.paneId === state.activePane
      ? `正在发送 ${key.keycap} ${key.label}…`
      : `正在向 ${pending.agentLabel} 发送按键…`;
  } else if (state.interruptPending) status = "正在发送 Interrupt…";
  else if (state.agentKeyFeedback && state.agentKeyFeedbackPane === state.activePane) {
    status = state.agentKeyFeedback;
    statusType = state.agentKeyFeedbackType;
  }

  elements.agentKeybar.hidden = !panelOpen;
  elements.agentKeybar.setAttribute("aria-busy", String(Boolean(pending)));
  elements.agentKeyToggle.disabled = !agent;
  elements.agentKeyToggle.classList.toggle("is-open", panelOpen);
  elements.agentKeyToggle.classList.toggle("is-pending", Boolean(pending));
  elements.agentKeyToggle.setAttribute("aria-expanded", String(panelOpen));
  elements.agentKeyToggle.setAttribute(
    "aria-label",
    panelOpen ? "收起 Agent 交互按键" : "显示 Agent 交互按键",
  );
  elements.agentKeyToggle.title = panelOpen ? "收起交互按键" : "交互按键";
  elements.agentKeyStatus.textContent = status;
  elements.agentKeyStatus.classList.toggle("is-success", statusType === "success");
  elements.agentKeyStatus.classList.toggle("is-error", statusType === "error");
  for (const button of elements.agentKeyButtons) {
    const isPending = pending?.key === button.dataset.agentKey;
    button.disabled = !enabled;
    button.classList.toggle("is-pending", isPending);
  }
}

function sendAgentInteractionKey(keyName) {
  const key = AGENT_INTERACTION_KEYS[keyName];
  const agent = activeAgent();
  if (!key || !agent || state.agentKeyPending || state.interruptPending) return;

  // A button tap should close the Android soft keyboard so the Agent output
  // remains visible while navigating a terminal prompt.
  elements.promptInput.blur();
  state.agentKeySequence += 1;
  const requestId = `agent-key-${Date.now().toString(36)}-${state.agentKeySequence}`;
  if (!send({
    type: "send_keys",
    pane_id: agent.pane_id,
    keys: [keyName],
    request_id: requestId,
  })) return;

  window.clearTimeout(state.agentKeyFeedbackTimer);
  state.agentKeyFeedback = "";
  state.agentKeyFeedbackType = "";
  state.agentKeyFeedbackPane = "";
  state.agentKeyPending = {
    requestId,
    key: keyName,
    paneId: agent.pane_id,
    agentLabel: agentLabel(agent),
  };
  window.clearTimeout(state.agentKeyPendingTimer);
  state.agentKeyPendingTimer = window.setTimeout(
    () => handleAgentKeyTimeout(requestId),
    AGENT_KEY_ACK_TIMEOUT_MS,
  );
  refreshActionAvailability();
}

async function copyOutput() {
  const terminal = state.outputTerminalInstance;
  const selection = terminalSelectedText(terminal);
  if (selection) {
    const copied = await copyText(selection, "已复制选区", "Agent 输出选区已复制到剪贴板。");
    if (copied) clearAgentOutputSelection(terminal);
    return;
  }
  const content = state.activePane ? state.outputs.get(state.activePane) : "";
  if (!content) {
    showToast("暂无可复制内容", "先刷新当前 Agent 的输出。", "error");
    return;
  }
  openTerminalCopyDialog({
    title: "Agent 最近输出 · 复制内容",
    description: "这是当前最近输出的纯文本快照；手机可长按选择，PC 可拖选或使用 Ctrl/⌘+C。",
    content,
    source: "agent",
    status: `${content.split("\n").length} 行 · Agent 最近输出`,
  });
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
  if (!agentSourceById(state.selectedSource)) {
    state.selectedSource = agentSourceById("local")?.id
      || state.agentSources.find(agentSourceUsable)?.id
      || state.agentSources[0]?.id
      || "local";
  }
  updateCounter(elements.initialPromptInput, elements.initialPromptCount);
  renderAgentSourcePicker();
  renderDirectoryBrowser();
  elements.newAgentDialog.showModal();
  browseDirectory(null);
}

function browseDirectory(path) {
  const source = agentSourceById(state.selectedSource);
  if (!socketReady() || !agentSourceUsable(source)) return;
  state.directoryPending = true;
  renderDirectoryBrowser();
  const message = { type: "list_directories", source_id: state.selectedSource };
  if (path) message.path = path;
  send(message);
}

function renderDirectoryBrowser() {
  elements.directoryList.replaceChildren();
  const listing = state.directory;
  const sourceUsable = agentSourceUsable(agentSourceById(state.selectedSource));
  elements.directoryPath.textContent = listing?.display_path || "配置的允许目录";
  elements.directoryUpButton.disabled = state.directoryPending || !listing?.parent;
  elements.directoryRefreshButton.disabled = state.directoryPending || !socketReady() || !sourceUsable;
  elements.directoryNote.hidden = !listing?.truncated;
  renderAgentSourcePicker();

  if (state.directoryPending) {
    elements.directoryList.append(directoryMessage("正在安全读取目录…", "directory-loading"));
  } else if (!listing) {
    elements.directoryList.append(directoryMessage("打开对话框后会显示允许访问的目录。", "directory-empty"));
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
  elements.selectDirectoryButton.disabled = state.directoryPending || !sourceUsable || !listing?.can_start_agent;
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

function scheduleWorkspaceFileRequestTimeout(operation, requestId) {
  window.setTimeout(() => {
    if (operation === "list") {
      if (!state.fileListingPending || state.fileListingRequestId !== requestId) return;
      state.fileListingPending = false;
      state.fileListingError = state.fileListing
        ? ""
        : "读取 Workspace 超时。请检查主机连接或重启 Relay 后重试。";
      renderWorkspaceFileBrowser();
      showToast("Workspace 读取超时", "Relay 未在预期时间内返回目录列表，请检查主机连接后重试。", "error");
      return;
    }
    if (operation === "read") {
      if (!state.fileReadPending || state.fileReadRequestId !== requestId) return;
      state.fileReadPending = false;
      if (state.activeFile) {
        state.activeFile.readError = "读取超时，请检查主机连接后重试";
      }
      renderWorkspaceFileViewer();
      showToast("文件读取超时", "Relay 未在预期时间内返回文件内容。", "error");
      return;
    }
    if (operation !== "download") return;
    if (!state.fileDownloadPending || state.fileDownloadRequestId !== requestId) return;
    state.fileDownloadPending = false;
    renderWorkspaceFileViewer();
    showToast("下载准备超时", "Relay 未能及时生成下载地址，请稍后重试。", "error");
  }, WORKSPACE_FILE_REQUEST_TIMEOUT_MS);
}

function ensureWorkspaceFileBrowser() {
  if (state.activeView !== "files" || state.fileListing || state.fileListingPending) return;
  if (workspaceFilesUnavailable()) {
    state.fileListingError = WORKSPACE_FILE_FEATURE_UNAVAILABLE;
    renderWorkspaceFileBrowser();
    return;
  }
  if (!workspaceFilesSupported()) return;
  const source = fileSourceById(state.fileSource);
  if (socketReady() && fileSourceUsable(source)) browseWorkspaceFiles(null);
}

function browseWorkspaceFiles(path) {
  if (!workspaceFilesSupported()) {
    if (workspaceFilesUnavailable()) {
      state.fileListingError = WORKSPACE_FILE_FEATURE_UNAVAILABLE;
      renderWorkspaceFileBrowser();
      showToast("Files 后台尚未更新", "请重启 Herdr Relay 服务；刷新网页本身无法热重载后台。", "error");
    }
    return;
  }
  const source = fileSourceById(state.fileSource);
  if (!socketReady() || !fileSourceUsable(source)) return;
  const nextPath = typeof path === "string" ? path : "";
  const navigating = !state.fileListing || nextPath !== state.fileListing.path;
  if (navigating) {
    state.fileListing = null;
    clearWorkspaceFileSelection(false);
  }
  state.fileListingPending = true;
  state.fileListingError = "";
  state.fileListingRequestId += 1;
  const requestId = state.fileListingRequestId;
  renderWorkspaceFileBrowser();
  const message = {
    type: "list_workspace_files",
    source_id: state.fileSource,
    request_id: requestId,
  };
  if (nextPath) message.path = nextPath;
  if (!send(message)) {
    state.fileListingPending = false;
    renderWorkspaceFileBrowser();
    return;
  }
  scheduleWorkspaceFileRequestTimeout("list", requestId);
}

function renderWorkspaceFileBrowser() {
  renderFileSourcePicker();
  const listing = state.fileListing;
  const sourceUsable = fileSourceUsable(fileSourceById(state.fileSource));
  const featureUnavailable = workspaceFilesUnavailable();
  elements.fileDirectoryPath.textContent = listing?.display_path || "配置的允许目录";
  elements.fileUpButton.disabled = featureUnavailable
    || state.fileListingPending
    || !listing
    || listing.parent === null
    || listing.parent === undefined;
  elements.fileRefreshButton.disabled = featureUnavailable
    || state.fileListingPending
    || !socketReady()
    || !sourceUsable;
  elements.fileUploadButton.disabled = featureUnavailable
    || !state.fileUploadSupported
    || state.fileListingPending
    || state.fileUploadPending
    || !socketReady()
    || !sourceUsable
    || !listing?.path;
  elements.fileUploadButton.title = !state.fileUploadSupported
    ? "上传需要 Remote Shell 授权"
    : (!listing?.path ? "先进入一个具体目录" : "上传到当前目录");
  elements.filePathJumpInput.disabled = featureUnavailable || state.fileListingPending;
  elements.filePathJumpForm.querySelector("button").disabled = featureUnavailable
    || state.fileListingPending
    || !socketReady()
    || !sourceUsable;
  elements.fileSearchInput.disabled = featureUnavailable;
  elements.fileListNote.hidden = featureUnavailable || !listing?.truncated;
  elements.fileList.classList.toggle("is-loading", state.fileListingPending && Boolean(listing));
  elements.fileList.setAttribute("aria-busy", String(state.fileListingPending));

  const query = state.fileQuery.trim().toLocaleLowerCase();
  const entries = (listing?.entries || []).filter((entry) => {
    if (!query) return true;
    return [entry.name, entry.display_path, entry.language]
      .filter(Boolean)
      .some((value) => String(value).toLocaleLowerCase().includes(query));
  });
  const nodes = entries.map(createWorkspaceFileEntry);
  if (!nodes.length) {
    let text = "此目录中没有可显示的文件。";
    if (state.fileListingError) text = state.fileListingError;
    else if (state.fileListingPending && !listing) text = "正在安全读取目录…";
    else if (!listing) text = "连接主机后会显示允许访问的目录。";
    else if (query) text = "当前目录中没有匹配的项目。";
    const messageClass = state.fileListingError
      ? "is-error"
      : (state.fileListingPending ? "is-loading" : "");
    nodes.push(workspaceFileListMessage(text, messageClass));
  }
  elements.fileList.replaceChildren(...nodes);
}

function workspaceFileListMessage(text, extraClass = "") {
  const item = document.createElement("li");
  item.className = `file-list-message ${extraClass}`.trim();
  item.textContent = text;
  return item;
}

function createWorkspaceFileEntry(entry) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  const directory = entry.kind === "directory";
  const selected = !directory && state.activeFile?.path === entry.path;
  button.type = "button";
  button.className = `file-entry is-${directory ? "directory" : "file"}${selected ? " is-selected" : ""}`;
  button.setAttribute(
    "aria-label",
    directory ? `打开目录 ${entry.name}` : `查看文件 ${entry.name}`,
  );
  button.addEventListener("click", () => {
    if (directory) browseWorkspaceFiles(entry.path);
    else selectWorkspaceFile(entry);
  });

  const icon = document.createElement("span");
  icon.className = "file-entry-icon";
  icon.setAttribute("aria-hidden", "true");
  if (directory) icon.textContent = entry.is_repo ? "⌘" : "▱";
  else if (entry.preview_kind === "markdown") icon.textContent = "M↓";
  else if (entry.preview_kind === "html") icon.textContent = "<>";
  else if (entry.previewable) icon.textContent = "{ }";
  else icon.textContent = "↓";

  const main = document.createElement("span");
  main.className = "file-entry-main";
  const name = document.createElement("strong");
  name.textContent = entry.name;
  const meta = document.createElement("span");
  if (directory) {
    meta.textContent = entry.is_repo ? "Git repository" : "文件夹";
  } else {
    const preview = entry.preview_kind === "markdown"
      ? "Markdown"
      : (entry.preview_kind === "html" ? "HTML" : (entry.language ? entry.language : "仅下载"));
    meta.textContent = `${formatFileSize(entry.size)} · ${preview}`;
  }
  main.append(name, meta);

  const badge = document.createElement("span");
  badge.className = "file-entry-badge";
  badge.textContent = directory
    ? (entry.is_repo ? "GIT" : "打开")
    : (entry.previewable ? "阅读" : (entry.downloadable === false ? "过大" : "下载"));
  button.append(icon, main, badge);
  item.append(button);
  return item;
}

function resetWorkspaceFileOperations() {
  state.fileReadPending = false;
  state.fileReadRequestId += 1;
  state.fileDownloadPending = false;
  state.fileDownloadRequestId += 1;
  resetWorkspaceFileReaderRender();
}

function resetWorkspaceFileReaderRender() {
  state.fileReaderRenderedMode = "";
  state.fileReaderRenderedSource = "";
  state.fileReaderRenderedPath = "";
  state.fileReaderRenderedSignature = "";
  state.fileReaderRenderedContent = null;
  elements.fileHtmlPreview.srcdoc = "";
}

function selectWorkspaceFile(entry) {
  resetWorkspaceFileOperations();
  state.fileHtmlSourceMode = false;
  state.activeFile = {
    ...entry,
    source_id: state.fileSource,
    source_label: state.fileListing?.source_label || fileSourceById(state.fileSource)?.label || "本机",
    content: null,
    readError: "",
  };
  document.body.classList.add("file-open");
  renderWorkspaceFileBrowser();
  renderWorkspaceFileViewer();
  if (entry.previewable) readWorkspaceFile(entry.path);
}

function openWorkspaceFilePath(path) {
  if (!path) return;
  resetWorkspaceFileOperations();
  const name = path.split("/").filter(Boolean).at(-1) || path;
  const markdown = /\.(?:md|markdown|mdown|mkd)$/i.test(name);
  const html = /\.html?$/i.test(name);
  state.fileHtmlSourceMode = false;
  state.activeFile = {
    name,
    path,
    display_path: path,
    kind: "file",
    size: 0,
    previewable: true,
    preview_kind: markdown ? "markdown" : (html ? "html" : "code"),
    language: html ? "xml" : "",
    downloadable: true,
    source_id: state.fileSource,
    source_label: fileSourceById(state.fileSource)?.label || "本机",
    content: null,
    readError: "",
  };
  document.body.classList.add("file-open");
  renderWorkspaceFileBrowser();
  renderWorkspaceFileViewer();
  readWorkspaceFile(path);
}

function readWorkspaceFile(path) {
  if (!path || !socketReady()) return;
  state.fileReadPending = true;
  state.fileReadRequestId += 1;
  const requestId = state.fileReadRequestId;
  renderWorkspaceFileViewer();
  if (!send({
    type: "read_workspace_file",
    source_id: state.fileSource,
    path,
    request_id: requestId,
  })) {
    state.fileReadPending = false;
    renderWorkspaceFileViewer();
    return;
  }
  scheduleWorkspaceFileRequestTimeout("read", requestId);
}

function clearWorkspaceFileSelection(render = true) {
  resetWorkspaceFileOperations();
  state.activeFile = null;
  document.body.classList.remove("file-open");
  if (render) {
    renderWorkspaceFileBrowser();
    renderWorkspaceFileViewer();
  }
}

function formatFileSize(value) {
  const bytes = Math.max(0, Number(value) || 0);
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let size = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && size >= 1024; index += 1) {
    size /= 1024;
    unit = units[index];
  }
  return `${size >= 10 ? size.toFixed(0) : size.toFixed(1)} ${unit}`;
}

function workspaceUploadTargetAvailable() {
  return Boolean(
    state.fileUploadSupported
    && socketReady()
    && fileSourceUsable(fileSourceById(state.fileSource))
    && state.fileListing?.path
  );
}

function captureWorkspaceUploadTarget() {
  if (!workspaceUploadTargetAvailable()) return null;
  const source = fileSourceById(state.fileSource);
  return {
    sourceId: state.fileSource,
    sourceLabel: state.fileListing?.source_label || source?.label || "本机",
    path: state.fileListing.path,
    displayPath: state.fileListing.display_path || state.fileListing.path,
  };
}

function requestWorkspaceUploadFiles() {
  if (!state.fileUploadSupported) {
    showToast("上传未授权", "Files 上传需要 Remote Shell 明确用户权限。", "error");
    return;
  }
  const target = elements.fileUploadDialog.open
    ? state.fileUploadTarget
    : captureWorkspaceUploadTarget();
  if (!target) {
    showToast("请选择上传目录", "先在 Files 中进入一个具体目录。", "error");
    return;
  }
  if (state.fileUploadPending) return;
  state.fileUploadTarget = target;
  elements.fileUploadInput.click();
}

function workspaceUploadFileAllowed(file) {
  if (!(file instanceof File)) return "不是可上传的文件";
  if (!file.name || file.name.startsWith(".") || /[\\/\x00-\x1f\x7f]/u.test(file.name)) {
    return "隐藏文件或文件名不受支持";
  }
  if (!Number.isSafeInteger(file.size) || file.size < 0) return "文件大小无效";
  if (file.size > state.fileUploadMaxBytes) {
    return `超过单文件 ${formatFileSize(state.fileUploadMaxBytes)} 限制`;
  }
  return "";
}

function addWorkspaceUploadFiles(files) {
  const selected = [...(files || [])];
  if (!selected.length || state.fileUploadPending) return;
  if (!state.fileUploadSupported || !socketReady()) {
    showToast("上传暂不可用", "请等待 Relay 重新连接，并确认当前用户拥有 Remote Shell 权限。", "error");
    return;
  }
  if (!state.fileUploadTarget) state.fileUploadTarget = captureWorkspaceUploadTarget();
  if (!state.fileUploadTarget) {
    showToast("请选择上传目录", "先在 Files 中进入一个具体目录。", "error");
    return;
  }

  let rejected = 0;
  let firstError = "";
  const remaining = Math.max(0, WORKSPACE_UPLOAD_MAX_FILES - state.fileUploadEntries.length);
  for (const file of selected.slice(0, remaining)) {
    const error = workspaceUploadFileAllowed(file);
    if (error) {
      rejected += 1;
      firstError ||= `${file.name || "未命名文件"}：${error}`;
      continue;
    }
    state.fileUploadSequence += 1;
    state.fileUploadEntries.push({
      id: state.fileUploadSequence,
      file,
      status: "queued",
      received: 0,
      result: null,
      error: "",
    });
  }
  if (selected.length > remaining) {
    rejected += selected.length - remaining;
    firstError ||= `每批最多选择 ${WORKSPACE_UPLOAD_MAX_FILES} 个文件`;
  }
  elements.fileUploadInput.value = "";
  renderWorkspaceUploadDialog();
  if (!elements.fileUploadDialog.open && state.fileUploadEntries.length) {
    elements.fileUploadDialog.showModal();
  }
  if (rejected) {
    showToast(
      `${rejected} 个文件未加入`,
      firstError || "请检查文件名称和大小。",
      "error",
    );
  }
}

function workspaceUploadStatusLabel(entry) {
  if (entry.status === "preparing") return "准备中";
  if (entry.status === "uploading") return `${Math.round((entry.received / Math.max(1, entry.file.size)) * 100)}%`;
  if (entry.status === "committing") return "写入中";
  if (entry.status === "complete") return "已完成";
  if (entry.status === "error") return "失败";
  return "等待";
}

function workspaceUploadProgress() {
  const total = state.fileUploadEntries.reduce((sum, entry) => sum + entry.file.size, 0);
  const completed = state.fileUploadEntries.reduce((sum, entry) => {
    if (entry.status === "complete") return sum + entry.file.size;
    if (["uploading", "committing"].includes(entry.status)) {
      return sum + Math.min(entry.received, entry.file.size);
    }
    return sum;
  }, 0);
  return {total, completed};
}

function renderWorkspaceUploadDialog() {
  if (!elements.fileUploadDialog) return;
  const target = state.fileUploadTarget;
  elements.fileUploadTargetHost.textContent = target?.sourceLabel || "尚未选择主机";
  elements.fileUploadTargetPath.textContent = target?.displayPath || "先进入一个具体目录";
  elements.fileUploadList.replaceChildren(...state.fileUploadEntries.map((entry) => {
    const item = document.createElement("li");
    item.className = `file-upload-item is-${entry.status}`;
    const copy = document.createElement("div");
    copy.className = "file-upload-item-copy";
    const name = document.createElement("strong");
    name.textContent = entry.result?.name || entry.file.name;
    const detail = document.createElement("span");
    detail.textContent = entry.error
      || entry.result?.display_path
      || formatFileSize(entry.file.size);
    copy.append(name, detail);
    const status = document.createElement("span");
    status.className = "file-upload-item-status";
    status.textContent = workspaceUploadStatusLabel(entry);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "file-upload-remove";
    remove.setAttribute("aria-label", `移除 ${entry.file.name}`);
    remove.textContent = "×";
    remove.disabled = state.fileUploadPending || entry.status === "complete";
    remove.addEventListener("click", () => {
      state.fileUploadEntries = state.fileUploadEntries.filter((itemEntry) => itemEntry.id !== entry.id);
      renderWorkspaceUploadDialog();
    });
    item.append(copy, status, remove);
    return item;
  }));

  const {total, completed} = workspaceUploadProgress();
  const percent = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  const completeCount = state.fileUploadEntries.filter((entry) => entry.status === "complete").length;
  const errorCount = state.fileUploadEntries.filter((entry) => entry.status === "error").length;
  const pendingCount = state.fileUploadEntries.length - completeCount;
  elements.fileUploadProgress.hidden = !total && !state.fileUploadPending;
  elements.fileUploadProgress.setAttribute("aria-valuenow", String(percent));
  elements.fileUploadProgressBar.style.width = `${percent}%`;
  elements.fileUploadProgressCopy.textContent = total
    ? `${formatFileSize(completed)} / ${formatFileSize(total)} · ${percent}%`
    : "等待上传";
  if (state.fileUploadPending && state.fileUploadActive) {
    const entry = state.fileUploadActive.entry;
    elements.fileUploadStatus.textContent = entry.status === "committing"
      ? `正在写入 ${entry.file.name}…`
      : `正在上传 ${entry.file.name}…`;
  } else if (completeCount && pendingCount === 0) {
    elements.fileUploadStatus.textContent = `${completeCount} 个文件已上传到当前目录。`;
  } else if (errorCount) {
    elements.fileUploadStatus.textContent = `${errorCount} 个文件失败，可点击重试未完成文件。`;
  } else if (state.fileUploadEntries.length) {
    elements.fileUploadStatus.textContent = `${state.fileUploadEntries.length} 个文件，共 ${formatFileSize(total)}。`;
  } else {
    elements.fileUploadStatus.textContent = "请选择至少一个文件。";
  }

  const committing = state.fileUploadActive?.entry.status === "committing";
  const selectionDisabled = state.fileUploadPending
    || !state.fileUploadSupported
    || !socketReady();
  elements.fileUploadDropzone.classList.toggle("is-disabled", selectionDisabled);
  elements.fileUploadDropzone.setAttribute("aria-disabled", String(selectionDisabled));
  elements.fileUploadInput.disabled = selectionDisabled;
  elements.fileUploadOverwrite.disabled = state.fileUploadPending;
  elements.fileUploadCloseButton.disabled = committing;
  elements.fileUploadCancelButton.disabled = committing;
  elements.fileUploadCancelButton.textContent = state.fileUploadPending ? "取消上传" : "关闭";
  elements.fileUploadSubmitButton.disabled = state.fileUploadPending
    || !state.fileUploadSupported
    || !socketReady()
    || !target
    || !state.fileUploadEntries.some((entry) => entry.status !== "complete");
  const hasIncomplete = state.fileUploadEntries.some((entry) => entry.status !== "complete");
  elements.fileUploadSubmitButton.textContent = completeCount && !hasIncomplete
    ? "上传完成"
    : (errorCount || completeCount ? "上传未完成文件" : "开始上传");
}

function bytesToBase64(bytes) {
  const segments = [];
  const segmentSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += segmentSize) {
    segments.push(String.fromCharCode(...bytes.subarray(offset, offset + segmentSize)));
  }
  return window.btoa(segments.join(""));
}

function sendWorkspaceUploadStart(entry) {
  const target = state.fileUploadTarget;
  if (!target || !socketReady()) return false;
  state.fileUploadSequence += 1;
  const uploadId = state.fileUploadSequence;
  entry.status = "preparing";
  entry.received = 0;
  entry.error = "";
  state.fileUploadActive = {
    uploadId,
    entry,
    offset: 0,
    sentEnd: 0,
    chunkBytes: state.fileUploadChunkBytes,
  };
  renderWorkspaceUploadDialog();
  return send({
    type: "workspace_upload_start",
    upload_id: uploadId,
    source_id: target.sourceId,
    directory: target.path,
    name: entry.file.name,
    size: entry.file.size,
    overwrite: elements.fileUploadOverwrite.checked,
  });
}

function startNextWorkspaceUpload() {
  if (!state.fileUploadPending || state.fileUploadActive) return;
  const entry = state.fileUploadEntries.find((candidate) => candidate.status === "queued");
  if (!entry) {
    state.fileUploadPending = false;
    renderWorkspaceUploadDialog();
    renderWorkspaceFileBrowser();
    const completed = state.fileUploadEntries.filter((candidate) => candidate.status === "complete");
    if (completed.length) {
      browseWorkspaceFiles(state.fileUploadTarget?.path || state.fileListing?.path || "");
      showToast(
        "文件上传完成",
        `${completed.length} 个文件已写入 ${state.fileUploadTarget?.displayPath || "当前目录"}。`,
        "success",
      );
    }
    return;
  }
  if (!sendWorkspaceUploadStart(entry)) {
    entry.status = "error";
    entry.error = "Relay 连接不可用";
    state.fileUploadActive = null;
    state.fileUploadPending = false;
    renderWorkspaceUploadDialog();
  }
}

function startWorkspaceUpload() {
  if (state.fileUploadPending) return;
  if (
    !state.fileUploadSupported
    || !socketReady()
    || !state.fileUploadTarget
    || !fileSourceUsable(fileSourceById(state.fileUploadTarget.sourceId))
  ) {
    showToast("上传目标不可用", "请确认 Relay 已连接，并重新选择 Files 主机和目录。", "error");
    return;
  }
  for (const entry of state.fileUploadEntries) {
    if (entry.status === "error") {
      entry.status = "queued";
      entry.received = 0;
      entry.error = "";
    }
  }
  if (!state.fileUploadEntries.some((entry) => entry.status === "queued")) return;
  state.fileUploadPending = true;
  state.fileUploadActive = null;
  renderWorkspaceUploadDialog();
  renderWorkspaceFileBrowser();
  startNextWorkspaceUpload();
}

async function sendNextWorkspaceUploadChunk() {
  const active = state.fileUploadActive;
  if (!state.fileUploadPending || !active) return;
  const {entry, uploadId} = active;
  if (active.offset >= entry.file.size) {
    send({type: "workspace_upload_finish", upload_id: uploadId});
    return;
  }
  const end = Math.min(entry.file.size, active.offset + active.chunkBytes);
  let bytes;
  try {
    bytes = new Uint8Array(await entry.file.slice(active.offset, end).arrayBuffer());
  } catch (_) {
    abortActiveWorkspaceUpload({upload_id: uploadId, message: "浏览器无法读取所选文件"});
    return;
  }
  if (!state.fileUploadPending || state.fileUploadActive?.uploadId !== uploadId) return;
  entry.status = "uploading";
  active.sentEnd = end;
  renderWorkspaceUploadDialog();
  if (!send({
    type: "workspace_upload_chunk",
    upload_id: uploadId,
    offset: active.offset,
    data: bytesToBase64(bytes),
  })) {
    abortActiveWorkspaceUpload({upload_id: uploadId, message: "文件分块发送失败"});
  }
}

function handleWorkspaceUploadReady(message) {
  const active = state.fileUploadActive;
  if (!active || Number(message.upload_id) !== active.uploadId) return;
  const serverChunkBytes = Number(message.chunk_bytes);
  if (Number.isSafeInteger(serverChunkBytes) && serverChunkBytes > 0) {
    active.chunkBytes = Math.min(serverChunkBytes, state.fileUploadChunkBytes);
  }
  if (active.entry.file.size === 0) {
    send({type: "workspace_upload_finish", upload_id: active.uploadId});
    return;
  }
  sendNextWorkspaceUploadChunk();
}

function handleWorkspaceUploadProgress(message) {
  const active = state.fileUploadActive;
  if (!active || Number(message.upload_id) !== active.uploadId) return;
  const received = Number(message.received);
  if (
    !Number.isSafeInteger(received)
    || received <= active.offset
    || received !== active.sentEnd
    || received > active.entry.file.size
  ) {
    abortActiveWorkspaceUpload({
      upload_id: active.uploadId,
      message: "Relay 返回了无效的上传进度",
    });
    return;
  }
  active.offset = received;
  active.entry.received = received;
  renderWorkspaceUploadDialog();
  sendNextWorkspaceUploadChunk();
}

function handleWorkspaceUploadCommitting(message) {
  const active = state.fileUploadActive;
  if (!active || Number(message.upload_id) !== active.uploadId) return;
  active.entry.status = "committing";
  active.entry.received = active.entry.file.size;
  renderWorkspaceUploadDialog();
}

function handleWorkspaceUploadComplete(message) {
  const active = state.fileUploadActive;
  if (!active || Number(message.upload_id) !== active.uploadId) return;
  active.entry.status = "complete";
  active.entry.received = active.entry.file.size;
  active.entry.result = {
    name: String(message.name || active.entry.file.name),
    path: String(message.path || ""),
    display_path: String(message.display_path || message.path || ""),
  };
  state.fileUploadActive = null;
  renderWorkspaceUploadDialog();
  startNextWorkspaceUpload();
}

function handleWorkspaceUploadError(message) {
  const active = state.fileUploadActive;
  const uploadId = Number(message.upload_id);
  if (active && uploadId && uploadId !== active.uploadId) return;
  if (active) {
    active.entry.status = "error";
    active.entry.error = message.message || "上传失败";
  }
  state.fileUploadActive = null;
  state.fileUploadPending = false;
  renderWorkspaceUploadDialog();
  renderWorkspaceFileBrowser();
  showToast("文件上传失败", message.message || "请检查目标目录和主机连接后重试。", "error");
}

function abortActiveWorkspaceUpload(message) {
  const active = state.fileUploadActive;
  if (active && socketReady()) {
    send({type: "workspace_upload_cancel", upload_id: active.uploadId});
  }
  handleWorkspaceUploadError(message);
}

function handleWorkspaceUploadCancelled(message) {
  const active = state.fileUploadActive;
  if (active && Number(message.upload_id) !== active.uploadId) return;
  if (active) {
    active.entry.status = "error";
    active.entry.error = "已取消";
  }
  state.fileUploadActive = null;
  state.fileUploadPending = false;
  renderWorkspaceUploadDialog();
  renderWorkspaceFileBrowser();
}

function cancelWorkspaceUpload() {
  const active = state.fileUploadActive;
  if (!state.fileUploadPending || !active) {
    elements.fileUploadDialog.close();
    return;
  }
  if (active.entry.status === "committing") {
    showToast("正在写入目标主机", "远端原子写入完成前不能中断，请稍候。", "error");
    return;
  }
  send({type: "workspace_upload_cancel", upload_id: active.uploadId});
  active.entry.status = "error";
  active.entry.error = "已取消";
  state.fileUploadActive = null;
  state.fileUploadPending = false;
  renderWorkspaceUploadDialog();
  renderWorkspaceFileBrowser();
}

function closeWorkspaceUploadDialog() {
  if (state.fileUploadPending) {
    cancelWorkspaceUpload();
    return;
  }
  elements.fileUploadDialog.close();
}

function resetWorkspaceUploadDialog() {
  if (state.fileUploadPending) return;
  state.fileUploadEntries = [];
  state.fileUploadActive = null;
  state.fileUploadTarget = null;
  elements.fileUploadInput.value = "";
  elements.fileUploadOverwrite.checked = false;
  renderWorkspaceUploadDialog();
}

function workspaceDragContainsFiles(event) {
  return [...(event.dataTransfer?.types || [])].includes("Files");
}

function handleWorkspaceUploadDrop(event) {
  if (!workspaceDragContainsFiles(event)) return;
  event.preventDefault();
  state.fileUploadDragDepth = 0;
  document.querySelector(".file-panel")?.classList.remove("is-upload-dragging");
  elements.fileUploadDropzone.classList.remove("is-dragging");
  if (!state.fileUploadTarget) state.fileUploadTarget = captureWorkspaceUploadTarget();
  addWorkspaceUploadFiles(event.dataTransfer?.files || []);
}

function setWorkspaceReaderStatus(title, detail, spinning = true) {
  elements.fileReaderStatus.hidden = false;
  elements.fileReaderStatus.querySelector(".terminal-spinner").hidden = !spinning;
  elements.fileReaderStatus.querySelector("strong").textContent = title;
  elements.fileReaderStatus.querySelector("span:last-child").textContent = detail;
}

function hideWorkspaceReaderContents() {
  elements.fileReaderStatus.hidden = true;
  elements.fileMarkdownContent.hidden = true;
  elements.fileHtmlPreview.hidden = true;
  elements.fileCodePreview.hidden = true;
  elements.fileDownloadOnly.hidden = true;
}

function workspaceFileReaderNeedsRender(file, mode, signature, content = null) {
  const source = String(file.source_id || state.fileSource || "");
  const path = String(file.path || "");
  if (
    state.fileReaderRenderedMode === mode
    && state.fileReaderRenderedSource === source
    && state.fileReaderRenderedPath === path
    && state.fileReaderRenderedSignature === signature
    && state.fileReaderRenderedContent === content
  ) {
    return false;
  }
  state.fileReaderRenderedMode = mode;
  state.fileReaderRenderedSource = source;
  state.fileReaderRenderedPath = path;
  state.fileReaderRenderedSignature = signature;
  state.fileReaderRenderedContent = content;
  return true;
}

function renderWorkspaceFileViewer() {
  const file = state.activeFile;
  elements.fileDetailEmpty.hidden = Boolean(file);
  elements.fileViewer.hidden = !file;
  if (!file) return;

  const kind = file.preview_kind || file.kind || "file";
  const kindLabel = kind === "markdown"
    ? "MARKDOWN"
    : (kind === "html" ? "HTML" : (file.language ? String(file.language).toLocaleUpperCase() : "FILE"));
  elements.fileAvatar.textContent = kind === "markdown"
    ? "MD"
    : (kind === "html" ? "<>" : (file.previewable ? "{ }" : "↓"));
  elements.fileTitle.textContent = file.name || "文件";
  elements.fileKindBadge.textContent = kindLabel;
  elements.fileKindBadge.className = `file-kind-badge is-${kind === "markdown"
    ? "markdown"
    : (kind === "html" ? "html" : (file.previewable ? "code" : "file"))}`;
  elements.fileMeta.textContent = `${file.source_label || "Workspace"} · ${file.display_path || file.path}`;
  elements.fileHtmlViewButton.hidden = kind !== "html";
  elements.fileHtmlViewButton.disabled = state.fileReadPending || typeof file.content !== "string";
  elements.fileHtmlViewButton.setAttribute("aria-pressed", String(state.fileHtmlSourceMode));
  elements.fileHtmlViewButton.setAttribute(
    "aria-label",
    state.fileHtmlSourceMode ? "渲染 HTML" : "查看 HTML 源码",
  );
  elements.fileHtmlViewButton.title = state.fileHtmlSourceMode ? "渲染 HTML" : "查看 HTML 源码";
  elements.fileHtmlViewButton.querySelector("span").textContent = state.fileHtmlSourceMode ? "渲染" : "源码";
  elements.copyFileButton.disabled = typeof file.content !== "string" || state.fileReadPending;
  elements.downloadFileButton.disabled = !socketReady()
    || file.downloadable === false
    || state.fileDownloadPending;
  elements.downloadFileEmptyButton.disabled = !socketReady()
    || file.downloadable === false
    || state.fileDownloadPending;
  elements.downloadFileButton.querySelector("span").textContent = state.fileDownloadPending ? "准备中…" : "下载";

  if (state.fileReadPending) {
    const signature = String(file.size || 0);
    if (!workspaceFileReaderNeedsRender(file, "loading", signature)) return;
    hideWorkspaceReaderContents();
    elements.fileReaderTitle.textContent = "Secure preview";
    elements.fileReaderMeta.textContent = formatFileSize(file.size);
    setWorkspaceReaderStatus("正在安全读取文件", "内容仍会在 Relay 端校验", true);
    return;
  }

  if (typeof file.content === "string") {
    const details = [
      `${file.line_count || file.content.split("\n").length} 行`,
      formatFileSize(file.size || new Blob([file.content]).size),
    ];
    if (file.kind === "markdown") {
      const signature = [file.language || "", ...details].join("\u0000");
      if (!workspaceFileReaderNeedsRender(file, "markdown", signature, file.content)) return;
      hideWorkspaceReaderContents();
      elements.fileReaderTitle.textContent = "Rendered Markdown";
      elements.fileReaderMeta.textContent = details.join(" · ");
      renderMarkdownWorkspaceFile(file);
    } else if (file.kind === "html") {
      const mode = state.fileHtmlSourceMode ? "html-source" : "html-rendered";
      const signature = [
        file.language || "",
        ...details,
        state.fileHtmlSourceMode ? "source" : resolvedTheme(),
      ].join("\u0000");
      if (!workspaceFileReaderNeedsRender(file, mode, signature, file.content)) return;
      hideWorkspaceReaderContents();
      if (state.fileHtmlSourceMode) {
        elements.fileReaderTitle.textContent = "HTML Source";
        elements.fileReaderMeta.textContent = ["HTML", ...details].join(" · ");
        renderCodeWorkspaceFile({ ...file, language: file.language || "xml" });
      } else {
        elements.fileReaderTitle.textContent = "Sandboxed HTML";
        elements.fileReaderMeta.textContent = [...details, "脚本与外部资源已禁用"].join(" · ");
        renderHtmlWorkspaceFile(file);
      }
    } else {
      const signature = [file.language || "", ...details].join("\u0000");
      if (!workspaceFileReaderNeedsRender(file, "code", signature, file.content)) return;
      hideWorkspaceReaderContents();
      elements.fileReaderTitle.textContent = "Source Preview";
      elements.fileReaderMeta.textContent = [file.language || "plain text", ...details].join(" · ");
      renderCodeWorkspaceFile(file);
    }
    return;
  }

  const downloadCopy = file.readError
    ? `预览失败：${file.readError}。你仍可尝试下载后在本机查看。`
    : (file.preview_reason || "代码、Markdown 与 HTML 之外的普通文件可安全下载到本机查看。");
  const signature = [String(file.size || 0), downloadCopy].join("\u0000");
  if (!workspaceFileReaderNeedsRender(file, "download", signature)) return;
  hideWorkspaceReaderContents();
  elements.fileReaderTitle.textContent = "Download only";
  elements.fileReaderMeta.textContent = formatFileSize(file.size);
  elements.fileDownloadOnly.hidden = false;
  elements.fileDownloadOnlyCopy.textContent = downloadCopy;
}

function renderCodeWorkspaceFile(file) {
  elements.fileCodePreview.hidden = false;
  const code = elements.fileCodeContent;
  code.className = "";
  const language = String(file.language || "");
  const canHighlight = file.content.length <= FILE_HIGHLIGHT_MAX_CHARACTERS
    && window.hljs?.getLanguage?.(language);
  if (!canHighlight) {
    code.textContent = file.content;
    return;
  }
  try {
    code.innerHTML = window.hljs.highlight(file.content, {
      language,
      ignoreIllegals: true,
    }).value;
    code.classList.add("hljs", `language-${language}`);
  } catch (_) {
    code.textContent = file.content;
  }
}

function prioritizeHtmlPreviewMainContent(previewDocument) {
  const body = previewDocument.body;
  const primary = body?.querySelector("main, [role='main']");
  if (!body || !primary || primary.closest("[hidden], [aria-hidden='true']")) return;
  if (primary.textContent.trim().length < 160) return;

  let section = primary;
  while (section.parentElement && section.parentElement !== body) {
    section = section.parentElement;
  }
  if (section.parentElement !== body || section === body.firstElementChild) return;

  section.setAttribute("data-herdr-preview-main", "");
  body.prepend(section);
}

function removeHtmlPreviewExternalImage(image, body) {
  let emptyAncestor = image.parentElement;
  image.remove();
  while (
    emptyAncestor
    && emptyAncestor !== body
    && !emptyAncestor.textContent.trim()
    && emptyAncestor.children.length === 0
  ) {
    const parent = emptyAncestor.parentElement;
    emptyAncestor.remove();
    emptyAncestor = parent;
  }
}

function replaceHtmlPreviewDocument(srcdoc) {
  // Chromium can leave a sandboxed srcdoc document without a layout tree after
  // the iframe was hidden for source view and then reused. Replacing the frame
  // creates a fresh browsing context and makes source/render toggles reliable.
  const preview = elements.fileHtmlPreview.cloneNode(false);
  preview.removeAttribute("srcdoc");
  preview.hidden = false;
  preview.srcdoc = srcdoc;
  elements.fileHtmlPreview.replaceWith(preview);
  elements.fileHtmlPreview = preview;
}

function renderHtmlWorkspaceFile(file) {
  const sanitizer = window.DOMPurify?.sanitize;
  if (typeof sanitizer !== "function" || typeof DOMParser !== "function") {
    elements.fileReaderMeta.textContent += " · 原文模式";
    renderCodeWorkspaceFile({ ...file, language: file.language || "xml" });
    return;
  }
  try {
    const sanitized = sanitizer(file.content, {
      WHOLE_DOCUMENT: true,
      USE_PROFILES: { html: true },
      FORBID_TAGS: [
        "audio", "base", "button", "canvas", "embed", "form", "iframe", "input",
        "link", "math", "meta", "object", "option", "script", "select", "svg",
        "textarea", "video",
      ],
      FORBID_ATTR: ["autofocus", "formaction", "ping", "poster", "srcset"],
    });
    const previewDocument = new DOMParser().parseFromString(sanitized, "text/html");
    const previewTheme = resolvedTheme();
    previewDocument.documentElement.dataset.herdrPreviewTheme = previewTheme;

    previewDocument.querySelectorAll("a[href]").forEach((link) => {
      const href = link.getAttribute("href") || "";
      if (href.startsWith("#")) return;
      link.removeAttribute("href");
      link.title = href
        ? `安全预览已禁用链接：${href}`
        : "安全预览已禁用此链接";
    });
    previewDocument.querySelectorAll("[src]").forEach((node) => {
      const source = node.getAttribute("src") || "";
      const embeddedImage = node.tagName === "IMG"
        && /^data:image\/(?:gif|jpeg|png|webp);base64,/i.test(source);
      if (embeddedImage) return;
      if (node.tagName === "IMG") {
        removeHtmlPreviewExternalImage(node, previewDocument.body);
        return;
      }
      node.removeAttribute("src");
    });
    previewDocument.querySelectorAll("[action], [formaction], [ping], [poster], [srcset]").forEach((node) => {
      for (const attribute of ["action", "formaction", "ping", "poster", "srcset"]) {
        node.removeAttribute(attribute);
      }
    });
    prioritizeHtmlPreviewMainContent(previewDocument);

    const charset = previewDocument.createElement("meta");
    charset.setAttribute("charset", "utf-8");
    const policy = previewDocument.createElement("meta");
    policy.setAttribute("http-equiv", "Content-Security-Policy");
    policy.setAttribute("content", HTML_PREVIEW_CSP);
    const baseStyle = previewDocument.createElement("style");
    baseStyle.textContent = `${HTML_PREVIEW_COMMON_STYLE}\n${previewTheme === "dark"
      ? HTML_PREVIEW_DARK_STYLE
      : HTML_PREVIEW_LIGHT_STYLE}`;
    previewDocument.head.prepend(policy);
    previewDocument.head.prepend(charset);
    previewDocument.head.append(baseStyle);

    replaceHtmlPreviewDocument(`<!doctype html>\n${previewDocument.documentElement.outerHTML}`);
  } catch (_) {
    elements.fileReaderMeta.textContent += " · 原文模式";
    renderCodeWorkspaceFile({ ...file, language: file.language || "xml" });
  }
}

function renderMarkdownWorkspaceFile(file) {
  const parser = window.marked?.parse;
  const sanitizer = window.DOMPurify?.sanitize;
  if (typeof parser !== "function" || typeof sanitizer !== "function") {
    elements.fileReaderMeta.textContent += " · 原文模式";
    renderCodeWorkspaceFile({ ...file, language: "markdown" });
    return;
  }
  try {
    const rendered = parser(file.content, { gfm: true, breaks: false });
    elements.fileMarkdownContent.innerHTML = sanitizer(rendered, {
      USE_PROFILES: { html: true },
      FORBID_TAGS: [
        "audio", "button", "canvas", "embed", "form", "iframe", "img", "input",
        "math", "object", "option", "script", "select", "style", "svg", "textarea", "video",
      ],
      FORBID_ATTR: ["autofocus", "srcset", "style"],
    });
    elements.fileMarkdownContent.hidden = false;
    enhanceWorkspaceMarkdown(elements.fileMarkdownContent, file);
  } catch (_) {
    elements.fileReaderMeta.textContent += " · 原文模式";
    renderCodeWorkspaceFile({ ...file, language: "markdown" });
  }
}

function enhanceWorkspaceMarkdown(root, file) {
  root.querySelectorAll("a[href]").forEach((link) => {
    const href = link.getAttribute("href") || "";
    if (href.startsWith("#")) return;
    if (/^(?:https?:|mailto:)/i.test(href)) {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      return;
    }
    if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith("//")) {
      link.removeAttribute("href");
      return;
    }
    const target = resolveWorkspaceMarkdownPath(file.path, href);
    if (!target) {
      link.removeAttribute("href");
      return;
    }
    link.href = "#";
    link.title = `在文件阅读器中打开 ${target}`;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      openWorkspaceFilePath(target);
    });
  });

  root.querySelectorAll("pre code").forEach((code) => {
    if (!window.hljs || code.textContent.length > MARKDOWN_CODE_HIGHLIGHT_MAX_CHARACTERS) return;
    try {
      window.hljs.highlightElement(code);
    } catch (_) {
      // Sanitized Markdown remains readable even when a language is unknown.
    }
  });
}

function resolveWorkspaceMarkdownPath(filePath, href) {
  let requested = String(href || "").split(/[?#]/, 1)[0];
  if (!requested) return "";
  try {
    requested = decodeURIComponent(requested);
  } catch (_) {
    return "";
  }
  const absolute = requested.startsWith("/");
  const parts = absolute
    ? []
    : String(filePath || "").split("/").filter(Boolean).slice(0, -1);
  for (const component of requested.split("/")) {
    if (!component || component === ".") continue;
    if (component === "..") parts.pop();
    else parts.push(component);
  }
  return parts.length ? `/${parts.join("/")}` : "";
}

async function copyWorkspaceFile() {
  const file = state.activeFile;
  if (!file || typeof file.content !== "string") return;
  await copyText(file.content, "文件内容已复制", `${file.name} 的原始文本已复制到剪贴板。`);
}

function downloadWorkspaceFile() {
  const file = state.activeFile;
  if (!file || file.downloadable === false || state.fileDownloadPending || !socketReady()) return;
  state.fileDownloadPending = true;
  state.fileDownloadRequestId += 1;
  const requestId = state.fileDownloadRequestId;
  renderWorkspaceFileViewer();
  if (!send({
    type: "prepare_workspace_download",
    source_id: state.fileSource,
    path: file.path,
    request_id: requestId,
  })) {
    state.fileDownloadPending = false;
    renderWorkspaceFileViewer();
    return;
  }
  scheduleWorkspaceFileRequestTimeout("download", requestId);
}

function selectCurrentDirectory() {
  if (!state.directory?.can_start_agent || !state.directory.path) return;
  state.selectedDirectory = {
    path: state.directory.path,
    display_path: state.directory.display_path || state.directory.path,
    git_root: state.directory.git_root || "",
    source_id: state.directory.source_id || state.selectedSource,
  };
  renderDirectoryBrowser();
}

function startAgent() {
  if (!state.selectedDirectory || state.startPending) return;
  const prompt = elements.initialPromptInput.value.trim();
  if (!send({
    type: "start_agent",
    kind: "codex",
    source_id: state.selectedDirectory.source_id || state.selectedSource,
    cwd: state.selectedDirectory.path,
    prompt,
  })) return;
  state.startPending = true;
  refreshActionAvailability();
}

function refreshActionAvailability() {
  const connected = socketReady();
  const selectedSource = agentSourceById(state.selectedSource);
  const selectedFileSource = fileSourceById(state.fileSource);
  elements.newAgentButton.disabled = !connected;
  elements.emptyNewAgent.disabled = !connected;
  elements.refreshOutputButton.disabled = !connected || !state.activePane;
  elements.copyOutputButton.disabled = !state.activePane;
  elements.interruptButton.disabled = !connected
    || !state.activePane
    || Boolean(state.agentKeyPending);
  elements.startAgentButton.disabled = !connected
    || !agentSourceUsable(selectedSource)
    || !state.selectedDirectory
    || state.startPending;
  elements.startAgentButton.querySelector("span").textContent = state.startPending ? "正在启动…" : "启动 Codex";
  elements.addSshHostButton.disabled = !connected || !state.terminalAuthorized;
  elements.refreshTerminalProfilesButton.disabled = !connected || !state.terminalAuthorized;
  elements.fileRefreshButton.disabled = !connected
    || state.fileListingPending
    || !fileSourceUsable(selectedFileSource);
  renderPromptState();
  renderAgentKeyControls();
  renderInterruptState();
  renderPushStatus();
  renderSshProfileForm();
  renderTerminalConnection();
  renderWorkspaceFileViewer();
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
  renderRemoteAccessStatus();
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
  output.textContent = `${input.value.length.toLocaleString()} 字`;
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
  bindAgentOutputNavigation();
  document.querySelectorAll("[data-app-view]").forEach((button) => {
    button.addEventListener("click", () => setAppView(button.dataset.appView));
  });
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
    state.lines = Number(elements.lineCount.value) || 500;
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
  elements.agentKeyToggle.addEventListener("click", () => {
    setAgentKeyPanelOpen(!state.agentKeyPanelOpen);
  });
  elements.agentKeyButtons.forEach((button) => {
    button.addEventListener("click", () => sendAgentInteractionKey(button.dataset.agentKey));
  });
  elements.terminalCopySelectAllButton.addEventListener("click", selectAllTerminalCopyText);
  elements.terminalCopyConfirmButton.addEventListener("click", copyFromTerminalDialog);
  ["select", "keyup", "pointerup", "touchend"].forEach((eventName) => {
    elements.terminalCopyText.addEventListener(eventName, renderTerminalCopyAction);
  });
  elements.terminalCopyDialog.addEventListener("close", () => {
    const source = state.copyDialogSource;
    state.terminalCaptureId += 1;
    state.terminalCapturePending = false;
    state.copyDialogSource = "";
    elements.terminalCopyText.value = "";
    renderTerminalCopyAction();
    if (source === "agent-selection") state.outputTerminalInstance?.clearSelection();
  });
  elements.terminalOutputFallback.addEventListener("scroll", () => {
    const output = elements.terminalOutputFallback;
    state.userScrolledUp = output.scrollHeight - output.scrollTop - output.clientHeight > 80;
    scheduleAgentOutputNavigationUpdate();
  });

  elements.promptInput.addEventListener("input", () => {
    updateCounter(elements.promptInput, elements.promptCount);
    renderPromptState();
  });
  elements.attachImageButton.addEventListener("click", () => elements.promptImageInput.click());
  elements.promptImageInput.addEventListener("change", () => addPromptImages(elements.promptImageInput.files));
  elements.promptInput.addEventListener("paste", (event) => {
    const files = [...(event.clipboardData?.files || [])].filter((file) => file.type.startsWith("image/"));
    if (!files.length) return;
    if (!event.clipboardData?.getData("text")) event.preventDefault();
    addPromptImages(files);
  });
  elements.promptForm.addEventListener("dragover", (event) => {
    if (![...(event.dataTransfer?.items || [])].some((item) => item.kind === "file")) return;
    event.preventDefault();
    elements.promptForm.classList.add("is-dragging");
  });
  elements.promptForm.addEventListener("dragleave", (event) => {
    if (!elements.promptForm.contains(event.relatedTarget)) {
      elements.promptForm.classList.remove("is-dragging");
    }
  });
  elements.promptForm.addEventListener("drop", (event) => {
    elements.promptForm.classList.remove("is-dragging");
    const files = [...(event.dataTransfer?.files || [])];
    if (!files.length) return;
    event.preventDefault();
    addPromptImages(files);
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
  elements.queuePromptButton.addEventListener("click", () => submitPrompt("queue"));

  elements.interruptButton.addEventListener("click", () => elements.interruptDialog.showModal());
  elements.confirmInterruptButton.addEventListener("click", confirmInterrupt);

  elements.pathJumpForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const path = elements.pathJumpInput.value.trim();
    if (path) browseDirectory(path);
  });
  elements.agentSourceSelect.addEventListener("change", () => {
    state.selectedSource = elements.agentSourceSelect.value;
    state.directory = null;
    state.selectedDirectory = null;
    elements.pathJumpInput.value = "";
    renderDirectoryBrowser();
    browseDirectory(null);
  });
  elements.directoryRefreshButton.addEventListener("click", () => browseDirectory(state.directory?.path || null));
  elements.directoryUpButton.addEventListener("click", () => browseDirectory(state.directory?.parent));
  elements.selectDirectoryButton.addEventListener("click", selectCurrentDirectory);
  elements.initialPromptInput.addEventListener("input", () => {
    updateCounter(elements.initialPromptInput, elements.initialPromptCount);
  });
  elements.startAgentButton.addEventListener("click", startAgent);

  elements.fileSourceSelect.addEventListener("change", () => {
    state.fileSource = elements.fileSourceSelect.value;
    state.fileListing = null;
    state.fileQuery = "";
    elements.fileSearchInput.value = "";
    elements.filePathJumpInput.value = "";
    clearWorkspaceFileSelection(false);
    renderWorkspaceFileBrowser();
    renderWorkspaceFileViewer();
    browseWorkspaceFiles(null);
  });
  elements.filePathJumpForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const path = elements.filePathJumpInput.value.trim();
    if (path) browseWorkspaceFiles(path);
  });
  elements.fileRefreshButton.addEventListener("click", () => {
    browseWorkspaceFiles(state.fileListing?.path || null);
  });
  elements.fileUpButton.addEventListener("click", () => {
    browseWorkspaceFiles(state.fileListing?.parent ?? "");
  });
  elements.fileSearchInput.addEventListener("input", () => {
    state.fileQuery = elements.fileSearchInput.value;
    renderWorkspaceFileBrowser();
  });
  elements.mobileFileBack.addEventListener("click", clearWorkspaceFileSelection);
  elements.fileHtmlViewButton.addEventListener("click", () => {
    if (state.activeFile?.kind !== "html" || typeof state.activeFile.content !== "string") return;
    state.fileHtmlSourceMode = !state.fileHtmlSourceMode;
    resetWorkspaceFileReaderRender();
    renderWorkspaceFileViewer();
  });
  elements.copyFileButton.addEventListener("click", copyWorkspaceFile);
  elements.downloadFileButton.addEventListener("click", downloadWorkspaceFile);
  elements.downloadFileEmptyButton.addEventListener("click", downloadWorkspaceFile);
  elements.fileUploadButton.addEventListener("click", requestWorkspaceUploadFiles);
  elements.fileUploadInput.addEventListener("change", () => {
    addWorkspaceUploadFiles(elements.fileUploadInput.files);
  });
  elements.fileUploadDropzone.addEventListener("click", () => {
    if (!state.fileUploadPending) requestWorkspaceUploadFiles();
  });
  elements.fileUploadDropzone.addEventListener("keydown", (event) => {
    if (!["Enter", " "].includes(event.key) || state.fileUploadPending) return;
    event.preventDefault();
    requestWorkspaceUploadFiles();
  });
  elements.fileUploadDropzone.addEventListener("dragenter", (event) => {
    if (!workspaceDragContainsFiles(event) || state.fileUploadPending) return;
    event.preventDefault();
    event.stopPropagation();
    elements.fileUploadDropzone.classList.add("is-dragging");
  });
  elements.fileUploadDropzone.addEventListener("dragover", (event) => {
    if (!workspaceDragContainsFiles(event) || state.fileUploadPending) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  });
  elements.fileUploadDropzone.addEventListener("dragleave", (event) => {
    if (!elements.fileUploadDropzone.contains(event.relatedTarget)) {
      elements.fileUploadDropzone.classList.remove("is-dragging");
    }
  });
  elements.fileUploadDropzone.addEventListener("drop", (event) => {
    event.stopPropagation();
    handleWorkspaceUploadDrop(event);
  });
  elements.filePanel.addEventListener("dragenter", (event) => {
    if (!workspaceDragContainsFiles(event)) return;
    event.preventDefault();
    state.fileUploadDragDepth += 1;
    if (workspaceUploadTargetAvailable() && !state.fileUploadPending) {
      elements.filePanel.classList.add("is-upload-dragging");
    }
  });
  elements.filePanel.addEventListener("dragover", (event) => {
    if (!workspaceDragContainsFiles(event)) return;
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = workspaceUploadTargetAvailable() && !state.fileUploadPending
        ? "copy"
        : "none";
    }
  });
  elements.filePanel.addEventListener("dragleave", (event) => {
    if (!state.fileUploadDragDepth) return;
    state.fileUploadDragDepth = Math.max(0, state.fileUploadDragDepth - 1);
    if (!state.fileUploadDragDepth || !elements.filePanel.contains(event.relatedTarget)) {
      state.fileUploadDragDepth = 0;
      elements.filePanel.classList.remove("is-upload-dragging");
    }
  });
  elements.filePanel.addEventListener("drop", (event) => {
    if (!workspaceDragContainsFiles(event)) return;
    state.fileUploadTarget = captureWorkspaceUploadTarget();
    handleWorkspaceUploadDrop(event);
  });
  elements.fileUploadOverwrite.addEventListener("change", renderWorkspaceUploadDialog);
  elements.fileUploadSubmitButton.addEventListener("click", startWorkspaceUpload);
  elements.fileUploadCancelButton.addEventListener("click", cancelWorkspaceUpload);
  elements.fileUploadCloseButton.addEventListener("click", closeWorkspaceUploadDialog);
  elements.fileUploadDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeWorkspaceUploadDialog();
  });
  elements.fileUploadDialog.addEventListener("close", resetWorkspaceUploadDialog);

  elements.themeSelect.addEventListener("change", () => applyTheme(elements.themeSelect.value));
  if (typeof systemDarkThemeQuery.addEventListener === "function") {
    systemDarkThemeQuery.addEventListener("change", handleSystemThemeChange);
  } else {
    systemDarkThemeQuery.addListener(handleSystemThemeChange);
  }
  elements.pushToggleButton.addEventListener("click", togglePush);
  elements.addSshHostButton.addEventListener("click", () => openSshHostDialog());
  elements.sshHostAgentEnabled.addEventListener("change", renderSshProfileForm);
  elements.refreshTerminalProfilesButton.addEventListener("click", () => {
    if (state.terminalAuthorized) send({type: "terminal_profiles_request"});
  });
  elements.copyNativeSshButton.addEventListener("click", () => {
    copyText(nativeSshCommand(), "SSH 命令已复制", "可在电脑终端或支持 Tailscale 的 SSH App 中执行。");
  });
  elements.copyTerminalSetupButton.addEventListener("click", () => {
    copyText(
      "./install-tailscale-web.sh --remote-shell",
      "启用命令已复制",
      "请在仓库的 relay 目录中执行。",
    );
  });
  elements.mobileTerminalBack.addEventListener("click", closeTerminalSelection);
  elements.disconnectTerminalButton.addEventListener("click", closeTerminalSelection);
  elements.copyWebTerminalButton.addEventListener("click", copyWebTerminalOutput);
  elements.terminalCopyButton.addEventListener("click", copyWebTerminalOutput);
  elements.reconnectTerminalButton.addEventListener("click", () => {
    if (state.activeTerminalProfile) openTerminalProfile(state.activeTerminalProfile);
  });
  elements.clearWebTerminalButton.addEventListener("click", () => {
    state.terminalInstance?.clear();
    state.terminalInstance?.focus();
  });
  elements.editSshHostButton.addEventListener("click", () => {
    if (state.activeTerminalProfile) openSshHostDialog(state.activeTerminalProfile);
  });
  elements.copyJumpCommandButton.addEventListener("click", () => {
    const profile = terminalProfileById(state.activeTerminalProfile);
    copyText(
      proxyJumpCommand(profile),
      profile?.kind === "local" ? "Tailscale SSH 命令已复制" : "ProxyJump 命令已复制",
      "可从其他电脑直接经本机跳转到目标服务器。",
    );
  });
  elements.terminalKeybar.addEventListener("pointerdown", preserveTerminalFocusForKeybar);
  document.querySelectorAll("[data-terminal-key]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.terminalKey;
      const sequence = TERMINAL_KEY_SEQUENCES[key] || "";
      sendTerminalShortcutText(terminalSequenceWithPendingModifiers(key, sequence));
    });
  });
  elements.terminalCtrlButton.addEventListener("click", () => {
    const pending = !state.terminalCtrlPending;
    setTerminalCtrlPending(pending);
  });
  document.querySelectorAll("[data-tmux-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.tmuxAction;
      if (action === "prefix") {
        const pending = !state.terminalTmuxPrefixPending;
        setTerminalTmuxPrefixPending(pending);
        return;
      }
      clearTerminalModifierState();
      sendTerminalShortcutText(TMUX_ACTION_SEQUENCES[action] || "");
    });
  });
  elements.terminalPasteButton.addEventListener("click", pasteIntoTerminal);
  elements.sshHostForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveSshHost();
  });
  elements.deleteSshHostButton.addEventListener("click", requestDeleteSshHost);
  elements.confirmDeleteSshHostButton.addEventListener("click", confirmDeleteSshHost);
  elements.openRemoteShellButton.addEventListener("click", () => {
    if (elements.settingsDialog.open) elements.settingsDialog.close();
    setAppView("terminal");
  });

  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => closeDialogById(button.dataset.closeDialog));
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      if (dialog === elements.fileUploadDialog) closeWorkspaceUploadDialog();
      else dialog.close();
    });
  });

  document.addEventListener("pointerdown", (event) => {
    if (!state.agentKeyPanelOpen) return;
    if (
      elements.agentKeybar.contains(event.target)
      || elements.agentKeyToggle.contains(event.target)
    ) return;
    setAgentKeyPanelOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !state.agentKeyPanelOpen) return;
    event.preventDefault();
    setAgentKeyPanelOpen(false);
    elements.agentKeyToggle.focus({ preventScroll: true });
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.connection === "offline") connect();
    if (!document.hidden && state.autoRefresh) refreshOutput();
  });
  window.addEventListener("online", connect);
  window.addEventListener("resize", () => {
    syncVisualViewportLayout();
    scheduleAgentOutputNavigationUpdate();
  });
  window.addEventListener("popstate", () => {
    const url = new URL(window.location.href);
    const requestedView = url.searchParams.get("view");
    const view = ["terminal", "files"].includes(requestedView) ? requestedView : "agents";
    setAppView(view, false);
    if (view === "terminal") {
      const profileId = url.searchParams.get("terminal");
      if (profileId && profileId !== state.activeTerminalProfile) openTerminalProfile(profileId);
      return;
    }
    if (view === "files") return;
    const pane = paneIdFromUrl();
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
  initializeMobileViewportHandling();
  bindEvents();
  state.terminalRequestedProfile = initialTerminalProfile;
  setAppView(initialView, false);
  updateCounter(elements.promptInput, elements.promptCount);
  updateCounter(elements.initialPromptInput, elements.initialPromptCount);
  renderPromptAttachments();
  renderAll();
  renderRemoteAccess();
  connect();
  initPush();
  window.setInterval(() => {
    if (state.autoRefresh && !document.hidden && state.connection === "online") refreshOutput();
  }, 3000);
}

initialize();
