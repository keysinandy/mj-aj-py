# Specification: optimization-evidence-contract

## ADDED Requirements

### Requirement: Promotion SHALL use attributable staged evidence

A Search-BC candidate SHALL be promoted only after the existing offline, hard-set, full paired and runtime gates meet their declared criteria. An offline selection report SHALL distinguish checkpoint eligibility from demonstrated improvement over a declared previous generation: `promoted=True` with no previous report MUST NOT be represented as inter-generation improvement. A required missing, malformed or unattributable report MUST block promotion; the pipeline MUST stop on a rejected gate before running more expensive stages. Each promotion decision SHALL retain run ID, commit, loaded kernel health, teacher/model/dataset/config identities, seeds, schedule, gate metrics and report provenance required by the staged-evaluation-gates contract. The only T0 training label is `legacyV2-offline`; the online shape-aware `legacyV2` profile is not a separate teacher identity.

#### Scenario: Eligible first-run checkpoint without predecessor
- **WHEN** offline selection finds an eligible checkpoint but has no declared previous-generation report
- **THEN** it MAY proceed to the remaining gates as first-run eligibility, but MUST NOT claim improvement over a previous generation

#### Scenario: Previous-generation comparison fails
- **WHEN** a declared predecessor exists and the offline improvement criterion is not met
- **THEN** promotion stops before paired evaluation, regardless of the checkpoint's eligibility flag

#### Scenario: Evidence cannot be attributed
- **WHEN** a required gate report lacks a required identity, seed, kernel-health field or metric
- **THEN** the candidate is not promoted and the missing evidence is reported, not inferred from filenames

### Requirement: Fast paired evidence SHALL be a rejection screen

The fast paired stage SHALL evaluate each declared matrix using the report's verdict, completed and required pair counts and uncertainty evidence, not process exit status alone. Supported regression SHALL reject the candidate. Missing or malformed reports and unmet required pairs SHALL be reported as insufficient evidence and MUST NOT count as a pass, even if the raw verdict says `superior`. `non_regression_ambiguous` MAY continue to the independent full paired gate for confirmation, but SHALL NOT constitute promotion or weaken the existing full paired/runtime release criteria. The decision SHALL record the matrix, verdict, pair counts, CI and reason, distinguishing `rejected`, `insufficient_evidence` and `continue_to_full`.

#### Scenario: Successful process reports regression
- **WHEN** the fast paired command exits zero but a declared matrix reports statistically supported `regression`
- **THEN** the candidate is rejected before full paired evaluation and the summary retains the regression reason

#### Scenario: Pair requirement is not met
- **WHEN** a fast report claims `superior` but `meets_required_pairs` is false or its required count is not reached
- **THEN** the result is insufficient evidence rather than a passed gate

#### Scenario: Ambiguous non-regression
- **WHEN** all declared fast matrices have sufficient pairs, no supported regression and a `non_regression_ambiguous` verdict
- **THEN** the candidate MAY enter full paired evaluation, but the fast result alone MUST NOT label it promoted

### Requirement: Gate summaries SHALL preserve report stage and rejection state

A summary SHALL normalize the pipeline's `offline_report.json` and `fast_paired.json` matrix map, the full CLI's `paired_report.json` `matrices` map, and historical flat `paired_<matrix>.json` reports without conflating fast and full evidence. It SHALL preserve the selected checkpoint, per-matrix stage, counts, verdict, provenance and early-exit reason. Missing, rejected and not-yet-run stages SHALL be distinct; fast evidence MUST NOT overwrite a full verdict. Ambiguous duplicate matrix identities SHALL fail visibly rather than choosing one silently.

#### Scenario: Full nested and legacy flat reports
- **WHEN** a run contains a full nested matrix report or a historical flat matrix file
- **THEN** the summary associates each matrix with its actual full stage and original verdict without substituting a fast verdict

