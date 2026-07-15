"""Application Executor (spec EPIC 6): post-apply-click routing, form
audit/fill, extension autofill, and the growing application-data store.

Public surface mirrors src/auth/ and src/discovery/'s convention.
"""

from src.executor.application_data import (
    ApplicationDataStore,
    LookupResult,
    RecordedAnswer,
    normalize_question,
)
from src.executor.extension import (
    AutofillAttempt,
    find_installed_jobright_extension,
    trigger_extension_autofill,
)
from src.executor.forms import (
    AdvanceButton,
    FieldAudit,
    FillLoopResult,
    FillReport,
    audit_form_fields,
    fill_field,
    fill_missing_fields,
    find_advance_button,
    find_validation_errors,
    record_observed_answers,
    run_fill_loop,
)
from src.executor.routing import (
    ApplyDestination,
    classify_apply_destination,
    detect_ats,
    has_login_wall,
)

__all__ = [
    "AdvanceButton",
    "ApplicationDataStore",
    "ApplyDestination",
    "AutofillAttempt",
    "FieldAudit",
    "FillLoopResult",
    "FillReport",
    "LookupResult",
    "RecordedAnswer",
    "audit_form_fields",
    "classify_apply_destination",
    "detect_ats",
    "fill_field",
    "fill_missing_fields",
    "find_advance_button",
    "find_installed_jobright_extension",
    "find_validation_errors",
    "has_login_wall",
    "normalize_question",
    "record_observed_answers",
    "run_fill_loop",
    "trigger_extension_autofill",
]
