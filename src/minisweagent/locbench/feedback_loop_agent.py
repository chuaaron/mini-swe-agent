"""Feedback-loop agent for LocBench bash-only mode."""

from __future__ import annotations

import shlex
from collections import defaultdict
from pathlib import Path
from typing import Any

from minisweagent.agents.default import DefaultAgent, NonTerminatingException, Submitted
from minisweagent.locbench.utils import extract_json_payload
from minisweagent.swe_qa_bench.utils import extract_paths_from_command, extract_paths_from_output


class FeedbackLoopBashAgent(DefaultAgent):
    """DefaultAgent extension with rule-based external feedback and submission gating."""

    _REPEAT_THRESHOLD = 3
    _ERROR_STREAK_THRESHOLD = 2
    _NO_PROGRESS_THRESHOLD = 4
    _FEEDBACK_REASON_LABELS = {
        "error_streak": "Command failures are repeating.",
        "repeat_command": "The same command is repeating without progress.",
        "no_progress": "No new code evidence is being collected.",
    }

    def __init__(
        self,
        *args,
        feedback_mode: str = "rule",
        feedback_every_n_steps: int = 3,
        feedback_max_rounds: int = 4,
        feedback_submission_gate: bool = True,
        repo_path: str | None = None,
        repo_mount_path: str | None = None,
        workdir: str | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.feedback_mode = (feedback_mode or "rule").strip().lower()
        if self.feedback_mode not in {"rule", "hybrid"}:
            self.feedback_mode = "rule"
        self.feedback_every_n_steps = max(1, int(feedback_every_n_steps))
        self.feedback_max_rounds = max(0, int(feedback_max_rounds))
        self.feedback_submission_gate = bool(feedback_submission_gate)

        self._repo_path: Path | None = None
        if repo_path:
            try:
                self._repo_path = Path(repo_path).resolve()
            except OSError:
                self._repo_path = None
        self._repo_mount_path = str(repo_mount_path or "")
        self._workdir = str(workdir or "")

        self._action_count = 0
        self._last_feedback_action_count = -10_000
        self._feedback_rounds = 0
        self._blocked_submissions = 0
        self._feedback_reason_counts: dict[str, int] = defaultdict(int)

        self._failed_streak = 0
        self._no_new_file_streak = 0
        self._recent_commands: list[str] = []

        self._observed_read_files: list[str] = []
        self._observed_read_set: set[str] = set()

    def get_observation(self, response: dict) -> dict:
        """Execute action and optionally append feedback guidance."""
        action = self.parse_action(response)
        output = self.execute_action(action)
        observation = self.render_template(self.config.action_observation_template, output=output)
        self.add_message("user", observation)
        if feedback_message := self._build_feedback_message(action, output):
            self.add_message("user", feedback_message)
        return output

    def has_finished(self, output: dict[str, str]):
        """Apply submission gate before accepting final marker."""
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() in {"MINI_SWE_AGENT_FINAL_OUTPUT", "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}:
            submission_payload = "".join(lines[1:])
            if self.feedback_submission_gate:
                ok, reason = self._validate_submission_payload(submission_payload)
                if not ok:
                    self._blocked_submissions += 1
                    self._feedback_reason_counts["submission_gate"] += 1
                    raise NonTerminatingException(self._submission_gate_message(reason))
            raise Submitted(submission_payload)

    def add_observed_file(self, path: str) -> None:
        """Record observed file evidence (helper for tests and integrations)."""
        normalized = self._normalize_hint(path)
        if normalized and normalized not in self._observed_read_set:
            self._observed_read_set.add(normalized)
            self._observed_read_files.append(normalized)

    def get_feedback_stats(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "mode": self.feedback_mode,
            "feedback_rounds": self._feedback_rounds,
            "blocked_submissions": self._blocked_submissions,
            "reason_counts": dict(self._feedback_reason_counts),
            "observed_read_files": list(self._observed_read_files),
        }

    def _build_feedback_message(self, action: dict, output: dict) -> str | None:
        reason = self._update_state_and_pick_reason(action, output)
        if reason is None:
            return None
        if self._feedback_rounds >= self.feedback_max_rounds:
            return None
        if self._action_count - self._last_feedback_action_count < self.feedback_every_n_steps:
            return None
        self._feedback_rounds += 1
        self._last_feedback_action_count = self._action_count
        self._feedback_reason_counts[reason] += 1
        return self._format_feedback_message(reason)

    def _update_state_and_pick_reason(self, action: dict, output: dict) -> str | None:
        self._action_count += 1
        command = str(action.get("action", "")).strip()
        normalized_command = self._normalize_command(command)
        if normalized_command:
            self._recent_commands.append(normalized_command)
            self._recent_commands = self._recent_commands[-10:]

        returncode = self._coerce_returncode(output.get("returncode", 1))
        if returncode == 0:
            self._failed_streak = 0
        else:
            self._failed_streak += 1

        added_files = self._ingest_read_evidence(command, str(output.get("output", "")))
        if added_files:
            self._no_new_file_streak = 0
        else:
            self._no_new_file_streak += 1

        if self._failed_streak >= self._ERROR_STREAK_THRESHOLD:
            return "error_streak"
        if self._has_repeat_pattern():
            return "repeat_command"
        if self._no_new_file_streak >= max(self._NO_PROGRESS_THRESHOLD, self.feedback_every_n_steps + 1):
            return "no_progress"
        return None

    def _ingest_read_evidence(self, command: str, output: str) -> list[str]:
        self._hydrate_repo_context()
        if self._repo_path is None:
            return []
        cmd_paths = extract_paths_from_command(command, self._repo_path, self._repo_mount_path, self._workdir)
        out_paths: list[str] = []
        if self._command_emits_path_matches(command):
            out_paths = extract_paths_from_output(output, self._repo_path, self._repo_mount_path, self._workdir)
        added: list[str] = []
        for path in cmd_paths + out_paths:
            if path in self._observed_read_set:
                continue
            self._observed_read_set.add(path)
            self._observed_read_files.append(path)
            added.append(path)
        return added

    def _hydrate_repo_context(self) -> None:
        if self._repo_path is None:
            raw_repo_path = self.extra_template_vars.get("repo_path")
            if raw_repo_path:
                try:
                    self._repo_path = Path(str(raw_repo_path)).resolve()
                except OSError:
                    self._repo_path = None
        if not self._repo_mount_path:
            self._repo_mount_path = str(self.extra_template_vars.get("repo_mount_path") or "")
        if not self._workdir:
            self._workdir = str(self.extra_template_vars.get("workdir") or "")

    def _submission_gate_message(self, reason: str) -> str:
        observed_preview = self._format_observed_files_preview()
        return (
            "SYSTEM_INTERCEPTION: Submission blocked by feedback gate.\n"
            f"Reason: {reason}\n"
            "Please inspect the missing target files with bash read commands (rg/sed/cat/head/tail), "
            "then submit again.\n"
            f"Observed evidence files:\n{observed_preview}"
        )

    def _validate_submission_payload(self, text: str) -> tuple[bool, str]:
        payload, _ = extract_json_payload(text)
        if payload is None:
            return False, "Final output is not valid JSON."
        submission_hints = self._extract_submission_hints(payload)
        if not submission_hints:
            return False, "No file_hint/found_files evidence in final payload."
        if not self._observed_read_set:
            return False, "No file-read evidence collected before submission."
        unverified = [hint for hint in submission_hints if not self._hint_is_observed(hint)]
        if unverified:
            preview = ", ".join(unverified[:5])
            return False, f"Unverified file hints: {preview}"
        return True, ""

    def _extract_submission_hints(self, payload: dict[str, Any]) -> list[str]:
        hints: list[str] = []
        raw_functions = payload.get("functions")
        if isinstance(raw_functions, list):
            for item in raw_functions:
                if not isinstance(item, dict):
                    continue
                hint = self._normalize_hint(item.get("file_hint", ""))
                if hint:
                    hints.append(hint)

        for key in ("found_files", "files"):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    hint = self._normalize_hint(item)
                    if hint:
                        hints.append(hint)

        for key in ("found_entities", "entities"):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    if not isinstance(item, str) or ":" not in item:
                        continue
                    hint = self._normalize_hint(item.split(":", 1)[0])
                    if hint:
                        hints.append(hint)

        deduped: list[str] = []
        seen = set()
        for hint in hints:
            if hint in seen:
                continue
            seen.add(hint)
            deduped.append(hint)
        return deduped

    def _hint_is_observed(self, hint: str) -> bool:
        normalized_hint = self._normalize_hint(hint)
        hint_name = Path(normalized_hint).name
        for observed in self._observed_read_set:
            observed_name = Path(observed).name
            if normalized_hint == observed:
                return True
            if observed.endswith(f"/{normalized_hint}"):
                return True
            if hint_name and hint_name == observed_name:
                return True
            if normalized_hint and normalized_hint in observed:
                return True
        return False

    def _format_feedback_message(self, reason: str) -> str:
        headline = self._FEEDBACK_REASON_LABELS.get(reason, "Execution is stuck.")
        observed_preview = self._format_observed_files_preview()
        return (
            "SYSTEM_INTERCEPTION: External feedback loop triggered.\n"
            f"Reason: {headline}\n"
            "Next step guidance:\n"
            "- Change strategy and target specific source files.\n"
            "- Prefer rg to narrow candidates, then use sed/cat for focused reads.\n"
            "- Avoid repeating failing commands.\n"
            f"Observed evidence files:\n{observed_preview}"
        )

    def _format_observed_files_preview(self) -> str:
        if not self._observed_read_files:
            return "- <none>"
        preview = self._observed_read_files[:8]
        lines = [f"- {item}" for item in preview]
        if len(self._observed_read_files) > len(preview):
            lines.append(f"- ... ({len(self._observed_read_files) - len(preview)} more)")
        return "\n".join(lines)

    def _has_repeat_pattern(self) -> bool:
        if len(self._recent_commands) < self._REPEAT_THRESHOLD:
            return False
        window = self._recent_commands[-self._REPEAT_THRESHOLD :]
        return len(set(window)) == 1

    @staticmethod
    def _coerce_returncode(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _command_emits_path_matches(command: str) -> bool:
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        return any(token in {"rg", "grep"} for token in tokens)

    @staticmethod
    def _normalize_command(command: str) -> str:
        try:
            return " ".join(shlex.split(command))
        except ValueError:
            return command.strip()

    @staticmethod
    def _normalize_hint(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        cleaned = value.strip().replace("\\", "/")
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        return cleaned