#### Scenario: Fast rejection prevents full run
- **WHEN** fast paired rejects and no full report exists
- **THEN** the summary shows fast `rejected` and full `not_run`, not a generic pending or successful promotion

### Requirement: Resume SHALL reconcile skip and persisted work identities

For the same declared sources, configuration, loaded kernel and seed, the generator's pre-label skip identity SHALL equal the eventual persisted `SearchSample.work_id` for forced, unsupported and search-evidence paths. That identity SHALL include source group, context/history hashes, teacher seed and effective search fingerprint. A resumed run SHALL yield the same rows and final dataset fingerprint as uninterrupted generation independent of worker count or source-spec order; conflicting duplicate rows MUST fail rather than silently de-duplicate. Production generation SHALL require the actually loaded `rust-weighted-two-ply-v5` kernel with `weighted_kernel_compatible=True` and `degraded=False`, retain manifest provenance and keep v3-degraded and v5 shard cohorts separate. An explicit degraded override MAY be used only for smoke/parity work, not promotion evidence.

#### Scenario: Resuming a partially written dataset
- **WHEN** generation resumes from persisted rows with otherwise identical declared inputs
- **THEN** completed work is skipped exactly once, and merged row identities and dataset fingerprint equal an uninterrupted run

#### Scenario: Resume mismatch is not reproducible
- **WHEN** a previous count mismatch cannot be reproduced under a recorded runtime, seed and test order
- **THEN** its root cause remains unconfirmed; no identity rewrite or claim of repair is justified until a failing trace distinguishes trajectory drift from skip/persisted-key mismatch

#### Scenario: Degraded kernel cohort
- **WHEN** the loaded kernel is v3 or reports `degraded=True`
- **THEN** its results MUST NOT be combined with v5 production shards or used as v5 promotion evidence

### Requirement: Pending Shape Guard deltas SHALL preserve the canonical admission contract

Before either pending Shape Guard MODIFIED requirement is archived, its projected replacement SHALL preserve the canonical default standing-shape predicate: strict lexicographic improvement of `standing_shape_signature[2:6]` at the first differing class, with equality or a worse first differing class rejected. It SHALL retain primary/guard `admitted_by`, legacy fallback, standing-shape quality/version and admission/rejection audit fields while incorporating intended speed-band additions. Strict syntax validation alone SHALL NOT authorize an archive that changes this semantic contract. This requirement does not widen online guard admission or freeze a new baseline.

#### Scenario: Equal signature or earlier-class regression
- **WHEN** a candidate has an equal signature, or a later taatsu-class improves while the first differing class is worse
- **THEN** it is not admitted by the default standing-shape guard, including after either pending delta is archived; an earlier-class tie followed by a strictly better first differing class remains eligible

#### Scenario: Pending replacement omits audit fields
- **WHEN** a MODIFIED delta passes strict validation but its projected requirement drops `admitted_by` or standing-shape audit fields
- **THEN** archive is blocked until the projected specification preserves those fields

### Requirement: Online X-round persistence SHALL be a separate behavioral release

A proposed online X-round counter SHALL survive repeated `Mirror.build_game()` calls within the same authoritative round and seat, reset on round transition, and not leak across games or seats; offline `Game` accounting SHALL retain its prior behavior. It MUST NOT be activated merely by accepting this specification. Activation SHALL require mirror-path legal-action checks, v5 non-degraded paired settlement-score and latency/window-loss evidence against the existing online profile, and a recorded decision in `PROGRESS.md` with corresponding tests. Y/Z semantics and the Piao pass cap SHALL remain separate decisions.

#### Scenario: Reconstructed game within a round
- **WHEN** consecutive online decisions rebuild `Game` for the same round and seat
- **THEN** an enabled X counter preserves its rounds without borrowing counts from another round, seat or game

#### Scenario: No comparable release evidence
- **WHEN** mirror-path legality or comparable paired score and timing/window evidence is absent
- **THEN** X remains disabled online; accepting this specification does not claim a behavior fix
