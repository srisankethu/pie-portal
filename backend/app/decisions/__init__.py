"""Decision Service: turn deterministic signals into persisted, validated
decisions — assembling permission-scoped context, invoking the AI layer,
finalizing priority (deterministic base + bounded AI adjustment), and enforcing
idempotency so re-runs refresh rather than duplicate.
"""
