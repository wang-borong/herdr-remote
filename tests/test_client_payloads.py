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
        self.assertIn('href="/app.css?v=20260829-5"', html)
        self.assertIn('src="/app.js?v=20260829-5"', html)
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

    def test_web_workspace_upload_uses_an_arbitrary_file_picker(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn(
            'id="file-upload-input" type="file" accept="application/*,text/*,font/*,model/*,',
            html,
        )
        self.assertIn(
            'id="file-upload-media-input" type="file" accept="image/*,video/*,audio/*" multiple hidden',
            html,
        )
        self.assertNotIn('id="file-upload-input" type="file" capture', html)
        self.assertNotIn('id="file-upload-media-input" type="file" capture', html)
        self.assertIn("打开文件管理器", html)
        self.assertIn('typeof window.showOpenFilePicker === "function"', source)
        self.assertIn("await window.showOpenFilePicker({multiple: true})", source)
        self.assertNotIn("fileUploadInput.showPicker()", source)
        self.assertIn("elements.fileUploadInput.click()", source)
        self.assertIn("elements.fileUploadMediaInput.click()", source)
        self.assertIn(
            'elements.fileUploadButton.addEventListener("click", startWorkspaceUploadSelection)',
            source,
        )
        self.assertIn(
            'elements.fileUploadBrowseButton.addEventListener("click", requestWorkspaceUploadFiles)',
            source,
        )
        self.assertIn(
            'elements.fileUploadMediaButton.addEventListener("click", requestWorkspaceUploadMediaFiles)',
            source,
        )

    def test_web_agent_output_fits_dividers_to_current_terminal_width(self):
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function fitAgentOutputDividers(snapshot, columns)", source)
        self.assertIn("glyph.repeat(dividerWidth)", source)
        self.assertIn("const dividerDominated =", source)
        self.assertIn("outputRenderedColumns: 0", source)
        self.assertIn("state.outputRenderedColumns === terminal.cols", source)
        self.assertIn("fitAgentOutputDividers(snapshot, terminal.cols)", source)

    def test_web_agent_output_preserves_cjk_punctuation_width(self):
        styles = (ROOT / "web" / "app.css").read_text(encoding="utf-8")

        self.assertRegex(
            styles,
            r"\.agent-output-terminal \.xterm \{[^}]*"
            r"text-spacing-trim: space-all;",
        )

    def test_web_agent_output_has_touch_scrollbar_and_larger_history_choices(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "web" / "app.css").read_text(encoding="utf-8")

        for value in ("200", "500", "1000"):
            self.assertIn(f'<option value="{value}"', html)
        self.assertIn('<option value="500" selected>', html)
        self.assertNotIn('<option value="60">', html)
        self.assertNotIn('<option value="120"', html)
        for element_id in (
            "agent-output-navigation",
            "agent-output-top-button",
            "agent-output-scroll-track",
            "agent-output-scroll-thumb",
            "agent-output-bottom-button",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("function updateAgentOutputNavigation()", source)
        self.assertIn("function bindAgentOutputNavigation()", source)
        self.assertIn("scrollback: AGENT_OUTPUT_SCROLLBACK_LINES", source)
        self.assertIn("AGENT_OUTPUT_XTERM_SCROLLBAR_PX = 1", source)
        self.assertIn(
            "overviewRuler: { width: AGENT_OUTPUT_XTERM_SCROLLBAR_PX }",
            source,
        )
        self.assertIn("lines: 500", source)
        self.assertIn("touch-action: none", styles)
        self.assertIn(".agent-output-scroll-thumb", styles)
        self.assertIn(
            ".agent-output-terminal .xterm-scrollable-element > .scrollbar",
            styles,
        )
        self.assertIn("display: none !important", styles)

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

    def test_windows_client_carries_omp_question_state(self):
        protocol = (ROOT / "herdi-win" / "Models" / "Protocol.cs").read_text(encoding="utf-8")
        agent = (ROOT / "herdi-win" / "Models" / "Agent.cs").read_text(encoding="utf-8")

        for field in (
            "prompt_id",
            "multi_options",
            "selected_options",
            "interaction",
            "multi",
        ):
            self.assertIn(field, protocol)
        self.assertIn('"question_toggle"', protocol)
        self.assertIn('"question_submit"', protocol)

        for member in ("MultiOptions", "SelectedOptions", "Interaction", "IsMultiSelect"):
            self.assertIn(member, agent)

    def test_windows_question_actions_are_connection_methods(self):
        source = (ROOT / "herdi-win" / "Services" / "RelayConnection.cs").read_text(
            encoding="utf-8"
        )
        for method in ("ToggleQuestionOption", "SubmitQuestion"):
            declaration = next(
                line for line in source.splitlines() if f"public void {method}(" in line
            )
            self.assertTrue(
                declaration.startswith("    public void "),
                f"{method} must be declared at RelayConnection class scope",
            )

    def test_windows_client_respects_relay_allowlists(self):
        """Free text must not go out as `respond`, and interrupt must spell C-c."""
        protocol = (ROOT / "herdi-win" / "Models" / "Protocol.cs").read_text(encoding="utf-8")
        connection = (ROOT / "herdi-win" / "Services" / "RelayConnection.cs").read_text(
            encoding="utf-8"
        )

        self.assertIn('InterruptKey = "C-c"', protocol)
        self.assertIn("SafeResponses", protocol)
        self.assertIn("Protocol.SafeResponses.Contains", connection)
        self.assertIn("Protocol.AgentPrompt", connection)

    def test_windows_client_renders_multi_selection_state(self):
        card = (ROOT / "herdi-win" / "Views" / "ApprovalCardView.xaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("MultiOptions", card)
        self.assertIn("ToggleOptionCommand", card)
        self.assertIn("SubmitQuestionCommand", card)

    def test_windows_sections_bind_to_notifying_flags(self):
        for view in ("SessionListView.xaml", "IslandWindow.xaml"):
            xaml = (ROOT / "herdi-win" / "Views" / view).read_text(encoding="utf-8")
            for collection in ("Blocked", "Working", "Idle"):
                self.assertNotIn(
                    f"{{Binding {collection}, Converter",
                    xaml,
                    f"{view} binds {collection} itself; bind Has{collection} instead",
                )

        sections = (ROOT / "herdi-win" / "Views" / "SessionListView.xaml").read_text(
            encoding="utf-8"
        )
        view_model = (ROOT / "herdi-win" / "ViewModels" / "IslandViewModel.cs").read_text(
            encoding="utf-8"
        )
        for flag in ("HasBlocked", "HasWorking", "HasIdle"):
            self.assertIn(f"Binding {flag}, Converter", sections)
            self.assertIn(f"public bool {flag} =>", view_model)
            self.assertIn(f"OnPropertyChanged(nameof({flag}))", view_model)

    def test_clients_offer_the_same_two_sources(self):
        mac = (ROOT / "herdi-mac" / "Sources" / "RelayConnection.swift").read_text(
            encoding="utf-8"
        )
        self.assertIn("case direct", mac)
        self.assertIn("case relay", mac)

        modes = (ROOT / "herdi-win" / "Services" / "ConnectionMode.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("Relay,", modes)
        self.assertIn("Direct,", modes)

        self.assertIn("herdi_remotes", mac)
        store = (ROOT / "herdi-win" / "Services" / "SettingsStore.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("public ConnectionMode Mode", store)
        self.assertIn("public IReadOnlyList<string> Remotes", store)

        connection = (ROOT / "herdi-win" / "Services" / "RelayConnection.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("Mode == ConnectionMode.Direct", connection)

    def test_windows_direct_mode_reaches_hosts_on_the_relays_terms(self):
        relay = (ROOT / "relay" / "herdr_relay.py").read_text(encoding="utf-8")
        cli = (ROOT / "herdi-win" / "Services" / "HerdrCli.cs").read_text(encoding="utf-8")
        poller = (ROOT / "herdi-win" / "Services" / "HerdrPoller.cs").read_text(
            encoding="utf-8"
        )

        self.assertIn("connect_timeout: int = 5", relay)
        self.assertIn('f"ConnectTimeout={connect_timeout}"', relay)
        self.assertIn('"ConnectTimeout=5"', cli)
        self.assertIn('"BatchMode=yes"', relay)
        self.assertIn('"BatchMode=yes"', cli)
        self.assertIn("HERDR_REMOTE_BIN", relay)
        self.assertIn("HERDR_REMOTE_BIN", cli)
        self.assertIn('"pane", "list"', poller)
        self.assertIn('"--lines", "100"', relay)
        self.assertIn("PromptReadLines = 50", poller)
        self.assertIn("lines[-50:]", relay)
        self.assertIn("PromptKeepLines = 20", poller)
        self.assertIn('"prompt": content[-500:]', relay)
        self.assertIn("PromptMaxChars = 500", poller)
        self.assertIn("prompt[^PromptMaxChars..]", poller)

    def test_windows_direct_mode_namespaces_remote_pane_ids(self):
        poller = (ROOT / "herdi-win" / "Services" / "HerdrPoller.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn('host + ":" + paneId', poller)
        self.assertIn("StartsWith(prefix", poller)
        self.assertIn("Protocol.InterruptKey", poller)

    def test_windows_direct_mode_keeps_panes_of_a_host_that_failed(self):
        poller = (ROOT / "herdi-win" / "Services" / "HerdrPoller.cs").read_text(
            encoding="utf-8"
        )
        connection = (ROOT / "herdi-win" / "Services" / "RelayConnection.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("HostsAnswered", poller)
        self.assertIn("ApplySnapshot(result.Agents, result.HostsAnswered)", connection)
        self.assertIn("hostsCovered.Contains(agent.Host)", connection)

    def test_windows_every_expanded_row_opens_something(self):
        sessions = (ROOT / "herdi-win" / "Views" / "SessionListView.xaml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(sessions.count('PreviewMouseLeftButtonUp="OnRowClicked"'), 3)

        view_model = (ROOT / "herdi-win" / "ViewModels" / "IslandViewModel.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("public void OpenAgent(Agent agent)", view_model)
        self.assertIn("if (agent.IsBlocked) ShowApproval(agent);", view_model)
        self.assertIn("public void ShowPane(Agent agent)", view_model)

        code_behind = (ROOT / "herdi-win" / "Views" / "SessionListView.xaml.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("if (node is ButtonBase) return;", code_behind)

    def test_windows_pane_view_reads_and_submits_over_both_transports(self):
        pane = (ROOT / "herdi-win" / "Views" / "PaneView.xaml").read_text(encoding="utf-8")
        self.assertIn("Binding PaneContent", pane)
        self.assertIn("SendPaneInputCommand", pane)
        self.assertIn("InterruptCommand", pane)

        connection = (ROOT / "herdi-win" / "Services" / "RelayConnection.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("public void SendPrompt(Agent agent, string text)", connection)
        self.assertIn("_direct.PromptAsync(agent, trimmed)", connection)
        self.assertIn("Protocol.AgentPrompt(agent.Id, trimmed)", connection)

        relay = (ROOT / "relay" / "herdr_relay.py").read_text(encoding="utf-8")
        self.assertIn('"agent", "prompt", pane_id, text', relay)
        self.assertIn("run_herdr_result(", relay)
        poller = (ROOT / "herdi-win" / "Services" / "HerdrPoller.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn('"agent", "prompt"', poller)

    def test_windows_rows_answer_a_right_click(self):
        sessions = (ROOT / "herdi-win" / "Views" / "SessionListView.xaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("<Border.ContextMenu>", sessions)
        code_behind = (ROOT / "herdi-win" / "Views" / "SessionListView.xaml.cs").read_text(
            encoding="utf-8"
        )
        for handler in ("OnMenuAnswer", "OnMenuOpenPane", "OnMenuInterrupt", "OnMenuCopyPaneId"):
            self.assertIn(f'Click="{handler}"', sessions)
            self.assertIn(f"private void {handler}(", code_behind)

        island = (ROOT / "herdi-win" / "Views" / "IslandWindow.xaml.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("_menuOpen = true;", island)
        self.assertIn("if (_menuOpen || _previewing) return;", island)

    def test_windows_flyout_is_absent_rather_than_transparent(self):
        xaml = (ROOT / "herdi-win" / "Views" / "IslandWindow.xaml").read_text(encoding="utf-8")
        self.assertIn('ShowInTaskbar="False"', xaml)
        self.assertIn('ShowActivated="False"', xaml)

        island = (ROOT / "herdi-win" / "Views" / "IslandWindow.xaml.cs").read_text(
            encoding="utf-8"
        )
        self.assertIn("WsExToolWindow = 0x00000080", island)
        self.assertIn("exStyle | WsExToolWindow", island)

        app = (ROOT / "herdi-win" / "App.xaml.cs").read_text(encoding="utf-8")
        self.assertIn("private IslandWindow? _island;", app)

    def test_tui_sends_multi_selection_messages_with_prompt_identity(self):
        source = (ROOT / "relay" / "herdr_tui.py").read_text(encoding="utf-8")

        self.assertIn('"type": "question_toggle"', source)
        self.assertIn('"type": "question_submit"', source)
        self.assertIn('"prompt_id": event.prompt_id', source)
        self.assertIn("selected_options", source)


if __name__ == "__main__":
    unittest.main()
