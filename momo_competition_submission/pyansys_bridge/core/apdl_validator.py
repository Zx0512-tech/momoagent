"""Static checks for generated APDL command streams."""

from __future__ import annotations

from dataclasses import dataclass
import re

_JINJA_PATTERN = re.compile(r"({{.*?}}|{%.*?%}|{#.*?#})")
_LEGACY_PLACEHOLDER_PATTERN = re.compile(r"(?<!{){[A-Za-z_][A-Za-z0-9_.]*}(?!})")


@dataclass(frozen=True)
class ApdlValidationIssue:
    code: str
    message: str
    line: int | None = None
    severity: str = "error"


class ApdlValidationError(ValueError):
    """Raised when APDL static validation finds blocking issues."""

    def __init__(self, issues: list[ApdlValidationIssue]) -> None:
        self.issues = issues
        details = "; ".join(_format_issue(issue) for issue in issues)
        super().__init__(f"APDL validation failed: {details}")


def validate_apdl(text: str) -> list[ApdlValidationIssue]:
    """Validate APDL text and raise on blocking static errors."""

    issues = collect_apdl_issues(text)
    if issues:
        raise ApdlValidationError(issues)
    return []


def collect_apdl_issues(text: str) -> list[ApdlValidationIssue]:
    """Return static validation issues without raising."""

    lines = text.splitlines()
    issues: list[ApdlValidationIssue] = []
    issues.extend(_unresolved_template_issues(lines))
    issues.extend(_legacy_placeholder_issues(lines))
    issues.extend(_do_loop_issues(lines))
    issues.extend(_duplicate_dim_issues(lines))
    issues.extend(_solution_section_issues(lines))
    issues.extend(_required_section_issues(lines))
    return issues


def _unresolved_template_issues(lines: list[str]) -> list[ApdlValidationIssue]:
    issues: list[ApdlValidationIssue] = []
    for line_number, line in enumerate(lines, start=1):
        if _JINJA_PATTERN.search(line):
            issues.append(
                ApdlValidationIssue(
                    code="unresolved_jinja",
                    message="Unresolved Jinja2 template marker remains in APDL command stream.",
                    line=line_number,
                )
            )
    return issues


def _legacy_placeholder_issues(lines: list[str]) -> list[ApdlValidationIssue]:
    issues: list[ApdlValidationIssue] = []
    for line_number, line in enumerate(lines, start=1):
        if _LEGACY_PLACEHOLDER_PATTERN.search(line):
            issues.append(
                ApdlValidationIssue(
                    code="legacy_placeholder",
                    message="Legacy str.format placeholder remains in APDL command stream.",
                    line=line_number,
                )
            )
    return issues


def _do_loop_issues(lines: list[str]) -> list[ApdlValidationIssue]:
    issues: list[ApdlValidationIssue] = []
    stack: list[int] = []
    for line_number, line in enumerate(lines, start=1):
        command = _command_name(line)
        if command == "*DO":
            stack.append(line_number)
        elif command == "*ENDDO":
            if stack:
                stack.pop()
            else:
                issues.append(
                    ApdlValidationIssue(
                        code="unbalanced_do_loop",
                        message="*ENDDO appears without a matching *DO.",
                        line=line_number,
                    )
                )
    for line_number in stack:
        issues.append(
            ApdlValidationIssue(
                code="unbalanced_do_loop",
                message="*DO appears without a matching *ENDDO.",
                line=line_number,
            )
        )
    return issues


def _duplicate_dim_issues(lines: list[str]) -> list[ApdlValidationIssue]:
    issues: list[ApdlValidationIssue] = []
    seen: dict[str, int] = {}
    for line_number, line in enumerate(lines, start=1):
        parts = _apdl_parts(line)
        if not parts or parts[0].upper() != "*DIM" or len(parts) < 2:
            continue
        name = parts[1].upper()
        if name in seen:
            issues.append(
                ApdlValidationIssue(
                    code="duplicate_dim_table",
                    message=f"*DIM name {parts[1]} repeats line {seen[name]}.",
                    line=line_number,
                )
            )
        else:
            seen[name] = line_number
    return issues


def _solution_section_issues(lines: list[str]) -> list[ApdlValidationIssue]:
    issues: list[ApdlValidationIssue] = []
    in_solution = False
    for line_number, line in enumerate(lines, start=1):
        command = _command_name(line)
        if command == "/SOLU":
            in_solution = True
        elif command == "FINISH":
            in_solution = False
        elif command == "/PREP7" and in_solution:
            issues.append(
                ApdlValidationIssue(
                    code="prep7_in_solution",
                    message="/PREP7 appears inside an active /SOLU section before FINISH.",
                    line=line_number,
                )
            )
    return issues


def _required_section_issues(lines: list[str]) -> list[ApdlValidationIssue]:
    commands = {_command_name(line) for line in lines}
    issues: list[ApdlValidationIssue] = []
    if "/SOLU" not in commands:
        issues.append(
            ApdlValidationIssue(
                code="missing_solu",
                message="APDL command stream does not contain a /SOLU section.",
            )
        )
    if "FINISH" not in commands:
        issues.append(
            ApdlValidationIssue(
                code="missing_finish",
                message="APDL command stream does not contain FINISH.",
            )
        )
    return issues


def _apdl_parts(line: str) -> list[str]:
    command_text = line.split("!", 1)[0].strip()
    if not command_text:
        return []
    return [part.strip() for part in command_text.split(",")]


def _command_name(line: str) -> str:
    parts = _apdl_parts(line)
    if not parts:
        return ""
    return parts[0].upper()


def _format_issue(issue: ApdlValidationIssue) -> str:
    if issue.line is None:
        return f"{issue.code}: {issue.message}"
    return f"{issue.code} at line {issue.line}: {issue.message}"


__all__ = [
    "ApdlValidationError",
    "ApdlValidationIssue",
    "collect_apdl_issues",
    "validate_apdl",
]
