# Spec Delta

## ADDED Requirements

### Requirement: Native legacy two-ply evaluation is optional and semantically equivalent

The system MAY evaluate the legacy two-ply public-information frontier through a Rust `mj_kernels` batch function. When enabled, the native function MUST receive only the root concealed hands, locked count, public visible counts, legal child discard sets, rule/phase eligibility, and deterministic budget. It MUST NOT receive opponent concealed hands, wall order, or hidden policy state. For supported inputs, native scalar metrics and action selection MUST match the Python reference evaluator.

#### Scenario: Native and Python reference agree
- **WHEN** the same roots, visible counts, locked count, legal sets, profile, and node budget are evaluated by Python and Rust
- **THEN** each root's completion state, future improvement weight, future ukeire, best-child discard mass, and final selected action are equal

#### Scenario: Hidden state cannot reach the kernel
- **WHEN** two games differ only in opponent concealed hands or real wall order
- **THEN** the native request payload and returned metrics are identical

### Requirement: Native batching preserves visible material and one-draw semantics

The native evaluator MUST perform exactly one simulated draw and one legal child discard per branch. It MUST update `visible_after_draw` before computing child ukeire, never restore a drawn tile to the unknown pool after the child discard, and reject counts outside the four-copy limit. A frozen branch MUST restrict child discard to the supplied drawn tile.

#### Scenario: Drawn tile is not counted twice
- **WHEN** a tile has one theoretical remaining copy and is selected as the simulated draw
- **THEN** its child visible count is four and its child remaining weight is zero, regardless of the subsequent discard

#### Scenario: Frozen legality is preserved
- **WHEN** the root is in a freeze/grab-discard state
- **THEN** the native result considers only the drawn tile as a child discard and cannot expand the legal action set

### Requirement: Native failures are transactional and observable

The Python adapter MUST validate the native response and discard all partial native metrics if the kernel is missing, raises, returns malformed rows, reports budget exhaustion, or exceeds the profile wall-clock budget. The final action MUST then come from the complete legacy ordering. Evaluation metadata MUST record the requested kernel, actual kernel, kernel version, and fallback reason; missing future fields MUST remain null/missing rather than zero.

#### Scenario: Partial native frontier falls back
- **WHEN** one eligible root completes natively but another root exceeds the node budget
- **THEN** no native future metric affects ordering, the complete legacy action is returned, and the explanation records a native budget fallback

#### Scenario: Old wheel remains safe
- **WHEN** an installed `mj_kernels` wheel has shanten/ukeire but no native legacy function
- **THEN** the evaluator uses the Python reference path without changing the action or treating the old wheel as a failed decision

### Requirement: Kernel selection and release state are versioned

The profile fingerprint MUST include the kernel selector and native kernel version. `MJ_KERNELS=python` MUST force the Python reference path. The native path MUST remain opt-in or explicitly selected until parity, performance, information-safety, and paired-benefit gates pass; no kernel migration may silently change the legacy default.

#### Scenario: Kernel configuration is distinguishable
- **WHEN** the same profile is evaluated once with Python and once with Rust
- **THEN** the profile fingerprints or kernel metadata distinguish the runs and the explanation records the actual implementation

#### Scenario: Performance gate fails
- **WHEN** native parity passes but the 8 ms completion/fallback gate fails
- **THEN** legacy remains the default and native evaluation is reported as opt-in/diagnostic only
