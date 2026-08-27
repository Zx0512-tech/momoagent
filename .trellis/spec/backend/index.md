# Backend Development Guidelines

> Best practices for backend development in this project.

---

## Overview

This directory contains guidelines for backend development. Fill in each file with your project's specific conventions.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Module organization and file layout | To fill |
| [Database Guidelines](./database-guidelines.md) | ORM patterns, queries, migrations | To fill |
| [Error Handling](./error-handling.md) | Error types, handling strategies | To fill |
| [Quality Guidelines](./quality-guidelines.md) | Code standards, forbidden patterns | To fill |
| [Logging Guidelines](./logging-guidelines.md) | Structured logging, log levels | To fill |
| [Workflow Contract](./workflow-contract.md) | Frozen workflow, gate, and transition contracts | Filled |
| [Agent Harness](./agent-harness.md) | Typed tool, approval, and audit contracts | Filled |
| [API Boundaries](./api-boundaries.md) | Strict requests, load artifacts, Live gates, and preflight | Filled |
| [Report and Refresh Safety](./report-and-refresh-safety.md) | Strict report JSON and non-replaying terminal refresh failures | Filled |
| [Real Execution Capabilities](./real-execution-capabilities.md) | Shared live/mock catalog and creation-time capability gates | Filled |
| [Damper Sweep Output Integrity](./damper-sweep-output-integrity.md) | Deep-path persistence, evidence gates, and read-only multi-case time-history comparison | Filled |
| [Session Context and History Lifecycle](./session-context-lifecycle.md) | Session-scoped LLM history and audit-preserving deletion | Filled |

---

## How to Fill These Guidelines

For each guideline file:

1. Document your project's **actual conventions** (not ideals)
2. Include **code examples** from your codebase
3. List **forbidden patterns** and why
4. Add **common mistakes** your team has made

The goal is to help AI assistants and new team members understand how YOUR project works.

---

**Language**: All documentation should be written in **English**.
