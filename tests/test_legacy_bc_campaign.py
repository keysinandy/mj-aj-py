from scripts.generate_legacy_bc_campaign import (
    build_campaign_config,
    campaign_fingerprint,
    _dataset_fingerprint_payload,
)


def test_legacy_campaign_freezes_match_and_seed_contract():
    config = build_campaign_config(
        campaign_id="test-campaign", games=30_000, per_shard=25,
        git_commit="abc123")

    assert config["seed_domain"] == {
        "train_seed_lo": 0,
        "train_seed_hi": 29_999,
        "games": 30_000,
    }
    assert config["seed_domains"] == {
        "train": [0, 29_999],
        "validation": [1_000_000, 1_001_999],
        "final_test": [2_000_000, 2_003_999],
    }
    assert config["match_rules"] == {
        "schema": "legacy-match-v1",
        "rounds": 8,
        "default_consecutive_deals": 3,
        "dealer_continues_on": ["dealer_win", "draw"],
        "winner_becomes_dealer": True,
        "settlement": "dealer-x8",
    }
    assert config["n_shards"] == 1_200
    assert campaign_fingerprint(config) == campaign_fingerprint(config)


def test_dataset_fingerprint_includes_match_and_shard_identity():
    config = build_campaign_config(
        campaign_id="test-campaign", games=4, per_shard=2,
        git_commit="abc123")
    manifest = dict(config)
    manifest.update({
        "campaign_fingerprint": campaign_fingerprint(config),
        "shards": [
            {"path": "shard_00000.npz", "sha256": "a", "samples": 10},
            {"path": "shard_00001.npz", "sha256": "b", "samples": 11},
        ],
    })
    first = _dataset_fingerprint_payload(manifest, config["evaluator"])
    manifest["match_rules"] = dict(manifest["match_rules"], rounds=1)
    second = _dataset_fingerprint_payload(manifest, config["evaluator"])
    assert first != second
