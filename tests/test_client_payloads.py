import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ClientPayloadTests(unittest.TestCase):
    def test_web_agent_keybar_sends_correlated_safe_navigation_keys(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "app.css").read_text(encoding="utf-8")

        self.assertIn('id="agent-key-toggle"', html)
        self.assertIn('aria-controls="agent-keybar"', html)
        self.assertIn('id="agent-keybar"', html)
        self.assertIn('aria-busy="false" hidden', html)
        for key in ("Up", "Down", "Enter", "Escape"):
            self.assertIn(f'data-agent-key="{key}"', html)
        for old_label in ("<span>上移</span>", "<span>下移</span>", "<span>Enter</span>", "<span>返回</span>"):
            self.assertNotIn(old_label, html)
        self.assertIn("const AGENT_INTERACTION_KEYS", source)
        self.assertIn("const AGENT_KEY_ACK_TIMEOUT_MS", source)
        self.assertIn('type: "send_keys"', source)
        self.assertIn("keys: [keyName]", source)
        self.assertIn("request_id: requestId", source)
        self.assertIn("message.request_id === pendingAgentKey.requestId", source)
        self.assertIn("|| !message.request_id", source)
        self.assertIn("handleAgentKeyTimeout(requestId)", source)
        self.assertIn("elements.agentKeybar.hidden = !panelOpen", source)
        self.assertIn("elements.promptInput.blur()", source)
        self.assertIn(".agent-key-toggle", styles)
        self.assertIn(".agent-keybar", styles)
        self.assertIn("grid-template-columns: repeat(4, 46px)", styles)
        self.assertIn("min-height: 44px", styles)

    def test_web_omits_low_frequency_prompt_and_shell_presets(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "app.css").read_text(encoding="utf-8")

        for label in ("汇报进展", "检查并测试", "总结上下文", "快捷命令"):
            self.assertNotIn(label, html)
        self.assertNotIn("data-prompt=", html)
        self.assertNotIn("data-terminal-command=", html)
        self.assertNotIn('[data-prompt]', source)
        self.assertNotIn('[data-terminal-command]', source)
        self.assertNotIn(".prompt-shortcuts", styles)
        self.assertNotIn(".terminal-quick-commands", styles)
        self.assertNotIn('<h3 id="composer-title">', html)
        self.assertIn('<section class="composer-card" aria-label="发送 Prompt">', html)
        for key in ("escape", "tab", "left", "right"):
            self.assertIn(f'data-terminal-key="{key}"', html)
        self.assertIn('id="terminal-ctrl-button"', html)
        self.assertIn('id="tmux-keybar"', html)

    def test_web_preserves_and_sends_omp_question_state(self):
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        for field in (
            "prompt_id",
            "multi_options",
            "selected_options",
            "interaction",
            "multi",
        ):
            self.assertIn(field, source)
        self.assertIn('type: "question_toggle"', source)
        self.assertIn('type: "question_submit"', source)
        self.assertIn("prompt_id: agent.promptId", source)

    def test_web_clears_prompt_identity_when_an_agent_leaves_blocked_state(self):
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function clearBlockedPromptState(agent)", source)
        self.assertIn('normalizedStatus(next) === "blocked"', source)
        self.assertIn('normalizedStatus(merged) !== "blocked"', source)
        self.assertIn('promptId: ""', source)

    def test_web_html_preview_is_sandboxed_and_keeps_source_mode(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "app.css").read_text(encoding="utf-8")

        self.assertIn('id="file-html-preview"', html)
        self.assertIn('title="HTML 安全预览" sandbox referrerpolicy="no-referrer"', html)
        self.assertIn('id="file-html-view-button"', html)
        self.assertIn('href="/app.css?v=20260828-1"', html)
        self.assertIn('src="/app.js?v=20260828-1"', html)
        self.assertNotIn("allow-scripts", html)
        self.assertNotIn("allow-forms", html)
        self.assertNotIn("allow-same-origin", html)
        self.assertIn("function renderHtmlWorkspaceFile(file)", source)
        self.assertIn("function prioritizeHtmlPreviewMainContent(previewDocument)", source)
        self.assertIn('body?.querySelector("main, [role=\'main\']")', source)
        self.assertIn('section.setAttribute("data-herdr-preview-main", "")', source)
        self.assertIn("body.prepend(section)", source)
        self.assertIn("function removeHtmlPreviewExternalImage(image, body)", source)
        self.assertIn("removeHtmlPreviewExternalImage(node, previewDocument.body)", source)
        self.assertIn("function replaceHtmlPreviewDocument(srcdoc)", source)
        self.assertIn("elements.fileHtmlPreview.cloneNode(false)", source)
        self.assertIn("elements.fileHtmlPreview = preview", source)
        self.assertIn("replaceHtmlPreviewDocument(`<!doctype html>", source)
        self.assertIn("WHOLE_DOCUMENT: true", source)
        self.assertIn('FORBID_ATTR: ["autofocus", "formaction", "ping", "poster", "srcset"]', source)
        self.assertIn('window.matchMedia("(prefers-color-scheme: dark)")', source)
        self.assertIn("function resolvedTheme()", source)
        self.assertIn("function refreshActiveHtmlPreviewTheme()", source)
        self.assertIn("function handleSystemThemeChange()", source)
        self.assertIn('previewDocument.documentElement.dataset.herdrPreviewTheme = previewTheme', source)
        self.assertIn(":root { color-scheme: light;", source)
        self.assertIn(":root { color-scheme: dark;", source)
        self.assertIn("html, body { color: #e6ebf5 !important; background: #0b1020 !important; }", source)
        self.assertIn("previewDocument.head.append(baseStyle)", source)
        self.assertIn('state.fileHtmlSourceMode ? "source" : resolvedTheme()', source)
        self.assertIn('policy.setAttribute("http-equiv", "Content-Security-Policy")', source)
        self.assertIn("HTML_PREVIEW_CSP", source)
        self.assertIn('"script", "select", "svg"', source)
        self.assertIn("state.fileHtmlSourceMode = !state.fileHtmlSourceMode", source)
        self.assertIn("elements.fileHtmlPreview.srcdoc", source)
        self.assertIn(".html-preview {\n  color-scheme: inherit;", styles)
        self.assertIn("background: var(--surface-strong);", styles)
        self.assertIn(".file-kind-badge.is-html", styles)

    def test_web_prompt_supports_images_without_an_arbitrary_text_limit(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "app.css").read_text(encoding="utf-8")

        self.assertIn('id="prompt-image-input"', html)
        self.assertIn('accept="image/png,image/jpeg,image/webp" multiple', html)
        self.assertIn('id="attach-image-button"', html)
        self.assertIn('id="prompt-attachments"', html)
        self.assertNotIn('maxlength="1000"', html)
        self.assertNotIn("0 / 1000", html)
        self.assertIn("const PROMPT_IMAGE_MAX_COUNT = 4", source)
        self.assertIn('addEventListener("paste"', source)
        self.assertIn('addEventListener("drop"', source)
        self.assertIn("message.images = encodedImages", source)
        self.assertIn("new FileReader()", source)
        self.assertIn("pendingPromptSubmissionId !== submissionId", source)
        self.assertIn("lineHeight: 1.1", source)
        self.assertNotIn("lineHeight: 1.35", source)
        self.assertIn(".prompt-attachment", styles)
        self.assertIn("#prompt-form.is-dragging #prompt-input", styles)

    def test_swift_models_decode_omp_question_state(self):
        for relative_path in (
            "herdi-ios/Sources/Models/Agent.swift",
            "herdi-mac/Sources/Agent.swift",
        ):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            for field in (
                "prompt_id",
                "multi_options",
                "selected_options",
                "interaction",
                "multi",
            ):
                self.assertIn(field, source)
            self.assertIn('let type = "question_toggle"', source)
            self.assertIn('let type = "question_submit"', source)

    def test_native_clients_render_and_send_multi_selection_state(self):
        ios = (ROOT / "herdi-ios" / "Sources" / "Views" / "ApprovalView.swift").read_text(
            encoding="utf-8"
        )
        mac = (ROOT / "herdi-mac" / "Sources" / "NotchContentView.swift").read_text(
            encoding="utf-8"
        )

        for source in (ios, mac):
            self.assertIn("selectedOptions.contains(option)", source)
            self.assertIn("toggleQuestionOption", source)
            self.assertIn("submitQuestion", source)

    def test_native_clients_clear_stale_prompt_state_after_unblocking(self):
        for relative_path in (
            "herdi-ios/Sources/Services/RelayConnection.swift",
            "herdi-mac/Sources/RelayConnection.swift",
        ):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("clearBlockedState(existing)", source)
            self.assertIn("agent.promptId = nil", source)
            self.assertIn("agent.selectedOptions = []", source)

    def test_native_question_actions_are_connection_methods(self):
        for relative_path in (
            "herdi-ios/Sources/Services/RelayConnection.swift",
            "herdi-mac/Sources/RelayConnection.swift",
        ):
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            for method in ("toggleQuestionOption", "submitQuestion"):
                declaration = next(
                    line for line in source.splitlines() if f"func {method}(" in line
                )
                self.assertTrue(
                    declaration.startswith("    func "),
                    f"{method} must be declared at RelayConnection class scope",
                )

    def test_tui_sends_multi_selection_messages_with_prompt_identity(self):
        source = (ROOT / "relay" / "herdr_tui.py").read_text(encoding="utf-8")

        self.assertIn('"type": "question_toggle"', source)
        self.assertIn('"type": "question_submit"', source)
        self.assertIn('"prompt_id": event.prompt_id', source)
        self.assertIn("selected_options", source)


if __name__ == "__main__":
    unittest.main()
