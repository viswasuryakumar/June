# JobRight Auto-Apply Pipeline — Engineering Spec v1.0

## 1. Overview

**Goal:** An autonomous pipeline that, on a schedule, logs into jobright.ai (existing subscription), discovers newly matched jobs, selects the best candidates against user-defined criteria, triggers JobRight's resume tailoring for each selected job, and executes the application — either via JobRight's own Agent mode or by driving the JobRight Chrome extension on the external job/ATS site — while logging every step to a local tracker and pausing for human input only where required (CAPTCHA, ambiguous form questions, final-submit approval if enabled).

**Non-goals (v1):**
- No CAPTCHA solving/bypassing — always human handoff.
- No cover-letter generation outside JobRight's built-in feature.
- No scraping of job boards directly; JobRight is the sole discovery source.
- No multi-user support; single account, single profile.

**Operating principle:** Prefer orchestrating JobRight's native capabilities (Agent apply, extension autofill, resume tuner) over re-implementing them. Playwright is the driver; JobRight is the engine.

---

## 2. High-Level Architecture

```
┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌───────────────┐
│  Scheduler   │──▶│  Auth/Session │──▶│ Job Discovery │──▶│ Selection      │
│  (cron/queue)│   │  Manager      │   │ & Sync        │   │ Engine         │
└─────────────┘   └──────────────┘   └──────────────┘   └───────┬───────┘
                                                                 ▼
┌─────────────┐   ┌──────────────┐   ┌──────────────────────────────────┐
│  Reporter/   │◀──│  Tracker DB   │◀──│ Application Executor              │
│  Notifier    │   │  (SQLite)     │   │  ├─ Path A: JobRight Agent apply  │
└─────────────┘   └──────────────┘   │  ├─ Path B: Extension-driven ATS  │
                                      │  └─ Resume Tailoring sub-step     │
                                      └────────────┬─────────────────────┘
                                                   ▼
                                      ┌──────────────────────┐
                                      │ Human-in-the-Loop Hub │
                                      │ (CAPTCHA, approvals,  │
                                      │  unknown questions)   │
                                      └──────────────────────┘
```

**Tech stack (recommended):**
- Python 3.11+ with `playwright` (or Node + Playwright — pick one repo-wide)
- Persistent Chromium profile (`launch_persistent_context`) with the JobRight Chrome extension pre-installed (extensions require a persistent context and headed or `--headless=new` mode)
- SQLite for tracker state; `pydantic` models for all inter-module contracts
- `python-dotenv` / OS keychain for secrets
- Notification channel: Slack webhook / Telegram bot / email (pick one)
- Optional LLM (Claude API) for: job-fit scoring rationale, answering free-text application questions from a user-provided knowledge file

**Repo layout:**
```
jobright-autopilot/
├── config/settings.yaml          # criteria, limits, feature flags
├── config/profile.yaml           # answers KB: work auth, salary, notice period, EEO prefs
├── src/
│   ├── auth/                     # Epic 2
│   ├── discovery/                # Epic 3
│   ├── selection/                # Epic 4
│   ├── resume/                   # Epic 5
│   ├── executor/                 # Epic 6 (agent_path.py, extension_path.py, ats_adapters/)
│   ├── hitl/                     # Epic 7
│   ├── tracker/                  # Epic 8
│   └── orchestrator/             # Epic 9
├── selectors/jobright.yaml       # ALL selectors centralized, versioned
├── tests/
│   ├── unit/                     # Claude-owned focused unit tests
│   ├── component/                # Claude-owned bounded integration tests
│   └── e2e/                      # Codex-owned cross-stage acceptance tests
└── runs/                         # per-run artifacts: screenshots, HTML dumps, logs
```

**Testing ownership:** Claude writes and maintains unit/component coverage for implementation details and bounded subsystem behavior. Codex ("sol") writes and maintains end-to-end/acceptance coverage under `tests/e2e/`, exercising user-visible flows across public stage interfaces with external boundaries mocked only when unavoidable. Both agents may run all suites; neither edits the other's test files. Codex requests shared-fixture corrections in `review.md`; Claude implements them and records the result in `PROGRESS.md`. Codex reads but never edits `PROGRESS.md`.

---

## 3. Shared Contracts (build first — everything depends on these)

### 3.1 Data models (pydantic / TypeScript types)

```python
class Job(BaseModel):
    job_id: str                 # JobRight internal id (from URL/DOM)
    title: str
    company: str
    location: str
    remote_type: Literal["remote","hybrid","onsite","unknown"]
    salary_min: int | None
    salary_max: int | None
    match_score: int | None     # JobRight's score
    posted_at: datetime | None
    jobright_url: str
    external_url: str | None    # the actual ATS/company posting
    apply_mode: Literal["agent","extension","manual_only","unknown"]
    raw_description: str

class ApplicationRecord(BaseModel):
    job_id: str
    status: Literal["discovered","selected","resume_tailored",
                    "applying","needs_human","submitted","failed","skipped"]
    resume_variant_path: str | None
    attempts: int
    last_error: str | None
    screenshots: list[str]
    timestamps: dict[str, datetime]

class HITLTicket(BaseModel):
    ticket_id: str
    job_id: str
    kind: Literal["captcha","unknown_question","final_approval","login_2fa","selector_broken"]
    context: dict               # screenshot path, question text, page URL
    resolution: str | None
```

### 3.2 State machine (single source of truth)

`discovered → selected → resume_tailored → applying → (needs_human ⇄ applying) → submitted | failed | skipped`

Every module reads/writes only through `tracker/repository.py`. No module talks to another module's internals — this is what enables parallel agent development.

### 3.3 Selector registry

All CSS/XPath selectors live in `selectors/jobright.yaml` with semantic keys (`login.email_input`, `jobs.card`, `jobs.match_score`, `resume.tailor_button`). Modules reference keys, never raw selectors. One agent owns keeping this file current.

