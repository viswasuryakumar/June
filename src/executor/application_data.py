"""Persistent application-data store (spec S6.2.3 / T6.3.3 / T7.4 groundwork).

The executor fills application forms on three paths (JobRight extension
autofill, native fill, LinkedIn Easy Apply). On every path it needs one
shared answer source to (a) fill empty fields, (b) verify what the
extension filled, and (c) *learn*: whenever a field gets filled — by us,
by the extension, or by a human — the observed question/answer pair is
recorded here so the next application can reuse it.

Two layers, looked up in order:

1. Canonical profile fields from ``config/profile.yaml``
   (:class:`src.config.profile.Profile`) — name/email/phone/address/
   authorization/salary/EEO — matched by keyword heuristics against the
   field's label text.
2. Recorded answers in ``config/application_data.yaml`` — the growing
   store this module owns. Exact normalized-question match first, then
   fuzzy (``difflib``) above a conservative ratio.

The store file is runtime-writable config (like the spec's
"learned-answers" concept, kept in its own file so hand-maintained
profile.yaml never gets machine-rewritten).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from src.config.profile import Profile, load_profile

DEFAULT_PROFILE_PATH = Path("config/profile.yaml")
DEFAULT_STORE_PATH = Path("config/application_data.yaml")

# Below this similarity ratio a stored answer is not trusted for a new
# question — better to leave a field for HITL than to fill it wrongly.
FUZZY_MATCH_THRESHOLD = 0.86


def normalize_question(text: str) -> str:
    """Casefold, strip punctuation/required-markers, collapse whitespace —
    so 'First Name *' and 'first name' hit the same stored answer."""
    text = text.casefold()
    text = re.sub(r"[*:?()\[\]✱]", " ", text)
    text = re.sub(r"\brequired\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_third_party_name_question(text: str) -> bool:
    """True when a field asks for SOMEONE ELSE's name (referral, reference,
    emergency contact, supervisor, ...) — the applicant's own name must
    never answer it, and callers fill a required one with 'N/A' instead."""
    norm = normalize_question(text)
    return "name" in norm and any(marker in norm for marker in _THIRD_PARTY_NAME_MARKERS)


@dataclass
class RecordedAnswer:
    question: str
    answer: str
    source: str  # "profile" | "native_fill" | "extension_observed" | "human"
    ats: str | None = None
    job_id: str | None = None
    recorded_at: str = ""


# Name keywords, and markers of a question asking for SOMEONE ELSE's name
# (a referral, reference, emergency contact, ...). When a question carries
# such a marker, the applicant's own name must never be used to answer it.
_NAME_KEYWORDS = frozenset(
    {
        "first name",
        "given name",
        "last name",
        "family name",
        "surname",
        "full name",
        "your name",
        "legal name",
        "name",
    }
)
_THIRD_PARTY_NAME_MARKERS = (
    "referred",
    "referral",
    "reference",
    "their name",
    "emergency contact",
    "next of kin",
    "supervisor",
    "manager name",
    "recruiter",
    "contact name",
)


# Keyword heuristics mapping a field label to a canonical Profile value.
# Order matters: first hit wins, and more specific phrases come first
# (e.g. "last name" must match before a bare "name").
def _profile_lookup_rules(profile: Profile) -> list[tuple[tuple[str, ...], str | None]]:
    name_parts = profile.full_name.split()
    first = name_parts[0] if name_parts else ""
    last = name_parts[-1] if len(name_parts) > 1 else ""
    salary = profile.salary_expectation
    salary_text = f"{salary.minimum}" if salary.minimum is not None else ""
    return [
        (("first name", "given name"), first),
        (("last name", "family name", "surname"), last),
        (("full name", "your name", "legal name", "name"), profile.full_name),
        (("email",), profile.email),
        (("phone", "mobile", "telephone"), profile.phone),
        (("linkedin",), profile.linkedin_url),
        (("portfolio", "website", "personal site"), profile.portfolio_url),
        (("street", "address line"), profile.address.street),
        (
            ("current location", "location"),
            ", ".join(p for p in (profile.address.city, profile.address.state) if p),
        ),
        (("city",), profile.address.city),
        (("state", "province"), profile.address.state),
        (("zip", "postal"), profile.address.postal_code),
        (("country",), profile.address.country),
        (
            ("authorized to work", "work authorization", "legally authorized"),
            _declinable_to_text(profile.work_authorized),
        ),
        (
            ("sponsorship", "visa"),
            _declinable_to_text(profile.requires_sponsorship),
        ),
        (
            ("notice period",),
            (
                f"{profile.notice_period_days} days"
                if profile.notice_period_days is not None
                else None
            ),
        ),
        (("salary", "compensation", "pay expectation"), salary_text),
        (("gender",), _eeo_to_text(profile.eeo_self_id.gender)),
        (("race", "ethnicity"), _eeo_to_text(profile.eeo_self_id.race_ethnicity)),
        (("veteran",), _eeo_to_text(profile.eeo_self_id.veteran_status)),
        (("disability",), _eeo_to_text(profile.eeo_self_id.disability_status)),
    ]


def _declinable_to_text(value: str) -> str | None:
    if value == "decline_to_answer":
        return None  # never auto-answer a declined field — leave for HITL
    return {"yes": "Yes", "no": "No"}.get(value, value)


def _eeo_to_text(value: str) -> str | None:
    return None if value == "decline_to_answer" else value


@dataclass
class LookupResult:
    answer: str
    source: str  # "profile" | "stored_exact" | "stored_fuzzy"
    matched_question: str | None = None
    stored_source: str | None = None  # the RecordedAnswer.source when matched from the store


class ApplicationDataStore:
    """Load/lookup/record the application answer knowledge base.

    All I/O is confined to :meth:`load` and :meth:`save`; lookup and
    record are pure in-memory operations so tests and the fill loop can
    batch many records and persist once per page.
    """

    def __init__(
        self,
        profile: Profile,
        answers: list[RecordedAnswer] | None = None,
        *,
        store_path: Path | str = DEFAULT_STORE_PATH,
    ) -> None:
        self.profile = profile
        self.answers = answers if answers is not None else []
        self.store_path = Path(store_path)
        self._by_norm: dict[str, RecordedAnswer] = {
            normalize_question(a.question): a for a in self.answers
        }

    # -- construction --------------------------------------------------

    @classmethod
    def load(
        cls,
        *,
        profile_path: Path | str = DEFAULT_PROFILE_PATH,
        store_path: Path | str = DEFAULT_STORE_PATH,
    ) -> ApplicationDataStore:
        profile = load_profile(profile_path)
        answers: list[RecordedAnswer] = []
        store_file = Path(store_path)
        if store_file.exists():
            raw = yaml.safe_load(store_file.read_text(encoding="utf-8")) or {}
            for item in raw.get("answers", []):
                answers.append(RecordedAnswer(**item))
        return cls(profile, answers, store_path=store_path)

    def save(self) -> Path:
        payload = {
            "answers": [
                {
                    "question": a.question,
                    "answer": a.answer,
                    "source": a.source,
                    "ats": a.ats,
                    "job_id": a.job_id,
                    "recorded_at": a.recorded_at,
                }
                for a in self.answers
            ]
        }
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return self.store_path

    # -- lookup ---------------------------------------------------------

    def lookup(self, question: str) -> LookupResult | None:
        """Best answer for a field label, or None (→ leave for HITL)."""
        norm = normalize_question(question)
        if not norm:
            return None

        stored = self._by_norm.get(norm)
        if stored is not None:
            return LookupResult(
                stored.answer, "stored_exact", stored.question, stored_source=stored.source
            )

        third_party_name = any(marker in norm for marker in _THIRD_PARTY_NAME_MARKERS)
        for keywords, value in _profile_lookup_rules(self.profile):
            if not value:
                continue
            # never answer a referral/reference/emergency-contact name field
            # with the applicant's own name
            if third_party_name and any(k in _NAME_KEYWORDS for k in keywords):
                continue
            if any(k in norm for k in keywords):
                return LookupResult(value, "profile")

        best: tuple[float, RecordedAnswer] | None = None
        for stored_norm, answer in self._by_norm.items():
            ratio = SequenceMatcher(None, norm, stored_norm).ratio()
            if ratio >= FUZZY_MATCH_THRESHOLD and (best is None or ratio > best[0]):
                best = (ratio, answer)
        if best is not None:
            return LookupResult(
                best[1].answer, "stored_fuzzy", best[1].question, stored_source=best[1].source
            )
        return None

    # -- record ----------------------------------------------------------

    def record(
        self,
        question: str,
        answer: str,
        *,
        source: str,
        ats: str | None = None,
        job_id: str | None = None,
        now_fn=lambda: datetime.now(UTC),
    ) -> RecordedAnswer:
        """Record an observed/filled answer. Same normalized question
        updates the existing entry instead of duplicating it. Empty
        answers are ignored (never learn a blank)."""
        norm = normalize_question(question)
        answer = answer.strip()
        if not norm or not answer:
            raise ValueError("both question and answer must be non-empty")
        existing = self._by_norm.get(norm)
        if existing is not None:
            # Never downgrade a human-corrected answer with a lower-trust
            # (extension/native) observation — the correction must persist
            # across runs, or the extension re-learns the wrong value.
            if existing.source == "human" and source != "human":
                return existing
            existing.answer = answer
            existing.source = source
            existing.ats = ats or existing.ats
            existing.job_id = job_id or existing.job_id
            existing.recorded_at = now_fn().isoformat()
            return existing
        entry = RecordedAnswer(
            question=question.strip(),
            answer=answer,
            source=source,
            ats=ats,
            job_id=job_id,
            recorded_at=now_fn().isoformat(),
        )
        self.answers.append(entry)
        self._by_norm[norm] = entry
        return entry
