"""Evidence-gated claim extraction (phase 6). Deterministic rules; no LLM; no inference beyond surface grammar.

See docs/engine/PHASE6_PLAN.md. Claims are only made about a resolved subject, from documents that pass the
identity gate; out-of-scope content (sensitive traits, whereabouts, residence, other people) is never stored.
"""