`login.challenge_indicator` was added by the Epic 2 agent alongside the pre-existing `login.*` keys (implemented — see `selectors/jobright.yaml` and `src/auth/login.py:detect_challenge_screen()`): a generic 2FA/OTP/email-verification challenge-screen indicator, since the original registry had no key for that state. Still a structural PLACEHOLDER value like the rest of the registry — no live challenge screen has ever been observed (no credentials in this environment); verify/replace it the first time a human completes a real 2FA challenge.

Eight new `jobs.*` keys were added by the Epic 3 agent alongside the pre-existing `jobs.card`/`jobs.match_score`/`jobs.title`/`jobs.company` (implemented — see `selectors/jobright.yaml` and `src/discovery/feed.py`/`extraction.py`): `feed_url` (the jobs feed entry-point URL — deliberately a URL string, not a CSS selector; reuses the registry's flat semantic-key lookup mechanism per §3.3's own "all selectors live here" intent rather than hardcoding the URL in `feed.py`), `feed_container` (structural — resolved via the raising `resolve_locator()`, since a feed that never renders it is a genuine break, not a normal empty-results state), `card_link` (href source for `job_id`/`jobright_url` derivation — see the callout below), `location`, `salary`, `posted_at`, `detail_description`, `detail_external_link`. All eight are structural PLACEHOLDER values like the rest of the registry — no live jobright.ai DOM has ever been observed in this environment; verify/replace against the real jobs feed and job-detail page markup before trusting extraction results. `apply.agent_button`/`apply.extension_autofill_button` (pre-existing, Epic 6-facing) are *reused, not duplicated*, by Epic 3's `_determine_apply_mode()` to set `apply_mode` during detail enrichment.

