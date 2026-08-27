# Quality Guidelines

> Code quality standards for frontend development.

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

- Do not create a page polling interval with raw `setInterval` inside an event handler. Register it through `useManagedIntervals`, so route changes clear outstanding timers.
- Do not leave submit or polling `catch {}` blocks empty. Report the error through `useJobStore().reportError` (or the page's existing error state) so `ErrorPanel` can show the failure.

---

## Required Patterns

- Job pages must use `useManagedIntervals` for interval cleanup and `errorMessage` for stable unknown-error fallbacks.
- Run cards must treat every backend terminal run status, including `UNSUPPORTED`, as terminal for progress rendering.

---

## Testing Requirements

- Add a regression test for each new status classification or shared polling utility. At minimum, assert `UNSUPPORTED` does not render as an active progress state.
- Run `npm.cmd run build` and `npm.cmd test` for frontend changes.

---

## Code Review Checklist

<!-- What reviewers should check -->

(To be filled by the team)
