## Context

The client can establish authoritative identity from an explicit source field
or the `tile_discarded` event sequence, and can carry that identity through a
safe re-anchor. Five eligible baseline windows were first observed from a
snapshot without either fact. Their current fallback is intentionally marked
`legacy_unresolved`, which is correct but prevents strong completeness.

The identity must therefore be supplied and preserved by the protocol. The
client-side change should remain a consumer and validator, not a generator of
guessed identity.

## Goals / Non-Goals

**Goals:**

- Define one stable identity field for a discard response window.
- Preserve the same identity for peng and chi phases and across `seq=0`.
- Make identity origin and first-seen path observable in client logs.
- Reject malformed, changing, or watermark-derived identities.
- Permit an explicit protocol-skipped status where a deployed protocol version
  cannot provide the field.

**Non-Goals:**

- Deriving identity from snapshot watermark, discard-list length, meld count,
  owner/tile, or timestamps.
- Changing action rules, StateDemand scheduling, rate, retry, or margins.
- Upgrading legacy windows to authoritative evidence without a server contract.

## Decisions

### 1. Accept either explicit source sequence or opaque response_window_id

The preferred field is `source_discard_seq` because the current event protocol
already has a monotonic sequence. An opaque `response_window_id` is an equally
valid alternative when the server cannot expose the source sequence. A
deployment MUST choose one stable field and document its scope; the client will
not combine two potentially different identities.

Alternative considered: use the `/state` watermark. Rejected because it changes
on unrelated events and is not the identity of the discard.

### 2. Attach identity to both event and snapshot representations

`tile_discarded` and authoritative response snapshots MUST carry or refer to
the same identity. A `seq=0` snapshot MUST retain the identity of the pending
window when that window is still open. After a claim, new discard, or round
transition, the old identity MUST not be reused.

Alternative considered: carry only client-side event state. Rejected because a
full re-anchor intentionally replaces the mirror and must remain independently
auditable.

### 3. Validate identity server-side and client-side

The server owns identity allocation and uniqueness. The client validates that
peng/chi agree, that a new source does not reuse an active identity, and that
the field is independent of watermark. A mismatch downgrades the observation
to weak/invalid evidence and MUST be logged with both values.

### 4. Preserve legacy exclusion during migration

Until protocol coverage is verified, snapshot-only records remain
`legacy_unresolved`. Acceptance reports `protocol_skipped_identity` only when
the protocol version explicitly declares that identity is unavailable; the
client cannot set that status based on a missing field alone.

## Risks / Trade-offs

- [Server rollout is partial] → Accept both old and new schema versions while
  keeping separate coverage counters and strong-completeness gates.
- [Identity field is accidentally tied to watermark] → Add replay tests with
  unrelated events and seq=0 re-anchors, and reject changes for one active
  window.
- [Opaque IDs are not retained in historical snapshots] → Require the field in
  every authoritative response snapshot before enabling strong gate.
- [Protocol cannot be changed immediately] → Keep legacy evidence useful for
  diagnostics and weak statistics, but do not weaken the current safety rule.