**job_id / jobright_url derivation assumption (documented design decision — see `derive_job_id()`/`derive_jobright_url()` in `src/discovery/extraction.py`):** `job_id` and `jobright_url` are both required `Job` fields with no default (§3.1), so extraction must never omit them even when a card's DOM gives us nothing usable. The implemented rule is: take the card link's `href` (via the `jobs.card_link` selector), strip the query string, and use the **last non-empty path segment** as `job_id` (this matches the `https://jobright.ai/jobs/<job_id>` shape already assumed by `tests/test_contracts.py`/`tests/test_tracker_repository.py`'s fixtures, but has never been verified against real JobRight markup). If no href is present at all, or it has no usable path segment, a deterministic synthetic id is used instead: `unknown-<sha1prefix>`, a 16-hex-char SHA-1 prefix hashed from `company|title|location|card-index`. **Known limitation, not overstated as safe:** a synthetic `unknown-<hash>` id is only guaranteed unique relative to other synthetic ids derived from the same four-part seed; it is not cryptographically or structurally guaranteed to never collide with a real, href-derived `job_id` that happens to share the `unknown-` prefix pattern or, in the (extremely unlikely but not impossible) case of a hash collision, with another synthetic id from a different seed. Downstream epics (Selection, Executor, Tracker reporting) that key off `job_id` should treat an `unknown-`-prefixed id as a signal of degraded extraction confidence, not a fully trustworthy identity — this has not been an issue in fixture testing but is flagged here because it is a real, if narrow, correctness gap rather than a purely theoretical one.

**Fuzzy-dedupe algorithm — concretization of S3.3.1's "secondary fuzzy dedupe" (see `normalize_title_company_location()`/`fuzzy_key()`/`find_fuzzy_duplicate()` in `src/discovery/dedupe.py`):** the implemented algorithm is casefold + whitespace-collapse on each of (company, title, location), then an **exact match** on that normalized 3-tuple against every other job already known to the tracker. This is simpler than "fuzzy" might imply: there is no edit-distance/similarity scoring, no stemming, and no synonym/semantic matching, so it catches only byte-for-byte reposts of the same listing text under a new `job_id` — e.g. "Senior Backend Engineer" reposted verbatim a week later, not "Sr. Backend Eng" vs. "Senior Backend Engineer" (which would slip through as two distinct jobs today). A future epic wanting stronger repost-catching (true fuzzy/similarity matching, e.g. Levenshtein/Jaro-Winkler on title or an LLM-based near-duplicate check) should treat this as the current ceiling, not assume `find_fuzzy_duplicate()` already does that.

### 3.4 Shared exceptions (implemented — see `src/contracts/exceptions.py`)

All cross-module errors subclass `JuneError`. As of Epic 2, the hierarchy is: `InvalidTransition`, `SecretsError`, `ConfigError`, `SelectorBroken` (Epic 1), plus `LoginFailed(attempts, max_failures)` — added by the Epic 2 agent for T2.3.3's hard-stop-after-N-consecutive-login-failures requirement, which the spec had flagged as an example of a plausible new exception type but did not name explicitly. Raised by `SessionManager.relogin()` (`src/auth/session.py`) once `consecutive_failures >= max_failures`; the orchestrator (Epic 9, not yet built) should catch it to notify and halt the run rather than retry further.

---

## 4. Epics (Gists), Tasks, and Subtasks

Each epic below is written to be handed to an independent agent. **Dependencies** and **Definition of Done (DoD)** are explicit. Epics 2–8 can be developed in parallel once Epic 1 lands (they mock the tracker + selector registry).

---

### EPIC 1 — Foundation & Shared Infrastructure
**Owner-agent scope:** repo scaffolding, contracts, config, logging. **Dependencies:** none. **Blocks:** everything.

- **T1.1 Repo + tooling scaffold**
  - S1.1.1 Init repo, package manager, lint/format (ruff/black or eslint/prettier), pre-commit hooks.
  - S1.1.2 Install Playwright + browsers; verify `launch_persistent_context` with a dummy extension loads.
  - S1.1.3 Makefile / CLI entrypoints: `run`, `run --dry-run`, `discover-only`, `resume-hitl <ticket_id>`.
- **T1.2 Config system**
  - S1.2.1 `settings.yaml` schema: max_applications_per_day, min_match_score, title include/exclude regexes, location/remote rules, salary floor, blocklisted companies, approval_mode (`auto` | `approve_each` | `approve_batch`), active_hours, per-domain rate limits. Extended by Epic 3 (`max_posting_age_days`, and — implemented in `src/config/settings.py`/`config/settings.yaml`, see Epic 3 T3.1.2/T3.2.2 — `max_discovery_pages` default 10, the scroll-and-settle pagination hard cap, and `discovery_enrichment_score_threshold` default 50, the coarse match-score gate for opening a job's detail page during discovery; deliberately separate from and looser than `min_match_score` default 70, since discovery wants to enrich anything plausibly interesting while Selection applies the stricter final filter) and Epic 2 (`max_login_failures`, `login_backoff_base_seconds` — see Epic 2 T2.3.3, implemented in `src/config/settings.py`/`config/settings.yaml`).
  - S1.2.2 `profile.yaml` schema: canonical answers (work authorization, sponsorship, notice period, salary expectation, address, phone, LinkedIn, portfolio, EEO/self-ID preferences incl. "decline to answer" defaults), plus free-text Q&A knowledge snippets.
  - S1.2.3 Secrets loading: `JOBRIGHT_EMAIL`, `JOBRIGHT_PASSWORD` (or SSO flag), notifier tokens — env/keychain only; fail-fast validation at startup; assert secrets never appear in logs.
- **T1.3 Contracts package**
  - S1.3.1 Implement models from §3.1; JSON-schema export so non-Python agents can validate.
  - S1.3.2 Tracker repository interface (`get_jobs(status)`, `transition(job_id, to_status, meta)`) with an in-memory fake for other agents' tests.
- **T1.4 Observability**
  - S1.4.1 Structured JSON logging (run_id, job_id, step, duration); log redaction filter for secrets/PII.
  - S1.4.2 Artifact capture helper: `snapshot(page, label)` → screenshot + HTML dump into `runs/<run_id>/`.
  - S1.4.3 Selector-miss detector: any failed locator auto-captures snapshot and raises `SelectorBroken` (routes to HITL kind `selector_broken`).

**DoD:** `make run --dry-run` executes an empty pipeline end-to-end with fake data; contracts published; other epics can start against fakes.

---

### EPIC 2 — Authentication & Session Management (Playwright ↔ JobRight)
**Dependencies:** Epic 1. **Blocks:** 3, 5, 6.

- **T2.1 Persistent browser context**
  - S2.1.1 Create/load persistent Chromium profile dir (`~/.jobright-autopilot/profile`); pin viewport, timezone, locale to stable values. (implemented — see `src/auth/context.py`: `BrowserContextConfig`, `DEFAULT_PROFILE_DIR`, `DEFAULT_VIEWPORT`/`DEFAULT_TIMEZONE_ID`/`DEFAULT_LOCALE`.)
  - S2.1.2 Load the JobRight Chrome extension into the context: locate the unpacked extension path (or install once manually into the profile and reuse); verify extension service worker is registered on startup; record extension ID. (implemented — see `get_extension_id()` in `src/auth/context.py`. Deviation: `launch_persistent_context`'s own `headless=` kwarg is never used to express headlessness because it selects a separate `chrome-headless-shell` binary not guaranteed to be installed alongside regular Chromium in this sandbox; headlessness is instead always expressed via the `--headless=new` launch arg — see `build_launch_args()` — the same pattern already proven in `tests/test_persistent_context_extension.py`.)
  - S2.1.3 Health check: `is_logged_in()` — navigate to jobright.ai dashboard, detect authenticated state (avatar/menu selector) vs. redirect to login. (implemented — see `is_logged_in()`/`locator_present()` in `src/auth/context.py`; deliberately non-raising against `login.dashboard_indicator` since "logged out" is a normal state, not a selector break.)
- **T2.2 Login flow**
  - S2.2.1 Email/password path: fill `login.email_input`, `login.password_input`, submit; explicit waits on network-idle + dashboard selector. (implemented — see `login_with_credentials()` in `src/auth/login.py`; uses the raising `resolve_locator()`, not the non-raising presence check, because a missing field on the login form itself is a genuine `SelectorBroken`.)
  - S2.2.2 Google SSO path (if account uses it): detect SSO button, drive Google auth pages; note that Google may block automated sign-in → on detection, open HITL ticket `login_2fa` and wait for human to complete in the headed browser window. (deviation/concretization: the implementation never attempts to drive Google's auth pages at all — `sso_detected()` only detects the SSO button/config flag and immediately opens the `login_2fa` ticket via `open_login_hitl_ticket()`, treating SSO and 2FA/challenge screens as the same "hand off to a human" case. See `src/auth/login.py`.)
  - S2.2.3 2FA/OTP/email-verification handling: detect challenge screens → HITL ticket with screenshot; poll for resolution; resume. (implemented — see `detect_challenge_screen()` (checked both before and after credential submit), `open_login_hitl_ticket()` which attaches a `snapshot()` screenshot+HTML dump to the ticket context, and `await_ticket_resolution()` for the polling/resume step, all in `src/auth/login.py`.)
  - S2.2.4 Post-login: persist storage state as backup (`storage_state.json`) in addition to the persistent profile. (implemented — see `persist_storage_state()` in `src/auth/context.py`, invoked automatically by `login()` on a successful email/password login.)
- **T2.3 Session lifecycle**
  - S2.3.1 Auto-relogin on session expiry mid-run (detect auth redirects globally via a navigation guard). (implemented — see `SessionManager.ensure_logged_in()`/`relogin()` in `src/auth/session.py`. Concretization: implemented as a synchronous check-then-relogin called by callers before/around each stage, not a background watcher/navigation-event listener — simpler to test, and sufficient for the sequential, one-application-at-a-time execution model Epic 9 describes.)
  - S2.3.2 Also verify the *extension* is authenticated (open extension popup page `chrome-extension://<id>/popup.html` or check its badge state); if the extension has its own login, add HITL one-time setup ticket. **Deferred/open item, not done:** `is_extension_authenticated()` in `src/auth/session.py` is a documented stub that always returns `None` ("unknown") — there is no live JobRight extension or account in this sandbox to reveal the real auth surface (popup DOM? badge text? background-page message?), so implementing it now would be speculative. Whoever picks up live JobRight access next must implement it for real (open the extension popup via `get_extension_id()`, inspect its logged-in indicator, and add the one-time-setup HITL ticket this subtask calls for if the extension turns out to need separate login) before T2.3.2 can be marked done.
  - S2.3.3 Backoff + jitter on repeated login failures; hard-stop after N failures with notification (never hammer the login endpoint). (implemented — see `SessionManager.relogin()` in `src/auth/session.py`: exponential backoff (`login_backoff_base_seconds * 2**(failures-1)` + random jitter) via injectable `sleep_fn`/`jitter_fn`, hard-stopping with the new `LoginFailed` exception — §3.4 — after `max_login_failures` consecutive failures. A `hitl_pending` outcome does not count against the failure budget, since a human is already in the loop at that point. "Notification" beyond raising `LoginFailed` is not yet wired to a real notifier — that's Epic 9/orchestrator's job once it exists.)

**Concrete interface (implemented — see `src/auth/*.py`, re-exported from `src/auth/__init__.py`):** the spec's §5 parallelization map named only `session.get_context()`/`is_logged_in()` as the interfaces Epic 2 produces; the actual shape other epics (3, 5, 6) should code against is:
- `src/auth/context.py`: `get_context(config: BrowserContextConfig | None) -> contextmanager[BrowserContext]`; `BrowserContextConfig(profile_dir, extension_path, headless, viewport, timezone_id, locale, extra_args)`; `get_extension_id(context, timeout_ms=10_000) -> str | None`; `is_logged_in(page, registry, run_id, timeout_ms=3000, logger=None) -> bool`; `persist_storage_state(context, profile_dir) -> Path`; `locator_present(page, registry, key, timeout_ms=3000) -> bool` (non-raising presence check, reusable outside auth).
- `src/auth/login.py`: `LoginOutcome(status: Literal["success","hitl_pending","failed"], ticket_id: str|None, error: str|None)`; `login(page, registry, secrets, tracker, run_id, job_id=SESSION_JOB_ID, logger=None, timeout_ms=8000) -> LoginOutcome` (the single entrypoint executor/orchestrator code should call); `sso_detected()`, `detect_challenge_screen()` for lower-level checks; `open_login_hitl_ticket(tracker, page, run_id, reason, job_id, logger=None) -> LoginOutcome` (status always `"hitl_pending"`); `await_ticket_resolution(tracker, ticket_id, timeout_s=1800, poll_interval_s=5, sleep_fn, now_fn) -> bool`.
- `src/auth/session.py`: `SessionManager(registry, secrets, tracker, run_id, max_failures=3, backoff_base_seconds=2.0, logger=None, login_fn=login, sleep_fn=time.sleep, jitter_fn=random.random)` with `.ensure_logged_in(page, job_id=SESSION_JOB_ID) -> LoginOutcome` and `.relogin(page, job_id=SESSION_JOB_ID) -> LoginOutcome` (raises `LoginFailed`); `is_extension_authenticated(context, extension_id) -> bool | None` (stub, see S2.3.2 above).

Any Epic 3/5/6/7 agent building against Epic 2 should import from `src.auth` (the curated `__init__.py` re-export), not the submodules directly, unless they need something not yet re-exported.

> **Implementation update (REV-001 resolved):** `src/auth/check.py` now provides the command named by the DoD below. Its local CLI/unit surface is implemented; the live seven-day persistence criterion remains open and must not be inferred from module existence.

**DoD:** `python -m src.auth.check` reliably returns logged-in state across restarts for ≥7 days without re-entering credentials; 2FA path produces a HITL ticket and resumes cleanly. **Open verification gap:** neither half of this DoD has been exercised against the live site — there are no `JOBRIGHT_EMAIL`/`JOBRIGHT_PASSWORD` secrets or jobright.ai access in this sandbox, and every `login.*` selector remains an Epic 1 placeholder. What *has* been verified here (per PROGRESS.md, 2026-07-11 entry): all code paths above against local fixtures (dummy extension, `data:` URL pages) — logged-out detection, successful login, SSO/challenge-screen HITL handoff, ticket-resolution unblocking, backoff/hard-stop/recovery, and persistent-context+extension launch. A future agent with real credentials must still: (a) replace the placeholder selectors in `selectors/jobright.yaml` against the live site, (b) run a real multi-day persistent-profile session to confirm the ≥7-day claim, (c) confirm real SSO/2FA challenge screens are actually caught by `sso_detected()`/`detect_challenge_screen()` (selector keys may need adjusting), and (d) implement `is_extension_authenticated()` per the S2.3.2 note above. Do not treat this DoD as satisfied until that live verification happens — it is tracked here as an explicitly open task, not silently dropped.

---

### EPIC 3 — Job Discovery & Sync
**Dependencies:** Epics 1, 2 (can develop against a saved HTML fixture of the jobs page). **Blocks:** 4.

- **T3.1 Jobs feed navigation**
  - S3.1.1 Navigate to JobRight "Recommended/Jobs" page; apply saved filters if the UI supports it (set once, verify each run). (implemented — see `navigate_to_jobs_feed()` in `src/discovery/feed.py`; `jobs.feed_url` is resolved through the selector registry as a URL string rather than a CSS selector — see §3.3. Deviation/open item: "apply saved filters" is not implemented — no live JobRight UI exists in this sandbox to observe a filters affordance at all, so this remains open for a future agent with live access.)
  - S3.1.2 Infinite-scroll / pagination handler: scroll-and-settle loop with max-pages cap and no-new-cards termination condition. (implemented — see `scroll_and_collect_cards()` in `src/discovery/feed.py`: `page.mouse.wheel()` + fixed settle pause + best-effort network-idle wait, terminating on `no_new_cards` or `max_pages_reached` — both normal outcomes, returned as `PaginationResult.terminated_reason`, see the Concrete interface block below. The pages cap is config-driven via the new `max_discovery_pages` setting, see the extended S1.2.1 bullet in Epic 1.)
- **T3.2 Job card extraction**
  - S3.2.1 Parse each card → `Job` model: title, company, match score, salary, location, posted time, job_id from card link. (implemented — see `extract_job_card()` in `src/discovery/extraction.py`. **job_id-derivation is a documented design decision, not verified against a live site** — see the callout under §3.3 above.)
  - S3.2.2 Job detail enrichment: open detail view for candidates above a coarse score threshold; extract full description, external apply URL, and which apply affordance JobRight shows ("Apply with Agent" vs "Autofill/Apply with extension" vs external link only) → set `apply_mode`. (implemented — see `enrich_job_detail()`/`_determine_apply_mode()` in `src/discovery/extraction.py`; reuses the existing `apply.agent_button`/`apply.extension_autofill_button` selector keys rather than adding new ones. Note: the detail-page `goto()` call is wrapped in a try/except — a dead/unreachable `jobright_url` logs a `job_detail_navigation_failed` warning and returns the job unchanged instead of crashing the whole run; this was a genuine bug the Code agent found and fixed during its own smoke-testing before handoff, per PROGRESS.md 2026-07-11 "Code Agent (Epic 3)" entry, point (1) — confirmed handled.)
  - S3.2.3 Resilience: every field extraction is optional-with-default; a missing field never kills the run; log field-level extraction stats per run. (implemented — see `ExtractionStats`/`log_extraction_stats()` in `src/discovery/extraction.py`, emitting a `job_extraction_stats` log event once per run with a per-field miss counter.)
- **T3.3 Deduplication & persistence**
  - S3.3.1 Upsert into tracker keyed on `job_id`; secondary fuzzy dedupe on (company + normalized title + location) to catch reposts. (implemented — see `find_fuzzy_duplicate()`/`fuzzy_key()` in `src/discovery/dedupe.py`, and `TrackerRepository.add_job()`'s own idempotent-create behavior in `src/tracker/repository.py` for the exact-`job_id` upsert half. **Concretization — simpler than "fuzzy" might imply:** see the callout under §3.3 above.)
  - S3.3.2 Skip anything already in a terminal or in-flight state. (implemented — see `should_skip_existing()` in `src/discovery/dedupe.py`: skips any tracked job whose status is not `discovered`, covering both terminal and in-flight states in one check since the state machine (§3.2) only ever moves forward from `discovered`.)
  - S3.3.3 Freshness policy: only ingest jobs posted within `config.max_posting_age_days`. (implemented — see `is_stale()` in `src/discovery/dedupe.py`, best-effort per S3.2.3's resilience mandate: a job whose `posted_at` couldn't be parsed is never dropped for being "stale" — only a *positively known* stale date causes a skip. Known gap: the posted-time parser only recognizes "N minutes/hours/days/weeks/months ago" and "today"/"just now" phrasings, not absolute dates — see the DoD verification gap below.)

**Concrete interface (implemented — see `src/discovery/*.py`, re-exported from `src/discovery/__init__.py`):** the spec's §5 parallelization map named only "`Job` records in tracker" as Epic 3's produced interface; the actual shape Epic 4 (Selection) and later epics should code against is:
- `src/discovery/sync.py`: `sync_jobs(page, registry, tracker, settings, run_id, *, logger=None, base_url=DEFAULT_JOBRIGHT_BASE_URL, enrichment_score_threshold=None, max_pages=None) -> DiscoveryRunResult` — the single top-level entrypoint tying T3.1+T3.2+T3.3 together (navigate → paginate → extract every card → skip already-tracked/in-flight → fuzzy-dedupe → freshness-filter → enrich survivors above the coarse score threshold via a fresh detail page → `tracker.add_job()`); `DiscoveryRunResult(jobs_seen, jobs_ingested, jobs_refreshed, jobs_skipped_existing, jobs_skipped_fuzzy_duplicate, jobs_skipped_stale, jobs_enriched, pages_scrolled, terminated_reason, extraction_stats: dict)` is both the return value and what gets logged as the `discovery_run_summary` event. `jobs_ingested` counts genuinely new tracker IDs; `jobs_refreshed` counts previously discovered IDs whose `Job` snapshot was updated on a later run (REV-004 resolution), so repeated discovery does not misreport refreshes as new ingestion.
- `src/discovery/feed.py`: `navigate_to_jobs_feed(page, registry, run_id, *, timeout_ms=8000, logger=None) -> None` (raises `SelectorBroken` if `jobs.feed_container` never resolves — a genuine structural break); `scroll_and_collect_cards(page, registry, run_id, *, max_pages=10, settle_ms=500, logger=None) -> PaginationResult(cards: list[Locator], pages_scrolled: int, terminated_reason: Literal["no_new_cards","max_pages_reached"])`.
- `src/discovery/extraction.py`: `extract_job_card(card, registry, *, base_url, index, stats: ExtractionStats) -> Job` (never raises — every field is independently best-effort); `derive_job_id(href, fallback_seed) -> tuple[str, bool]` and `derive_jobright_url(href, base_url, job_id) -> str` (the href-parsing rule — see §3.3 callout); `enrich_job_detail(detail_page, registry, job, *, run_id, timeout_ms=8000, logger=None) -> Job` (expects a fresh `page.context.new_page()`, not the feed page itself, so the feed's already-collected card Locators are never invalidated; caller must close `detail_page`); `ExtractionStats` (dataclass of per-field miss counters) / `log_extraction_stats(logger, run_id, stats)`.
- `src/discovery/dedupe.py`: `normalize_title_company_location(company, title, location) -> tuple[str,str,str]` / `fuzzy_key(job: Job) -> tuple[str,str,str]` (the S3.3.1 concretization — see §3.3 callout); `find_fuzzy_duplicate(tracker, job, *, exclude_job_id=None) -> str | None` (scans `tracker.get_jobs()`/`get_job_details()`, no new tracker method added); `is_stale(job, max_posting_age_days, *, now_fn=...) -> bool`; `should_skip_existing(record: ApplicationRecord | None) -> bool`.

Any Epic 4/5/6 agent building against Epic 3 should import from `src.discovery` (the curated `__init__.py` re-export), not the submodules directly, unless something not yet re-exported is needed.

**DoD:** A discovery run against the live site ingests ≥95% of visible cards into the tracker with correct `apply_mode`, is idempotent when run twice back-to-back, and completes within a configurable page/time budget. **Open verification gap (mirroring Epic 2's T2.1 7-day DoD gap):** none of this DoD has been exercised against the live site — there is no jobright.ai access in this sandbox, and every `jobs.*`/reused `apply.*` selector remains a PLACEHOLDER. What *has* been verified here (per PROGRESS.md, 2026-07-11 "Code Agent (Epic 3)" entry): a scratch smoke script driving the full pipeline against synthetic fixture HTML — card extraction across well-formed and deliberately malformed cards, `enrich_job_detail()` across all four apply-affordance combinations, and two full back-to-back `sync_jobs()` runs against the same fixture+tracker proving idempotent ingestion counts with zero duplicate tracker rows on the second run. A future agent with live access must still: (a) replace every new `jobs.*` PLACEHOLDER selector with values verified against the real site, (b) confirm the job_id-from-href assumption (last non-empty path segment) actually matches real JobRight card link URLs — if links use query params or a different path shape, `derive_job_id()`'s rule needs revising, (c) confirm the relative-time-phrasings posted-time parser covers what JobRight actually renders (it may show absolute dates instead, which `_extract_posted_at()` cannot currently parse — it degrades to `None`/not-stale rather than guessing, so the freshness filter may under-trigger against real data until this is checked), and (d) run a live discovery pass twice back-to-back to confirm the ≥95%-card and idempotency numbers for real. Do not treat this DoD as satisfied until that live verification happens.

---

### EPIC 4 — Selection Engine
**Dependencies:** Epic 1 (pure logic — fully parallelizable; consumes tracker fixtures). **Blocks:** 5.

> Note: Epic 3 (Discovery) is implemented and populates the tracker via `sync_jobs()` — see Epic 3's "Concrete interface (implemented)" block for the `Job`/`DiscoveryRunResult` shape Selection consumes; in particular, a small share of `job_id`s may be synthetic (`unknown-<hash>`, see §3.3) rather than derived from a real JobRight URL when card extraction found no usable href.

- **T4.1 Hard filters**
  - S4.1.1 Apply config rules: min JobRight match score, title include/exclude regex, location/remote policy, salary floor (treat unknown salary per config flag), company blocklist, posting age.
- **T4.2 Scoring & ranking**
  - S4.2.1 Composite score = weighted(JobRight match score, keyword overlap between description and user skill list, recency, salary fit). Weights in config.
  - S4.2.2 Optional LLM rationale pass: for top-K, generate a 2-line "why this fits / risks" note stored on the record (used in approval UI and reports). Feature-flagged; pipeline must run without it.
- **T4.3 Daily quota & queueing**
  - S4.3.1 Select top N respecting `max_applications_per_day` minus already-submitted-today.
  - S4.3.2 Transition chosen jobs `discovered → selected`; everything else remains `discovered` (re-evaluated next run) or `skipped` with reason code.
  - S4.3.3 If `approval_mode != auto`: emit a batch approval HITL ticket (list of selected jobs + rationale) and gate on response.

**DoD:** Given a fixture set of 200 jobs and a config, selection is deterministic, respects quota, and every skip has a machine-readable reason.

---

### EPIC 5 — Resume Tailoring (via JobRight tuner)
**Dependencies:** Epics 1, 2; UI flows can be built against recorded traces. **Blocks:** 6 (per-job).

- **T5.1 Baseline resume setup (one-time)**
  - S5.1.1 Verify a master resume exists in the JobRight account; if not, upload from `config.master_resume_path` via the resume section.
  - S5.1.2 Snapshot master resume version/hash so tailored variants can be traced to a baseline.
- **T5.2 Per-job tailoring flow**
  - S5.2.1 From the job detail page, trigger JobRight's "tailor/customize resume for this job" action; wait for generation to finish (poll for completion indicator, generous timeout, progress logging).
  - S5.2.2 Review guardrails: extract the tailored resume text; run automated sanity checks — no fabricated employers/titles/dates vs. master resume (diff against a parsed master), contact info intact, length sane. Fail → mark `needs_human` with diff attached.
  - S5.2.3 Accept/save the tailored variant in JobRight so the Agent/extension uses it for this application; also download a PDF copy to `runs/<run_id>/resumes/<job_id>.pdf` for the tracker.
  - S5.2.4 Transition `selected → resume_tailored`; store variant path + JobRight variant identifier on the record.
- **T5.3 Fallback**
  - S5.3.1 If tailoring fails twice, config flag decides: apply with master resume (log it) or skip job with reason `resume_tailor_failed`.

**DoD:** For 10 consecutive selected jobs, tailored variants are generated, sanity-checked, saved to the account, and archived locally, with zero silent fabrication passing the diff check.

---

### EPIC 6 — Application Executor (the core)
**Dependencies:** Epics 1, 2, 5; ATS adapters (T6.3) are independently parallelizable per-ATS. 

Routing: read `apply_mode` → Path A (JobRight Agent), Path B (extension on external ATS), else `skipped:manual_only` (or HITL if config says surface them).

- **T6.1 Path A — JobRight Agent apply**
  - S6.1.1 On job detail page, click "Apply with Agent" (selector key `apply.agent_button`); confirm the tailored resume variant is the one attached (verify variant name in the dialog if shown).
  - S6.1.2 Monitor agent progress: poll the JobRight application-status UI / orchestrator page for terminal states (submitted / needs input / failed); define per-state selectors; timeout with status snapshot.
  - S6.1.3 If the Agent asks clarifying questions in its UI: answer from `profile.yaml` via a question-matcher (exact → fuzzy → LLM match against knowledge file); confidence below threshold → HITL ticket `unknown_question`.
  - S6.1.4 On success, capture confirmation screenshot + any confirmation text/ID → `submitted`.
- **T6.2 Path B — Extension-driven external apply**
  - S6.2.1 Open `external_url` in a new page within the same persistent context (extension active). Handle interstitials: cookie banners (decline non-essential), "apply on company site" hops, login-required ATS (→ HITL or skip per config).
  - S6.2.2 Trigger JobRight extension autofill: detect the extension's injected UI/FAB on the page or open its popup and invoke autofill for the current tab; wait for fill completion signal.
  - S6.2.3 Post-fill audit (critical): walk all form fields; classify each as `filled_ok / empty_required / suspicious` (e.g., name in phone field). Fill gaps from `profile.yaml` using the question-matcher; upload tailored resume PDF to file inputs if the extension didn't; re-audit.
  - S6.2.4 Multi-step forms: loop next-button → audit → fill until final review page; per-page snapshot; max-steps cap.
  - S6.2.5 CAPTCHA / bot-check detection (reCAPTCHA, hCaptcha, Cloudflare turnstile iframes): never attempt to solve — snapshot, HITL ticket `captcha`, pause with the page held open (or persist URL+state to resume), continue after human solves it.
  - S6.2.6 Final submit: if `approval_mode` requires it, HITL ticket `final_approval` with full-page screenshot of the review step; else click submit; then verify success signal (confirmation text/URL/email hint) before marking `submitted` — no signal = `needs_human`, not success.
- **T6.3 ATS adapters (parallel-friendly sub-workstream)**
  - S6.3.1 Adapter interface: `detect(page) -> bool`, `quirks: AdapterQuirks` (login wall? multi-step? file upload selector? known question phrasings?).
  - S6.3.2 Ship adapters for the big five first: Greenhouse, Lever, Workday, Ashby, iCIMS. **Each adapter is a standalone task assignable to a separate agent.**
  - S6.3.3 Generic fallback adapter using semantic field matching (label/aria-name → profile key).
- **T6.4 Failure policy**
  - S6.4.1 Retry matrix: transient (network/timeout) → up to 2 retries with backoff; structural (selector miss, unknown ATS) → HITL; hard (job closed, already applied) → terminal with reason.
  - S6.4.2 Global circuit breaker: >X consecutive failures aborts the run and notifies.

**DoD:** End-to-end on 5 real jobs per path in supervised mode: correct resume attached, all required fields filled or escalated, zero false "submitted" statuses, CAPTCHA correctly pauses instead of failing.

---

### EPIC 7 — Human-in-the-Loop Hub
**Dependencies:** Epic 1 only (fully parallelizable). **Consumed by:** 2, 4, 5, 6.

- **T7.1 Ticket store + API**
  - S7.1.1 CRUD over `HITLTicket` in tracker DB; blocking helper `await_resolution(ticket_id, timeout)` used by executor coroutines.

  > **Forward-looking proposal (not settled, from the Epic 2 implementation agent — see PROGRESS.md 2026-07-11):** since Epic 7 doesn't exist yet, Epic 2's login flow (`src/auth/login.py`) built its own small `await_ticket_resolution(tracker, ticket_id, timeout_s, poll_interval_s)` bridge directly against the tracker's `add_ticket()`/`get_ticket()` surface, rather than waiting for T7.1.1's real `await_resolution()`. When Epic 7 lands, its owner should evaluate whether: (a) `await_ticket_resolution` should be superseded by/aliased to the real `await_resolution(ticket_id, timeout)`, and (b) the polling helper itself should move out of `src/auth/login.py` into a `src/hitl/` module so all HITL-awaiting call sites (Epic 2's login gate, Epic 6's executor, etc.) share one implementation instead of each epic growing its own. This is a proposal to consider, not a decision — Epic 2's version is a stopgap, not a claim on Epic 7's design.
- **T7.2 Notification + response channel**
  - S7.2.1 Push ticket (kind, job, screenshot, deep link/URL) to Slack/Telegram/email.
  - S7.2.2 Response ingestion: simplest v1 = CLI (`autopilot hitl resolve <id> --answer "..."` / `--approve` / `--reject`); v2 = inline Slack buttons/reply parsing.
- **T7.3 Pause/resume semantics**
  - S7.3.1 For CAPTCHA/approval on a live page: keep the browser page alive up to `hitl_hold_minutes`; human solves it directly in the headed window; automation detects resolution (captcha iframe gone / approval received) and continues.
  - S7.3.2 If hold expires: persist state (URL, filled fields snapshot, job_id), release the page, mark `needs_human`; a later `resume` command re-opens and re-fills.
- **T7.4 Answer learning loop**
  - S7.4.1 Every human-answered `unknown_question` is appended (question, answer, ats, job context) to `profile.yaml` learned-answers section for future auto-answering.

**DoD:** From a fired CAPTCHA ticket, a human can solve it in the open window and the run continues without restart; unknown questions round-trip through Slack/CLI and are learned.

---

### EPIC 8 — Tracker, Logging & Reporting
**Dependencies:** Epic 1. **Parallelizable.**

- **T8.1 Tracker DB**
  - S8.1.1 SQLite schema: `jobs`, `applications`, `hitl_tickets`, `runs`, `events` (append-only audit log of every transition with actor = automation|human).
  - S8.1.2 Migration tooling (alembic or simple versioned SQL).
- **T8.2 Reporting**
  - S8.2.1 End-of-run summary → notifier: discovered / selected / submitted / needs_human / failed counts + per-job one-liners + links to screenshots.
  - S8.2.2 Daily digest + rolling stats (submissions this week, success rate by ATS, top failure reasons).
  - S8.2.3 CSV/Sheets export of the application log for the user's own tracking.
- **T8.3 Ops dashboards (optional v1.1)**
  - S8.3.1 Minimal local web page (read-only) listing pipeline state and open HITL tickets.

**DoD:** After any run, one notification message fully explains what happened; every submitted application has a screenshot proving submission.

---

### EPIC 9 — Orchestrator, Scheduling & Safety Rails
**Dependencies:** all epics (integration), but the skeleton can be built against fakes from day one.

- **T9.1 Pipeline runner**
  - S9.1.1 Async orchestration of stages with per-stage timeouts; jobs processed sequentially through executor (one application at a time — human-like), discovery/selection batched.
  - S9.1.2 Idempotency: safe to kill and rerun at any point; state machine + `events` log make every step resumable.
  - S9.1.3 `--dry-run` mode: full pipeline through selection + resume tailoring preview, no submissions.
- **T9.2 Scheduling & pacing**
  - S9.2.1 Cron/systemd-timer/launchd entry within `active_hours`; random start jitter.
  - S9.2.2 Human-like pacing inside runs: randomized delays between actions, per-domain rate limits, daily cap enforcement at the orchestrator level (belt-and-suspenders with Epic 4).
- **T9.3 Safety rails**
  - S9.3.1 Kill switch: a `PAUSE` file / config flag halts new applications immediately.
  - S9.3.2 Duplicate-application guard: never apply to a (company, normalized title) pair twice within M days, across job_id reposts.
  - S9.3.3 Budget guard: hard cap on applications/day and /week regardless of config typos (absolute ceiling constant).
- **T9.4 Integration test suite**
  - S9.4.1 Staged supervised runs: 1 job → 3 jobs → 10 jobs, each gated on manual review of tracker + screenshots before raising autonomy level.

**DoD:** A scheduled unattended run completes within limits, produces a report, and the kill switch stops it mid-run cleanly.

---

## 5. Parallelization Map (feed to agents)

| Agent | Epic(s) | Can start after | Interfaces consumed | Interfaces produced |
|---|---|---|---|---|
| A1 | 1 Foundation | now | — | contracts, tracker iface, selector registry, config |
| A2 | 2 Auth/Session | E1 contracts | selector registry, HITL iface (mock) | `session.get_context()`, `is_logged_in()` |
| A3 | 3 Discovery | E1 (fixtures) | session context, tracker | `Job` records in tracker |
| A4 | 4 Selection | E1 | tracker | status transitions + reasons |
| A5 | 5 Resume | E1 (traces), E2 for live | session, tracker, HITL | tailored variant per job |
| A6 | 6 Executor core (T6.1, T6.2, T6.4) | E1; live testing needs E2+E5 | session, HITL, profile KB | submissions |
| A7–A11 | 6 ATS adapters ×5 (T6.3) | adapter interface from A6 (day 1 deliverable) | adapter iface | one adapter each |
| A12 | 7 HITL Hub | E1 | tracker, notifier | ticket API used by all |
| A13 | 8 Tracker/Reporting | E1 | events | DB, reports |
| A14 | 9 Orchestrator | E1 (fakes) | everything | runnable pipeline |

Critical path: **E1 → E2 → (E3, E5) → E6 → E9 integration.** Everything else overlaps.

## 6. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| JobRight UI changes break selectors | Central selector registry, selector-miss auto-snapshots, weekly smoke test |
| Account flagged for bot-like behavior (JobRight or ATS sites) | Persistent real profile, human pacing, headed browser, daily caps, no CAPTCHA bypass; note some ATS/job-site ToS restrict automated submissions — keep volumes modest and approval mode on for sensitive employers |
| Extension autofill API is undocumented / not scriptable | Fallback: use extension for detection only, do form fill natively via ATS adapters (T6.3 generic adapter) |
| Tailored resume contains fabrications | S5.2.2 diff-against-master gate, HITL on anomalies |
| False "submitted" statuses | S6.2.6 requires positive confirmation signal; ambiguous = needs_human |
| Google SSO blocks automation | HITL-assisted first login into persistent profile; sessions persist thereafter |

## 7. Milestones

1. **M1 (Foundation):** E1 done; all agents unblocked.
2. **M2 (Logged-in loop):** E2 + E3 — unattended discovery runs populating the tracker.
3. **M3 (Decisions + resumes):** E4 + E5 — selected jobs get tailored resumes, dry-run reports.
4. **M4 (First submissions, supervised):** E6 Path A + Greenhouse/Lever adapters + E7 — approve-each mode.
5. **M5 (Autonomous):** E9 scheduling, remaining adapters, auto mode with daily caps.
