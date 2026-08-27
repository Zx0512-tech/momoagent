# Quality Guidelines

> Code quality standards for backend development.

---

## Overview

<!--
Document your project's quality standards here.

Questions to answer:
- What patterns are forbidden?
- What linting rules do you enforce?
- What are your testing requirements?
- What code review standards apply?
-->

(To be filled by the team)

---

## Forbidden Patterns

<!-- Patterns that should never be used and why -->

(To be filled by the team)

- Do not use Python `eval` for ANSYS/APDL numeric expressions, even with an empty
  `__builtins__`; object traversal remains executable syntax.
- Do not silently coerce an invalid engineering expression to `0.0`. Numeric
  parsers accept an AST whitelist (`Constant`, arithmetic `BinOp`, unary signs)
  and raise a named `ValueError` for names, calls, attributes, missing array
  entries, syntax errors, or invalid arithmetic.
- Do not hold `_state_lock` while running an inline job handler. `create_job()`
  persists the `RUNNING` job, executes outside the lock, and suppresses
  `refresh()` reloads for the duration of that inline execution so in-memory
  artifacts cannot be discarded by a concurrent read.

---

## Required Patterns

<!-- Patterns that must always be used -->

(To be filled by the team)

- Queue real solver workflows so dispatcher wall-clock and heartbeat timeouts
  can terminate the worker process tree. The shared executor's post-run check
  is not a process preemption mechanism for arbitrary Python callables.

---

## Testing Requirements

<!-- What level of testing is expected -->

(To be filled by the team)

- Add a concurrency regression test proving a slow inline handler does not block
  `refresh()` and still persists a `SUCCEEDED` job after it releases.

---

## Code Review Checklist

<!-- What reviewers should check -->

(To be filled by the team)
