from __future__ import annotations

import re
from dataclasses import dataclass


_QUESTION_PREFIX = re.compile(
    r"^\s*(?:(?:\d{1,3})\s*[.)、:：]\s*|[-*]\s*)"
)
_STANDALONE_NUMBER = re.compile(r"^\s*\d{1,4}[.)、:：]?\s*$")
_ROLE_LINE = re.compile(r"^\s*(?:user|assistant|system)\s*[:：]?\s*$", re.IGNORECASE)
_FALLBACK_QUESTIONS = (
    "这个问题还涉及哪些关键内容？",
    "相关技术有哪些典型应用？",
    "实际应用中需要注意哪些问题？",
)


class _CandidateBuffer:
    """Keep marker candidates bounded by compressing long horizontal whitespace."""

    _EXACT_WHITESPACE_LIMIT = 128

    def __init__(self) -> None:
        self._parts: list[str | int] = []
        self._exact_whitespace = 0

    def append(self, value: str, *, whitespace: bool = False) -> None:
        if not value:
            return
        if not whitespace:
            self._append_raw(value)
            return

        exact = min(
            len(value),
            self._EXACT_WHITESPACE_LIMIT - self._exact_whitespace,
        )
        if exact:
            self._append_raw(value[:exact])
            self._exact_whitespace += exact

        compressed = len(value) - exact
        if compressed:
            if self._parts and isinstance(self._parts[-1], int):
                self._parts[-1] += compressed
            else:
                self._parts.append(compressed)

    def _append_raw(self, value: str) -> None:
        if self._parts and isinstance(self._parts[-1], str):
            self._parts[-1] += value
        else:
            self._parts.append(value)

    def render(self) -> str:
        return "".join(
            part if isinstance(part, str) else " " * part
            for part in self._parts
        )

    @property
    def storage_size(self) -> int:
        return sum(
            len(part) if isinstance(part, str) else 1
            for part in self._parts
        )

    def clear(self) -> None:
        self._parts.clear()
        self._exact_whitespace = 0


