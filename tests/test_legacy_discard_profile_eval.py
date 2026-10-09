from collections import Counter

import pytest

from scripts.legacy_v2_discard_profile_eval import pair_assignment, production_profile, reaction_profile
from mj.legacy_eval import LegacyTwoPlyProfile


def test_pairs_balance_hero_and_dealer_independently():
    assert Counter(pair_assignment(i) for i in range(32)) == Counter(
        {(hero, dealer): 2 for hero in range(4) for dealer in range(4)})


def test_experiment_profile_preserves_production_and_rejects_silent_overrides():
    assert production_profile() == LegacyTwoPlyProfile.weighted_online()
    candidate = production_profile(speed_band_enabled=True,
                                   speed_band_min_ratio_by_shanten=[1, .95, .9, .85])
    assert candidate.speed_band_min_ratio_by_shanten == (1, .95, .9, .85)
    assert not candidate.big_hand_enabled
    assert candidate.fingerprint != production_profile().fingerprint
    with pytest.raises(ValueError, match="unsupported experiment fields"):
        production_profile(big_hand_enabled=True)


def test_reaction_tuning_does_not_leak_into_discard_profile():
    point = {"reaction": {"claim_min_gain_ratio": 1.25}}
    assert production_profile(**point) == production_profile()
    assert reaction_profile(**point).fingerprint != reaction_profile().fingerprint
    with pytest.raises(ValueError, match="unsupported reaction fields"):
        production_profile(reaction={"tempo_guard": False})
