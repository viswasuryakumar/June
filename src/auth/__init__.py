"""Authentication & Session Management (Epic 2 - spec §4 EPIC 2).

Public surface other epics (discovery, resume, executor) should import
from, matching the spec §5 parallelization map's documented interfaces
(`session.get_context()`, `is_logged_in()`):

    from src.auth import BrowserContextConfig, get_context, is_logged_in
    from src.auth import login, LoginOutcome, SessionManager

No live jobright.ai access exists in this environment (no credentials) -
every function here is built/testable against local fixtures (the dummy
extension under tests/fixtures/, `data:` URL pages) rather than the real
site. See PROGRESS.md for the Epic 2 implementation entry and known gaps.

Note: importing `login` here shadows the `src.auth.login` *module* with
the `login` *function* as an attribute of this package (normal Python
behavior when a package re-exports a name matching its submodule). Use
`from src.auth import login` or `from src.auth.login import ...` (both
work); don't rely on `import src.auth` then `src.auth.login.<other_name>`
attribute-chasing to reach the submodule.

Note: `src.auth.check` (the `python -m src.auth.check` CLI entrypoint) is
deliberately NOT re-exported here, unlike context.py/login.py/session.py.
It's a standalone executable module, not part of this curated library
surface, and eagerly importing it from the package initializer caused a
`RuntimeWarning` about `sys.modules`/`runpy` when actually invoked as
`python -m src.auth.check` (REV-005) - `runpy` re-imports the module as
`__main__` and warns that `src.auth.check` was already present in
`sys.modules` from this package-level import. Import it directly instead:
`from src.auth.check import CheckResult, check_login_state`.
"""

from __future__ import annotations

from src.auth.context import (
    BrowserContextConfig,
    get_context,
    get_extension_id,
    is_logged_in,
    launch_persistent_context,
    locator_present,
    persist_storage_state,
)
from src.auth.login import (
    LoginOutcome,
    await_ticket_resolution,
    detect_challenge_screen,
    login,
    login_with_credentials,
    open_login_hitl_ticket,
    sso_detected,
)
from src.auth.session import SessionManager, is_extension_authenticated

__all__ = [
    "BrowserContextConfig",
    "get_context",
    "get_extension_id",
    "is_logged_in",
    "launch_persistent_context",
    "locator_present",
    "persist_storage_state",
    "LoginOutcome",
    "await_ticket_resolution",
    "detect_challenge_screen",
    "login",
    "login_with_credentials",
    "open_login_hitl_ticket",
    "sso_detected",
    "SessionManager",
    "is_extension_authenticated",
]
