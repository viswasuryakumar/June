# User decisions

Record product decisions that agents must not repeatedly reconsider.

## DEC-001 — Human acceptance closes product requests

- Decision: A technically verified request remains delivered until user feedback marks it accepted.
- Reason: Passing tests cannot determine whether the delivered behavior matches the user's intent.
- Revisit when: The user explicitly changes this policy.

## DEC-002 — User requests sit above the engineering spec

- Decision: Approved requests control priority and product intent; the engineering spec remains the
  architecture and safety baseline.
- Reason: Requests should not duplicate shared contracts and safety requirements.
- Conflict rule: Record a new decision before implementing a product request that conflicts with a
  technical safety invariant.
