# User product input

This directory is the human-owned product layer above the engineering specification.

- Add one product brief per file under `requests/` using `TEMPLATE.md`.
- Only `status: approved` requests are eligible for automated implementation.
- Record durable product choices in `DECISIONS.md`.
- After delivery, add a feedback file with `accepted` or `changes-required`.
- Approve or reject workflow-improvement proposals under `proposal-decisions/`.

Agents may read these files but never edit them. `jobright-automation-spec.md` remains authoritative for
architecture, contracts, dependencies, and safety. User requests control priority and user-visible
intent. If they conflict, automation stops until the user records a decision.