class ChatStreamSanitizer:
    """Remove a leaked chat-turn marker without delaying ordinary text."""

    _NORMAL = "normal"
    _BLANK_LINE = "blank_line"
    _BEFORE_NUMBER = "before_number"
    _NUMBER = "number"
    _AFTER_NUMBER = "after_number"
    _BEFORE_ROLE = "before_role"
    _ROLE = "role"
    _ROLE_SUFFIX = "role_suffix"
    _AFTER_ROLE = "after_role"
    _AFTER_ROLE_COLON = "after_role_colon"
    _ROLES = ("user", "assistant", "system")
    _FUSED_ROLE_ARTIFACT_SUFFIXES = ("edm",)

    def __init__(self) -> None:
        self._state = self._NORMAL
        self._candidate = _CandidateBuffer()
        self._digit_count = 0
        self._number = ""
        self._role = ""
        self._role_suffix = ""
        self._pending_cr = False
        self._discarding = False

    @property
    def buffered_size(self) -> int:
        return self._candidate.storage_size + int(self._pending_cr)

    def feed(self, chunk: str) -> str:
        if self._discarding or not chunk:
            return ""

        output: list[str] = []
        index = 0
        if self._pending_cr:
            self._pending_cr = False
            if chunk.startswith("\n"):
                output.append(self._process_token("\r\n", newline=True))
                index = 1
            else:
                output.append(self._process_token("\r"))

        while index < len(chunk) and not self._discarding:
            char = chunk[index]
            if char == "\r":
                if index + 1 < len(chunk) and chunk[index + 1] == "\n":
                    output.append(self._process_token("\r\n", newline=True))
                    index += 2
                elif index + 1 == len(chunk):
                    self._pending_cr = True
                    index += 1
                else:
                    output.append(self._process_token(char))
                    index += 1
            elif char == "\n":
                output.append(self._process_token(char, newline=True))
                index += 1
            else:
                output.append(
                    self._process_token(char, whitespace=char in " \t")
                )
                index += 1
        return "".join(output)

    def finish(self) -> str:
        if self._discarding:
            self._reset()
            return ""

        output = ""
        if self._pending_cr:
            self._pending_cr = False
            output += self._process_token("\r")
        if self._state != self._NORMAL:
            output += self._candidate.render()
        self._reset()
        return output

    def abort(self) -> str:
        """Flush only text that cannot yet be part of a leaked turn marker."""

        if self._discarding:
            self._reset()
            return ""

        output = ""
        if self._state == self._BLANK_LINE:
            output = self._candidate.render()
        elif self._state == self._NORMAL and self._pending_cr:
            output = "\r"
        self._reset()
        return output

    def _process_token(
        self,
        token: str,
        *,
        newline: bool = False,
        whitespace: bool = False,
    ) -> str:
        if self._state == self._NORMAL:
            if newline:
                self._candidate.append(token)
                self._state = self._BLANK_LINE
                return ""
            return token

        if self._state == self._BLANK_LINE:
            if whitespace:
                self._candidate.append(token, whitespace=True)
                return ""
            if newline:
                self._candidate.append(token)
                self._state = self._BEFORE_NUMBER
                return ""
            return self._reject(token, newline=newline, whitespace=whitespace)

        if self._state == self._BEFORE_NUMBER:
            if whitespace:
                self._candidate.append(token, whitespace=True)
                return ""
            if token.isdigit():
                self._candidate.append(token)
                self._digit_count = 1
                self._number = token
                self._state = self._NUMBER
                return ""
            return self._reject(token, newline=newline, whitespace=whitespace)

        if self._state == self._NUMBER:
            if token.isdigit():
                if self._digit_count == 4:
                    return self._reject(
                        token,
                        newline=newline,
                        whitespace=whitespace,
                    )
                self._candidate.append(token)
                self._digit_count += 1
                self._number += token
                return ""
            if token in ".)、:：":
                self._candidate.append(token)
                self._state = self._AFTER_NUMBER
                return ""
            if whitespace:
                self._candidate.append(token, whitespace=True)
                self._state = self._AFTER_NUMBER
                return ""
            if newline:
                self._candidate.append(token)
                self._state = self._BEFORE_ROLE
                return ""
            return self._reject(token, newline=newline, whitespace=whitespace)

        if self._state == self._AFTER_NUMBER:
            if whitespace:
                self._candidate.append(token, whitespace=True)
                return ""
            if newline:
                self._candidate.append(token)
                self._state = self._BEFORE_ROLE
                return ""
            lowered = token.lower()
            if any(role.startswith(lowered) for role in self._ROLES):
                self._candidate.append(token)
                self._role = lowered
                self._state = self._ROLE
                return ""
            return self._reject(token, newline=newline, whitespace=whitespace)

        if self._state == self._BEFORE_ROLE:
            if whitespace:
                self._candidate.append(token, whitespace=True)
                return ""
            lowered = token.lower()
            if any(role.startswith(lowered) for role in self._ROLES):
                self._candidate.append(token)
                self._role = lowered
                self._state = self._ROLE
                return ""
            return self._reject(token, newline=newline, whitespace=whitespace)

        if self._state == self._ROLE:
            if self._role in self._ROLES:
                if whitespace:
                    self._candidate.append(token, whitespace=True)
                    self._state = self._AFTER_ROLE
                    return ""
                if token in ":：":
                    self._candidate.append(token)
                    self._state = self._AFTER_ROLE_COLON
                    return ""
                if newline:
                    return self._confirm_marker()
                if "a" <= token.lower() <= "z":
                    self._candidate.append(token)
                    self._role_suffix = token.lower()
                    self._state = self._ROLE_SUFFIX
                    return ""

            lowered = self._role + token.lower()
            if any(role.startswith(lowered) for role in self._ROLES):
                self._candidate.append(token)
                self._role = lowered
                return ""
            return self._reject(token, newline=newline, whitespace=whitespace)

        if self._state == self._ROLE_SUFFIX:
            if "a" <= token.lower() <= "z" and len(self._role_suffix) < 16:
                self._candidate.append(token)
                self._role_suffix += token.lower()
                return ""
            if (
                self._role_suffix in self._FUSED_ROLE_ARTIFACT_SUFFIXES
                and len(token) == 1
                and ord(token) > 127
                and not token.isspace()
            ):
                number = self._number
                self._reset_candidate()
                return f"\n\n{number}. " + self._process_token(
                    token,
                    newline=newline,
                    whitespace=whitespace,
                )
            return self._reject(token, newline=newline, whitespace=whitespace)

        if self._state == self._AFTER_ROLE:
            if whitespace:
                self._candidate.append(token, whitespace=True)
                return ""
            if token in ":：":
                self._candidate.append(token)
                self._state = self._AFTER_ROLE_COLON
                return ""
            if newline:
                return self._confirm_marker()
            return self._reject(token, newline=newline, whitespace=whitespace)

        if self._state == self._AFTER_ROLE_COLON:
            if whitespace:
                self._candidate.append(token, whitespace=True)
                return ""
            if newline:
                return self._confirm_marker()
            return self._reject(token, newline=newline, whitespace=whitespace)

        return token

    def _reject(
        self,
        token: str,
        *,
        newline: bool,
        whitespace: bool,
    ) -> str:
        safe = self._candidate.render()
        self._reset_candidate()
        return safe + self._process_token(
            token,
            newline=newline,
            whitespace=whitespace,
        )

    def _confirm_marker(self) -> str:
        self._candidate.clear()
        self._state = self._NORMAL
        self._discarding = True
        return ""

    def _reset_candidate(self) -> None:
        self._candidate.clear()
        self._state = self._NORMAL
        self._digit_count = 0
        self._number = ""
        self._role = ""
        self._role_suffix = ""

    def _reset(self) -> None:
        self._reset_candidate()
        self._pending_cr = False
        self._discarding = False


