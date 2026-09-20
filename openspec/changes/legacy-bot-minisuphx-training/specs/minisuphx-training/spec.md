# Specification: minisuphx-training

## ADDED Requirements

### Requirement: v1 SHALL use a hybrid policy with learned ordinary discard only

The v1 runtime/training policy SHALL route HU, KONG, CHOW, PONG, PASS and configured safeguards through the frozen legacy decision layer. The learned policy SHALL control only ordinary discard decisions.

#### Scenario: hero can HU
- **WHEN** a legal HU is available and the legacy safeguard selects HU
- **THEN** the learned discard policy is not queried for final action selection and HU is executed

#### Scenario: ordinary discard
- **WHEN** the legacy gate classifies the current hero decision as an ordinary discard
- **THEN** the learned policy selects among the legal discard actions using the same action encoding and legal mask

### Requirement: BC and RL SHALL share one model/value contract

BC and RL SHALL use compatible backbone, policy head and value head structures. The v1 normalized round-score value contract SHALL be `clip(score/24,-4,4)/4`, and both BC target and RL terminal reward SHALL use that scale.

#### Scenario: entering RL from BC-v1
- **WHEN** the learner initializes from BC-v1
- **THEN** backbone, policy head and value head are loaded exactly according to the model manifest, with no silently randomized replacement critic

#### Scenario: old reward-scale shard
- **WHEN** a rollout shard declares a different value contract such as an unnormalized `[-4,4]` scale
- **THEN** merge/training rejects the shard rather than mixing it into the normalized run

### Requirement: BC data SHALL be streamed and reproducibly partitioned

BC training SHALL consume immutable shards through a streaming dataset/DataLoader abstraction. Train/validation/final-test source domains SHALL be frozen in the campaign manifest, and final-test sources MUST NOT be used for training, DAgger collection, PPO rollout or hyperparameter selection.

#### Scenario: 30k-game dataset exceeds comfortable RAM
- **WHEN** the full BC corpus is larger than available memory
- **THEN** training iterates shards/batches without concatenating the full training corpus into one resident array

### Requirement: DAgger SHALL preserve legacy as teacher identity

DAgger MAY execute trajectories with a mixture of legacy and learned discard policies, but ordinary-discard labels SHALL continue to come from the frozen legacy teacher. Teacher version, executor version and disagreement SHALL be recorded.

#### Scenario: BC executes an unfamiliar state
- **WHEN** BC chooses the trajectory action at a state not commonly reached by legacy
- **THEN** the sample label still records the legacy teacher action for that state and the BC/legacy disagreement is preserved

### Requirement: BC-v1 SHALL be an immutable long-lived anchor

The BC-v1 checkpoint and manifest used as the RL prior SHALL be frozen. Later RL checkpoints MUST NOT overwrite or relabel the anchor identity.

#### Scenario: Gen2 becomes champion
- **WHEN** an RL checkpoint is promoted
- **THEN** BC prior KL still references the original BC-v1 fingerprint unless a later OpenSpec explicitly replaces the anchor

### Requirement: RL timesteps SHALL count exposed discard decisions

The primary training step counter SHALL count learned ordinary-discard decisions, not internal environment transitions automatically executed by legacy/opponent logic.

#### Scenario: ten internal actions occur before hero discards
- **WHEN** the environment auto-advances ten non-learned actions and then exposes one ordinary discard
- **THEN** the RL discard-decision counter increments by one

### Requirement: PPO SHALL retain an annealed BC prior

The PPO learner SHALL support a versioned schedule for KL regularization against BC-v1. The schedule SHALL be part of the run profile and persisted on resume.

#### Scenario: resumed run
- **WHEN** training resumes at 350k discard decisions
- **THEN** the learner restores the correct KL coefficient state for that progress rather than restarting the early-training coefficient

### Requirement: Exploration SHALL use normalized target entropy control

The learner SHALL report and control policy entropy relative to the number of legal actions. A dynamic coefficient SHALL move normalized entropy toward a configured target band, with persisted controller state and safety bounds.

#### Scenario: state has only two legal discards
- **WHEN** entropy is evaluated for that state
- **THEN** it is normalized against the legal-action count instead of being compared directly to a fixed raw-entropy target designed for a larger action set

### Requirement: Potential shaping SHALL anneal out before final strength claims

Potential-based shaping MAY be used in early RL, but the schedule SHALL reduce it to zero for later training/evaluation. Promotion strength SHALL be measured from true terminal outcomes.

#### Scenario: candidate has high shaped return
- **WHEN** shaped training reward improves but paired terminal score does not
- **THEN** the candidate is not promoted on the basis of shaped return

### Requirement: Opponent league SHALL retain stable anchors

Opponent pools MAY add BC and historical RL policies, but v1/v2 training SHALL retain configured minimum legacy and BC-v1 anchor proportions. Opponent reaction and discard components SHALL be separately versioned.

#### Scenario: league generation changes
- **WHEN** a new historical RL opponent is added or pool weights change
- **THEN** a new opponent-pool fingerprint/generation identity is produced

### Requirement: Oracle Guiding SHALL only become default after Champion-v1

Oracle-enabled training SHALL start from a full-gate Champion-v1. The final deployable Oracle-derived candidate SHALL pass evaluation with oracle features fully disabled.

#### Scenario: oracle-trained model is strong only with hidden inputs
- **WHEN** its public-only full gate regresses
- **THEN** it is not promoted and Champion-v1 remains the rollback/default target

### Requirement: Reaction RL SHALL unlock by explicit capability stages

Learning control beyond discard SHALL be introduced through separately versioned scopes, beginning with PONG/PASS and then CHOW/PASS. Each scope SHALL have its own legacy anchor, hard set and promotion gate.

#### Scenario: discard RL succeeds
- **WHEN** Champion-v1 is promoted
- **THEN** that success alone does not authorize PPO to control every remaining action type
