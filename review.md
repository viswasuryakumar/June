# Continuous Code Review

> Ownership correction: Codex records all review work, corrections, and E2E evidence here and does not edit `PROGRESS.md`. Claude exclusively maintains `PROGRESS.md` for implementation and focused-test work. Existing historical Codex entries in the append-only progress log are retained but are not the forward-going workflow.

This is the canonical rolling review of worker changes. Findings keep stable IDs; later cycles mark them resolved rather than deleting history.

## Current status

- Last review: 2026-07-11 23:25 UTC
- Review cadence: every 10 minutes until stopped
- Scope observed: Epic 2/3 finding fixes and active Epic 4 Selection implementation/unit tests
- Verification state: focused non-browser verification passes (57 tests and ruff); browser/full-suite verification remains blocked by severe disk pressure under REV-002

## Open findings

### REV-002 — High — Persistent-context test hangs and blocks full-suite verification

A fresh `.venv/bin/python -m pytest -q` emitted two passes and then made no further progress for more than 90 seconds. Isolation with `timeout 90s ... tests/test_auth_context.py -vv` identified `test_persistent_context_loads_dummy_extension_and_returns_extension_id` as failing/hanging around persistent Chromium startup, after which `test_get_context_manager_yields_usable_context` also failed to complete before the file timeout. Running the first test alone also timed out after 45 seconds. The newer claims of a fully passing suite are therefore not independently confirmed against the current working tree.

Environment evidence at 23:03 UTC: the root filesystem is again at 100% with about 38 MB available. A prior Test Agent entry reports the same condition caused Chromium `TargetClosedError`/shared-memory failures and that tests passed after cache cleanup. Treat disk pressure as the leading hypothesis, but do not close this finding until the persistent-context file and full suite pass under a documented adequate-free-space baseline.

### REV-006 — High — Submitted-today applications do not consume the daily quota

Epic 4 S4.3.1 requires `max_applications_per_day` minus applications already submitted today. `_selected_today_count()` queries only records whose current status is `selected`; once a job progresses to `submitted`, it disappears from that query even though its `timestamps["submitted"]` proves it consumed today's application budget. The new Codex-owned acceptance test `tests/e2e/test_selection_acceptance.py::test_submitted_today_application_consumes_selection_quota` reproduces the over-selection through public tracker and selection interfaces.

Verification: the E2E test fails with `jobs_selected == 1` where the remaining quota is zero. Static checks on the E2E file pass.

Required correction: calculate quota consumption from records submitted today (and define whether still-in-flight selections also reserve quota), then add Claude-owned unit coverage for submitted, selected/in-flight, previous-day, and mixed-status cases.

### REV-007 — Medium — LLM rationale is not stored on the application record

Epic 4 S4.2.2 requires the top-K rationale to be stored on the record for approval UI/reporting. `rank_jobs()` returns `ScoredJob.rationale`, but `select_and_queue()` only includes it in the transient `job_selected` log event. `ApplicationRecord` has no rationale field and the tracker transition metadata does not persist it. A later approval/reporting stage therefore cannot retrieve the rationale from tracker state.

Required correction: add an additive typed record field (or a documented structured metadata field), persist the rationale during selection, and cover retrieval after the run. Keep scoring/ranking independent of the optional rationale.

## Resolved findings

### REV-001 — Resolved 2026-07-11 23:00 UTC — Auth DoD entrypoint was missing

A worker added `src/auth/check.py`; `python -m src.auth.check --help` and the module-discovery review test now pass. Behavioral coverage was added in `tests/test_auth_check.py`. Live seven-day persistence remains a separate verification gap, not part of this resolution.

### REV-003 — Resolved 2026-07-11 23:25 UTC — Missing selectors were masked as logged-out state

`locator_present()` now distinguishes an absent DOM element from a missing/malformed registry key and raises `SelectorBroken` with snapshot context for the structural case. Claude-owned tests cover missing, malformed, and direct unknown keys.

### REV-004 — Resolved 2026-07-11 23:25 UTC — Discovery refreshes were counted as new ingestion

`sync_jobs()` now checks tracker presence before upsert, increments `jobs_ingested` only for new IDs and `jobs_refreshed` for existing discovered IDs, emits both summary fields, and has updated repeated-run tests.

### REV-005 — Resolved 2026-07-11 23:25 UTC — Auth check failure contract and import warning

`main()` now converts operational exceptions into structured JSON plus exit code 1, and `src.auth.__init__` no longer eagerly imports the executable check module. `python -m src.auth.check --help` runs without the prior runpy warning.

## Known verification gaps

- Live JobRight selectors, credentials, SSO/2FA, extension authentication, seven-day session persistence, and live discovery accuracy remain unverified.
- `is_extension_authenticated()` remains an intentional stub returning `None`.
- Saved-filter application in Epic 3 remains unimplemented.

## Cycle history

- 2026-07-11 — Hardened the contributor coordination contract: corrected the canonical instruction filename to `AGENTS.md`, added a worker/runtime-component registry, introduced file-scoped task claims and handoff fields, required isolated branches/worktrees for concurrent writers, and documented integration checks. No implementation files or Claude-owned progress history were changed in this pass.
- 2026-07-11 22:52 UTC — Initial review created. Inspected current worktree, worker progress claims, Epic 2/3 code, tests, and spec. Recorded four actionable findings; verification isolation remains in progress.
- 2026-07-11 22:56 UTC — Isolated REV-002 to persistent Chromium context startup and added `tests/test_review_state.py` with executable documentation checks, including a strict expected failure for REV-001.
- 2026-07-11 23:00 UTC — Reviewed the worker's new auth-check entrypoint. Closed REV-001 at the file/interface level, opened REV-005 for its unhandled operational failures and runpy warning, and added focused CLI unit tests.
- 2026-07-11 23:03 UTC — No new worker source changes. Confirmed recurring 100%-full root filesystem as the leading environmental explanation for REV-002; preserved the finding pending a clean-space rerun.
- 2026-07-11 23:11 UTC — Reviewed an in-progress REV-004 fix. The new result field is documented but not populated, logged, or tested; static formatting/lint checks pass, but the behavioral finding remains open.
- 2026-07-11 23:25 UTC — Verified fixes for REV-003/004/005 (57 focused tests pass; auth CLI help clean; ruff clean). Reviewed active Epic 4 work, opened REV-006/007, and added Codex-owned acceptance coverage under `tests/e2e/`, including the exact 200-job deterministic/auditable DoD and the submitted-today quota regression. The quota test fails as predicted; no `PROGRESS.md` edit was made.