@dataclass(frozen=True, slots=True)
class StreamContentUpdate:
    content: str
    replace_content: bool = False


class ChatStreamAssembler:
    """Unify incremental and full-snapshot model streams."""

    def __init__(self) -> None:
        self.content = ""
        self._sanitizer = ChatStreamSanitizer()
        self._snapshot_raw: str | None = None

    def feed_incremental(self, chunk: str) -> StreamContentUpdate | None:
        if self._snapshot_raw is not None:
            self._snapshot_raw += chunk
        return self._append(self._sanitizer.feed(chunk))

    def feed_snapshot(self, snapshot: str) -> StreamContentUpdate | None:
        if self._snapshot_raw is not None and snapshot.startswith(self._snapshot_raw):
            suffix = snapshot[len(self._snapshot_raw):]
            self._snapshot_raw = snapshot
            return self._append(self._sanitizer.feed(suffix))

        self._snapshot_raw = snapshot
        self._sanitizer = ChatStreamSanitizer()
        confirmed = self._sanitizer.feed(snapshot)
        return self._set_content(confirmed)

    def finish(self) -> StreamContentUpdate | None:
        return self._append(self._sanitizer.finish())

    def abort(self) -> StreamContentUpdate | None:
        return self._append(self._sanitizer.abort())

    def _append(self, safe: str) -> StreamContentUpdate | None:
        if not safe:
            return None
        self.content += safe
        return StreamContentUpdate(safe)

    def _set_content(self, target: str) -> StreamContentUpdate | None:
        if target == self.content:
            return None
        if target.startswith(self.content):
            suffix = target[len(self.content):]
            self.content = target
            return StreamContentUpdate(suffix)
        self.content = target
        return StreamContentUpdate(target, replace_content=True)


def parse_related_questions(raw: str) -> list[str]:
    """Normalize common model list formats into at most three questions."""

    if not isinstance(raw, str) or not raw.strip():
        return []

    questions: list[str] = []
    seen: set[str] = set()
    for raw_line in raw.splitlines():
        line = raw_line.strip().strip("`").strip()
        if not line or _STANDALONE_NUMBER.fullmatch(line) or _ROLE_LINE.fullmatch(line):
            continue

        line = line.strip("*").strip()
        is_list_item = bool(_QUESTION_PREFIX.match(line))
        line = _QUESTION_PREFIX.sub("", line).strip()
        if not line:
            continue
        if "?" not in line and "？" not in line:
            if is_list_item and len(line) <= 80:
                topic = re.split(r"[。；;，,：:]", line, maxsplit=1)[0].strip()
                if not topic:
                    continue
                line = f"{topic}包括哪些内容？"
            else:
                continue
        if re.match(r"^(?:user|assistant|system)\s*:", line, re.IGNORECASE):
            continue

        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        questions.append(line)
        if len(questions) == 3:
            break

    return questions


def complete_related_questions(questions: list[str]) -> list[str]:
    """Preserve model suggestions and pad missing entries with safe defaults."""

    completed = list(dict.fromkeys(questions))[:3]
    for fallback in _FALLBACK_QUESTIONS:
        if len(completed) == 3:
            break
        if fallback not in completed:
            completed.append(fallback)
    return completed
