## 1. Protocol contract

- [ ] 1.1 Confirm with the service protocol owner whether `source_discard_seq` or an opaque `response_window_id` is the authoritative field for this deployment.
- [ ] 1.2 Document field scope, uniqueness, peng-to-chi reuse, seq=0 retention, round transition, and malformed/missing behavior in the protocol fixture contract.

## 2. Server and fixture coverage

- [ ] 2.1 Add representative event and snapshot fixtures carrying the same identity through response_peng, response_chi, and seq=0 re-anchor responses.
- [ ] 2.2 Add fixtures for repeated same-tile discards, claimed discards, new rounds, identity mismatch, and protocol versions that explicitly skip identity.

## 3. Client validation and provenance

- [ ] 3.1 Consume the selected protocol field and preserve `identity_origin` and `first_seen_via` in event, snapshot, window-confirm, and action records.
- [ ] 3.2 Detect identity mismatch or reuse and downgrade the affected evidence without promoting snapshot watermark or structural guesses.
- [ ] 3.3 Preserve `legacy_unresolved` behavior until protocol coverage is verified; do not add a client-side fallback identity.

## 4. Acceptance coverage

- [ ] 4.1 Add identity coverage and protocol-skipped counters without changing transport/window/game layer denominators.
- [ ] 4.2 Add replay tests proving identity survives seq=0 and peng-to-chi transitions and that legacy windows remain excluded from strong completeness.
- [ ] 4.3 Validate the deployed protocol coverage in a fresh frozen-version run before enabling the strong identity gate.
