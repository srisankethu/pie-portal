"""AI Decision Layer.

The AI interprets a curated, permission-scoped ContextBundle into a concise,
validated recommendation. It never calculates authoritative numbers, never sees
raw data or another org's context, and never executes anything. Every response is
schema- and fact-validated before it can be persisted; on any failure the
deterministic signal still surfaces via a template.
"""
