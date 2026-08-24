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
