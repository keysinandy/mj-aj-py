"""replay 校验器测试:synth 合成流对账 + 污染检测(离线)。"""

import unittest

from mj.replay import replay_doc, replay_round
from mj.platform.synth import synth_game
from mj.platform.proto import EV_PASS, EV_DISCARDED, EV_ROUND_ENDED


def _doc(res):
    return {
        "blocks": [{"start_hands": res["start_hands"],
                    "events": res["events"]}],
        "rounds": [{"dealer": res["game"].dealer,
                    "draw": r["draw"], "fan": r["fan"],
                    "detail": r["detail"], "scores": r["scores"]}
                   for r in res["rounds"]],
    }


class TestReplay(unittest.TestCase):
    def test_synth_streams_clean(self):
        n_actions = n_settle = 0
        for seed in range(30):
            ycbk = seed % 3 == 0
            res = synth_game(seed, you_cai_bi_kao=ycbk)
            reps = replay_doc(_doc(res), you_cai_bi_kao=ycbk)
            for r in reps:
                self.assertEqual(r["illegal"], [], f"seed={seed}: {r['illegal']}")
                self.assertGreater(r["actions_checked"], 0)
                n_actions += r["actions_checked"]
                if r["engine_result"]:
                    n_settle += 1
        self.assertGreater(n_settle, 20)

    def test_corrupted_discard_detected(self):
        res = synth_game(3)
        events = [dict(e) for e in res["events"]]
        # 改一个弃牌为非法牌(不在该座手牌中)
        for i, e in enumerate(events):
            if e["type"] == EV_DISCARDED:
                bad = dict(e)
                bad["tile"] = "东" if e["tile"] != "东" else "南"
                events[i] = bad
                break
        rep = replay_round(res["start_hands"], events,
                           res["game"].dealer)
        self.assertTrue(rep["illegal"], "污染流应被检出")

    def test_missing_pass_events_tolerated(self):
        res = synth_game(7)
        events = [e for e in res["events"] if e["type"] != EV_PASS]
        rep = replay_round(res["start_hands"], events,
                           res["game"].dealer)
        self.assertEqual(rep["illegal"], [])
        self.assertGreater(rep["auto_pass"], 0)

    def test_settle_mismatch_detected(self):
        res = synth_game(11)
        doc = _doc(res)
        if doc["rounds"][0]["fan"]:
            doc["rounds"][0]["fan"] += 1
            reps = replay_doc(doc)
            self.assertTrue(any(r["illegal"] for r in reps),
                            "倍率污染应被检出")


if __name__ == "__main__":
    unittest.main()
