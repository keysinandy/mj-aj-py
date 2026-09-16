# Specification: staged-evaluation-gates

## ADDED Requirements

### Requirement: Evaluation SHALL run in staged gates

Candidates SHALL be evaluated in order: offline gate (every checkpoint), hard-set gate, fast paired gate (256–512 pairs), full paired gate (4096 pairs, unchanged release standard) and runtime gate. A failing gate SHALL stop promotion without running later, more expensive gates.

#### Scenario: offline failure
- **WHEN** mean regret does not improve while p95/catastrophic/special-state metrics regress
- **THEN** the candidate is eliminated before any paired game is played

#### Scenario: fast paired pass
- **WHEN** the candidate survives offline and hard-set gates
- **THEN** a 256–512 pair gate MAY reject obvious failures before the full 4096-pair gate

### Requirement: Gate decisions SHALL be recorded with run metadata

Every run SHALL record run_id, git commit, model/teacher/dataset config hashes, sampling/loss/optimizer config, seeds, and all gate metrics, so any result can be regenerated and attributed.

#### Scenario: unattributable result
- **WHEN** a report lacks required provenance
- **THEN** it MUST NOT be used for a promotion decision

### Requirement: Top-1 accuracy SHALL remain diagnostic

No gate SHALL promote a candidate on top-1 accuracy alone; regret, tail regret and catastrophic rate are the primary offline signals.

#### Scenario: accuracy and regret disagree
- **WHEN** top-1 improves while mean/p95 regret worsens
- **THEN** the candidate is rejected

### Requirement: Runtime gate SHALL measure the network-only release path

The runtime gate SHALL measure end-to-end latency (p50/p95/p99), memory and throughput for the released network-only path.

#### Scenario: slow preprocessing
- **WHEN** end-to-end p99 violates the window gate even though the isolated forward is fast
- **THEN** the candidate fails the runtime gate
