# Database Guidelines

> Database patterns and conventions for this project.

---

## Overview

<!--
Document your project's database conventions here.

Questions to answer:
- What ORM/query library do you use?
- How are migrations managed?
- What are the naming conventions for tables/columns?
- How do you handle transactions?
-->

(To be filled by the team)

## Scenario: Concurrent platform snapshots

### 1. Scope / Trigger

Use this contract whenever a web process and an independent worker persist
platform Jobs, Artifacts, or engineering configuration to the shared SQLite DB.

### 2. Signatures

```python
SQLitePlatformRepository.save(*, jobs, artifacts, engineering_config,
                              update_engineering_config=True) -> None
```

### 3. Contracts

- Saving a stale in-memory Store must upsert its records without deleting rows
  created by another process.
- A persisted terminal Job must not regress to a stale non-terminal status.
- Engineering configuration is written only when it changed since that Store's
  last load.
- SQLite context managers must close the connection after commit/rollback; the
  built-in Connection context alone is insufficient because it does not close.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Concurrent Job or Artifact absent from stale Store | Preserve it |
| Current Job terminal, stale Job non-terminal | Preserve terminal payload |
| Store did not change engineering config | Do not overwrite current config |
| Transaction fails | Roll back and close the connection |

### 5. Good / Base / Bad Cases

- Good: A worker finishes while the API uploads another Artifact; both remain.
- Base: A single Store upserts its own records normally.
- Bad: `DELETE FROM jobs/artifacts` followed by reinserting a stale snapshot.

### 6. Tests Required

- Load two Stores from one DB, mutate the second, persist the first, and assert
  the second Store's Job, Artifact, and configuration remain.
- Run repository tests with `ResourceWarning` promoted to an error.

### 7. Wrong vs Correct

#### Wrong

```sql
DELETE FROM jobs;
```

#### Correct

```sql
INSERT INTO jobs (...) VALUES (...)
ON CONFLICT(job_id) DO UPDATE SET payload_json=excluded.payload_json;
```

---

## Query Patterns

<!-- How should queries be written? Batch operations? -->

(To be filled by the team)

---

## Migrations

<!-- How to create and run migrations -->

(To be filled by the team)

---

## Naming Conventions

<!-- Table names, column names, index names -->

(To be filled by the team)

---

## Common Mistakes

<!-- Database-related mistakes your team has made -->

(To be filled by the team)
