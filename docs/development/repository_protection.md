# Repository protection and solver CI policy

This document records the repository-admin settings required to make the code-level hardening in PR17 enforceable at the GitHub boundary.

## `master` protection

Configure a branch protection rule or repository ruleset for `master` with the following minimum policy:

- require a pull request before merging;
- require the branch to be up to date before merging;
- require the existing `Backend fast tests` status check;
- require the existing `Frontend lint, test, build` status check;
- block force pushes;
- block branch deletion;
- do not allow direct pushes that bypass the required checks.

The normal hosted CI remains the fast deterministic gate. It intentionally excludes machine-dependent solver execution.

## Real solver integration lane

`.github/workflows/solver-integration.yml` adds Windows self-hosted runtime checks without making an unavailable solver machine block every pull request.

Automatic pull-request execution is opt-in through repository variables:

- `MOMO_SOLVER_RUNNER_ENABLED=true` enables the real OpenSees runtime smoke;
- `MOMO_ANSYS_RUNNER_ENABLED=true` enables the real ANSYS/MAPDL runtime smoke.

Both jobs require a self-hosted runner with the labels `Windows` and `X64`.

### OpenSees smoke

The OpenSees job requires Python 3.13 because the repository-bundled OpenSeesPy runtime is a CPython 3.13 Windows build. It performs two checks:

1. exercise the registered USER300 viscous, eddy-current and friction materials using the bundled OpenSeesPy runtime;
2. build the verified STbridge OpenSees model, commit gravity state and execute a real modal solve.

This is a real solver runtime gate, not a mock/dry-run test.

### ANSYS smoke

The ANSYS job resolves the executable from `MOMO_MAPDL_EXECUTABLE` or `MAPDL.exe` on `PATH`, then executes a minimal MAPDL batch static solve. It is intentionally independent of the full optimization workload so it can detect installation/runtime breakage quickly.

## Required-check rollout

Do not make either self-hosted solver job a required GitHub status check until the corresponding runner is continuously available. Once a runner is reliable, promote its status to required separately from the hosted fast CI.

Branch protection/ruleset settings are GitHub repository administration state and are not represented by a normal source-code commit. After merging PR17, verify the rule/ruleset in repository settings and confirm direct pushes to `master` are rejected.
