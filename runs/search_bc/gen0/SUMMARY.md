# Reduced Gen0 summary

## pi0

- selected: `heuristic:shape-v1` (lowest_reference_regret_with_paired_score_tiebreak)

| candidate | mean regret | p95 | top1 | p95 ms |
| --- | ---: | ---: | ---: | ---: |
| heuristic:legacy | 1.7690 | 8.2112 | 0.5223 | 0.5467 |
| heuristic:shape-v1 | 1.6619 | 8.2112 | 0.5282 | 18.3503 |
| heuristic:shape-v2 | 1.7446 | 8.2112 | 0.5344 | 49.3810 |

## dataset0

- rows: 11189
- fingerprint: `ef0c9449f3e0a684bda77faa`
- coverage passed: False
- coverage missing: ['piao', 'four-white-boards', 'seven-pairs', 'luxury-seven-pairs', 'zhuada-quan', 'wall-tail']

## training (pi1)

- usable rows: 10349
- epochs: `[{"augmentation": "suit", "epoch": 1, "loss": 1.8892303207367789, "samples": 10349, "target_mode": "visit"}, {"augmentation": "suit", "epoch": 2, "loss": 1.794321487728369, "samples": 10349, "target_mode": "visit"}, {"augmentation": "suit", "epoch": 3, "loss": 1.7447984081061523, "samples": 10349, "target_mode": "visit"}]`
- skipped: `{"features": 0, "missing_q": 0, "missing_value": 0, "status": 0, "zero_weight": 840}`
- dataset fingerprint: `ef0c9449f3e0a684bda77faa`

## offline selection

- selected: `runs/search_bc/gen0/epoch_003.pt` (promoted=True, reference_regret_improved)

| checkpoint | mean regret | p95 | catastrophic | top1 | KL | p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| runs/search_bc/gen0/epoch_001.pt | 2.3117 | 9.9279 | 0.0000 | 0.3957 | 1.5843 | 21.6073 |
| runs/search_bc/gen0/epoch_002.pt | 2.3234 | 11.6485 | 0.0052 | 0.4227 | 1.5062 | 17.6896 |
| runs/search_bc/gen0/epoch_003.pt | 2.0792 | 8.1860 | 0.0050 | 0.4279 | 1.4754 | 25.5970 |

## paired games

| matrix | pairs | mean Δ | CI95 | verdict |
| --- | ---: | ---: | --- | --- |
| previous | pending | pending | pending | pending |
| shape_v1 | pending | pending | pending | pending |
| shape_v2 | pending | pending | pending | pending |

## pipeline tail

```
epoch_001.pt
epoch_002.pt
epoch_003.pt
paired_previous.json
paired_shape_v1.json
paired_shape_v2.json
selection_report.json
training_manifest.json
```
