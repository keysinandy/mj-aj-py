import json
import tempfile
import unittest
from pathlib import Path

from mj.replay_debugger.adapters import (
    EvidenceBundle, import_local_jsonl, import_sources, import_trace,
    normalize_event, read_jsonl,
)
from mj.replay_debugger.compiler import compile_bundle
from mj.replay_debugger.diagnostics import classify_request, detect_missing_transitions
from mj.replay_debugger.fixtures import jsonl, three_source_fixture
from mj.replay_debugger.model import (
    Boundary, EvidenceStrength, EventType, KnowledgeState, KnownValue,
    LocalStep, LocalStepType, RawRecord, ReplaySession, Request, SourceRole,
    stable_id,
)
from mj.replay_debugger.export import render_html, safe_json, write_export
from mj.replay_debugger.state import ReferenceReducer, project_visibility
from mj.replay_debugger.timeline import (TimelineIndex, deduplicate_events,
                                         state_at, verify_checkpoints)
from mj.replay_debugger.trace import ReplayTraceWriter, scrub_credentials


class ReplayDebuggerTests(unittest.TestCase):
    def test_knowledge_and_stable_identity(self):
        self.assertEqual(KnownValue.known([]).status, KnowledgeState.KNOWN)
        self.assertEqual(KnownValue.unknown().status, KnowledgeState.UNKNOWN)
        self.assertEqual(KnownValue.hidden().status, KnowledgeState.HIDDEN)
        self.assertEqual(KnownValue.not_applicable().status, KnowledgeState.NOT_APPLICABLE)
        a = RawRecord.make(SourceRole.STATE_RESPONSE, {"seq": 1}, source_path="/a/log.jsonl")
        b = RawRecord.make(SourceRole.STATE_RESPONSE, {"seq": 1}, source_path="/moved/log.jsonl")
        self.assertEqual(a.record_id, b.record_id)
        self.assertEqual(stable_id("x", 1), stable_id("x", 1))

    def test_normalize_aliases_and_sse_watermark(self):
        event = normalize_event({"type": "gang", "seat": 1, "tile": "5p",
                                 "data": {"kind": "bu"}, "seq": 8},
                                source=SourceRole.SERVER_TIMELINE)
        self.assertEqual(event.type, EventType.KAN_ADDED)
        self.assertEqual(event.tile, "5b")
        self.assertEqual(normalize_event({"type": "unknown-protocol"},
                                         source=SourceRole.SERVER_TIMELINE).type,
                         EventType.UNKNOWN)

    def test_normalize_all_kan_types(self):
        self.assertEqual(normalize_event(
            {"type": "gang_ming", "seat": 0, "tile": "1m"},
            source=SourceRole.SERVER_TIMELINE).type, EventType.KAN_OPEN)
        self.assertEqual(normalize_event(
            {"type": "gang", "kind": "an", "seat": 0, "tile": "2p"},
            source=SourceRole.SERVER_TIMELINE).type, EventType.KAN_CLOSED)
        self.assertEqual(normalize_event(
            {"type": "gang", "kind": "bu", "seat": 0, "tile": "3s"},
            source=SourceRole.SERVER_TIMELINE).type, EventType.KAN_ADDED)

    def test_sse_watermark_has_no_mahjong_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sse.jsonl"
            path.write_text(jsonl([
                {"type": "meta", "gid": "g", "seat": 0},
                {"type": "sse_frame", "seq": 184,
                 "payload": {"seq": 184}, "accepted": True},
            ]), encoding="utf-8")
            bundle = import_local_jsonl(path)
            self.assertEqual(bundle.events[0].type, EventType.SSE_RECEIVED)
            self.assertFalse(any(e.type in {EventType.DRAW, EventType.DISCARD,
                                            EventType.CHI, EventType.PON,
                                            EventType.KAN_OPEN,
                                            EventType.KAN_CLOSED,
                                            EventType.KAN_ADDED}
                                for e in bundle.events))

    def test_duplicate_server_event_keeps_refs(self):
        first = normalize_event({"type": "discard", "seq": 4,
                                 "seat": 1, "tile": "5m"},
                                source=SourceRole.SERVER_TIMELINE,
                                raw_ref="raw-a", game_id="g")
        second = normalize_event({"type": "discard", "seq": 4,
                                  "seat": 1, "tile": "5m"},
                                 source=SourceRole.SERVER_TIMELINE,
                                 raw_ref="raw-b", game_id="g")
        events, conflicts = deduplicate_events([first, second])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].raw_refs, ["raw-a", "raw-b"])
        self.assertFalse(conflicts)

    def test_identity_conflict_is_not_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.jsonl"
            server = Path(tmp) / "server.json"
            local.write_text(jsonl([{"type": "meta", "gid": "local"}]),
                             encoding="utf-8")
            server.write_text(json.dumps({"gid": "other", "events": [
                {"seq": 1, "type": "discard", "seat": 0, "tile": "1w"}
            ]}), encoding="utf-8")
            bundle = import_sources(local, server_path=server)
            session = compile_bundle(bundle)
            self.assertTrue(any(q["kind"] == "IDENTITY_CONFLICT"
                                for q in session.quality))
            self.assertFalse(session.frames)

    def test_malformed_jsonl_keeps_later_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "game.jsonl"
            path.write_text('{"type":"meta","gid":"g"}\n{broken\n{"type":"end"}\n', encoding="utf-8")
            records, raws, quality = read_jsonl(path, SourceRole.STATE_RESPONSE)
            self.assertEqual([r["type"] for r in records], ["meta", "end"])
            self.assertEqual(len(raws), 3)
            self.assertEqual(quality[0]["kind"], "MALFORMED_JSONL")
            bundle = import_local_jsonl(path)
            self.assertEqual(len(bundle.local_steps), 1)
            self.assertTrue(bundle.quality)

    def test_three_source_compile_and_offline_export(self):
        fixture = three_source_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "fixture.jsonl"
            server = root / "server.json"
            trace = root / "trace.jsonl"
            local.write_text(jsonl(fixture["local"]), encoding="utf-8")
            server.write_text(json.dumps(fixture["server"], ensure_ascii=False), encoding="utf-8")
            trace.write_text(jsonl(fixture["trace"]), encoding="utf-8")
            from mj.replay_debugger import compile_replay
            session = compile_replay(local, server_path=server, trace_path=trace)
            self.assertEqual(session.game_id, "fixture-game")
            self.assertTrue(session.source_coverage["serverIndependent"])
            self.assertTrue(session.source_coverage["sse"])
            self.assertTrue(session.source_coverage["trace"])
            self.assertTrue(session.frames)
            self.assertIn("rawRecords", session.as_dict())
            out = root / "out"
            json_path, html_path = write_export(session, out, input_paths=[local, server, trace])
            self.assertTrue(json_path.is_file())
            self.assertTrue(html_path.is_file())
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("Mahjong Replay Debugger", html)
            self.assertNotIn("http://", html)
            self.assertNotIn("https://", html)
            with self.assertRaises(FileExistsError):
                write_export(session, out, input_paths=[local])

    def test_trace_is_bounded_scrubbed_and_importable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            writer = ReplayTraceWriter(path, gid="g", session_id="s", queue_size=1)
            for i in range(20):
                writer.capture("transition", {"i": i, "token": "secret"}, seq_no=i)
            writer.close()
            self.assertTrue(path.is_file())
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('"token": "secret"', text)
            self.assertIn("footer", text)
            bundle = import_trace(path)
            self.assertFalse(any(x.get("kind") == "INCOMPLETE_TRACE" for x in bundle.quality))
            self.assertTrue(bundle.local_steps)
            self.assertEqual(scrub_credentials({"Authorization": "x"})["Authorization"], "[REDACTED]")

    def test_seq_zero_and_private_gap_are_not_events(self):
        bundle = EvidenceBundle(game_id="g")
        bundle.events.extend([
            normalize_event({"type": "discard", "seq": 1, "seat": 0,
                             "tile": "1w"}, source=SourceRole.SERVER_TIMELINE,
                            game_id="g"),
            normalize_event({"type": "discard", "seq": 3, "seat": 1,
                             "tile": "2w"}, source=SourceRole.SERVER_TIMELINE,
                            game_id="g"),
        ])
        session = compile_bundle(bundle)
        self.assertEqual([frame.seq_no for frame in session.frames], [1, 3])
        self.assertNotIn(0, [frame.seq_no for frame in session.frames])

    def test_request_lifecycle_has_three_boundaries(self):
        fixture = three_source_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local.jsonl"
            path.write_text(jsonl(fixture["local"]), encoding="utf-8")
            session = compile_bundle(import_local_jsonl(path))
            self.assertEqual(len(session.requests), 1)
            self.assertEqual(
                [step.type for step in session.local_steps[:5]],
                [LocalStepType.STATE_RESPONSE, LocalStepType.SSE_RECEIVED,
                 LocalStepType.STATE_REQUEST, LocalStepType.STATE_RESPONSE,
                 LocalStepType.STATE_MERGE])
            request = session.requests[0]
            self.assertEqual(request.logical_request_id, "req-1")
            self.assertIsNotNone(request.request_to_response_diff)
            self.assertIsNotNone(request.effective_merge_diff)
            self.assertIsNotNone(request.expected_observed_diff)

    def test_server_world_is_independent(self):
        fixture = three_source_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.jsonl"
            server = Path(tmp) / "server.json"
            local.write_text(jsonl(fixture["local"]), encoding="utf-8")
            server.write_text(json.dumps(fixture["server"], ensure_ascii=False),
                             encoding="utf-8")
            session = compile_bundle(import_sources(local, server_path=server))
            server_state = session.frames[-1].server_after
            self.assertEqual(server_state["players"][0]["hand"]["status"], "KNOWN")
            self.assertEqual(server_state["players"][0]["hand"]["source"],
                             "SERVER_TIMELINE")

    def test_watermark_does_not_apply_pon(self):
        fixture = three_source_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.jsonl"
            local.write_text(jsonl(fixture["local"]), encoding="utf-8")
            session = compile_bundle(import_local_jsonl(local))
            sse = next(step for step in session.local_steps
                       if step.type == LocalStepType.SSE_RECEIVED)
            before = session.states["steps"][sse.step_id]["expectedAfter"]
            self.assertFalse(any(m["type"] == "PON"
                                 for p in before["players"] for m in p["melds"]))

    def test_legacy_observed_is_derived(self):
        fixture = three_source_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.jsonl"
            local.write_text(jsonl(fixture["local"]), encoding="utf-8")
            session = compile_bundle(import_local_jsonl(local))
            self.assertEqual(session.states["observedFinal"]["evidence"], "DERIVED")

    def test_called_river_and_added_kan_lineage(self):
        hands = [["5b", "5b", "5b"], [], [], []]
        reducer = ReferenceReducer(game_id="g", round_no=1)
        reducer.initialize_hands(hands)
        events = [
            normalize_event({"type": "discard", "seq": 1, "seat": 1,
                             "tile": "5b"}, source=SourceRole.SERVER_TIMELINE,
                            game_id="g", round_no=1),
            normalize_event({"type": "pon", "seq": 2, "seat": 0,
                             "fromSeat": 1, "tile": "5b"},
                            source=SourceRole.SERVER_TIMELINE,
                            game_id="g", round_no=1),
            normalize_event({"type": "gang", "kind": "bu", "seq": 3,
                             "seat": 0, "tile": "5b"},
                            source=SourceRole.SERVER_TIMELINE,
                            game_id="g", round_no=1),
        ]
        for event in events:
            reducer.apply_event(event)
        state = reducer.as_state()
        river = state.players[1].river[0]
        meld = state.players[0].melds[0]
        self.assertTrue(river.called)
        self.assertEqual(river.called_by, 0)
        self.assertEqual(river.call_type, "PON")
        self.assertEqual(meld.type, "KAN_ADDED")
        self.assertEqual(meld.source_discard_event_id, events[0].event_id)
        self.assertEqual(meld.parent_meld_id, meld.meld_id)

    def test_hidden_hand_keeps_known_count(self):
        reducer = ReferenceReducer(game_id="g", round_no=1)
        reducer.apply_snapshot({"hand_counts": [13, 12, 13, 13]},
                               source=SourceRole.STATE_RESPONSE)
        player = reducer.as_state().players[1]
        self.assertEqual(player.hand.status, KnowledgeState.HIDDEN)
        self.assertEqual(player.hand_count.value, 12)
        self.assertEqual(player.hand_count.status, KnowledgeState.KNOWN)

    def test_request_diff_boundaries(self):
        fixture = three_source_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.jsonl"
            server = Path(tmp) / "server.json"
            local.write_text(jsonl(fixture["local"]), encoding="utf-8")
            server.write_text(json.dumps(fixture["server"], ensure_ascii=False),
                             encoding="utf-8")
            session = compile_bundle(import_sources(local, server_path=server))
            request = session.requests[0]
            self.assertIsInstance(request.request_to_response_diff.as_dict(), dict)
            self.assertIn("comparedFields", request.effective_merge_diff.as_dict())
            self.assertIn("unknownFields", request.expected_observed_diff.as_dict())

    def test_request_classification_priority(self):
        request = Request("request-1", logical_request_id="logical-1")
        classify_request(request, local_transition_recovered=True,
                         reconnect_recovered=True, complete_comparison=True,
                         effective_change=True)
        self.assertEqual(request.classification.value,
                         "RECOVERY_CAUSED_BY_LOCAL_TRANSITION")
        self.assertEqual(request.avoidability.value, "AVOIDABLE")

    def test_missing_transition_requires_completion(self):
        event = normalize_event({"type": "pon", "seq": 2, "seat": 0,
                                 "tile": "5b"}, source=SourceRole.SERVER_TIMELINE,
                                game_id="g")
        expected_reducer = ReferenceReducer(game_id="g")
        expected_reducer.initialize_hands([["5b", "5b"], [], [], []])
        expected_reducer.apply_event(event)
        observed = ReferenceReducer(game_id="g").as_state().as_dict()
        complete = LocalStep(stable_id("step", "complete"), 0,
                             LocalStepType.PROCESSING_COMPLETE,
                             related_seq_no=2, related_event_id=event.event_id,
                             payload={"executionComplete": True},
                             outcome="COMPLETED")
        incomplete = LocalStep(stable_id("step", "incomplete"), 1,
                               LocalStepType.LOCAL_TRANSITION,
                               related_seq_no=2, related_event_id=event.event_id)
        states = {complete.step_id: {"expectedAfter": expected_reducer.as_state().as_dict(),
                                     "observedAfter": observed},
                  incomplete.step_id: {"expectedAfter": expected_reducer.as_state().as_dict(),
                                       "observedAfter": observed}}
        self.assertEqual(len(detect_missing_transitions(
            [event], [incomplete], states, game_id="g")), 0)
        diagnostics = detect_missing_transitions([event], [complete], states,
                                                  game_id="g")
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].type, "MISSING_LOCAL_TRANSITION")

    def test_visibility_projection_is_explicit(self):
        reducer = ReferenceReducer(game_id="g")
        reducer.initialize_hands([["1w"], ["2w"], ["3w"], ["4w"]])
        player_view = project_visibility(reducer.as_state(), "PLAYER_VIEW", 2)
        self.assertEqual(player_view["players"][2]["hand"]["status"], "KNOWN")
        self.assertEqual(player_view["players"][0]["hand"]["status"], "HIDDEN")
        omniscient = project_visibility(reducer.as_state(), "OMNISCIENT", 2)
        self.assertEqual(omniscient["players"][0]["hand"]["status"], "KNOWN")

    def test_checkpoint_seek_is_detached_and_consistent(self):
        fixture = three_source_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.jsonl"
            local.write_text(jsonl(fixture["local"]), encoding="utf-8")
            session = compile_bundle(import_local_jsonl(local))
            self.assertTrue(verify_checkpoints(session)["passed"])
            step = session.local_steps[0]
            cursor = TimelineIndex(session.frames, session.local_steps,
                                    session.game_id).cursor_for_step(step.step_id)
            value = state_at(session, cursor, world="expected")
            value["round"]["roundNo"] = 999
            self.assertNotEqual(session.states["steps"][step.step_id]
                                ["expectedAfter"]["round"]["roundNo"], 999)

    def test_embedded_untrusted_text_is_not_script_markup(self):
        raw = RawRecord.make(SourceRole.LOCAL_DERIVED,
                             {"message": "</script><img src=x onerror=alert(1)>"},
                             source_path="/tmp/evidence.jsonl")
        session = ReplaySession("session", game_id="g", raw_records=[raw])
        html = render_html(session)
        self.assertNotIn("</script><img", html)
        self.assertIn("React + shadcn/ui", html)

    def test_policy_diagnostics_are_visible_without_web_bundle(self):
        session = ReplaySession(
            "session", game_id="g",
            metadata={"policyDiagnostics": {
                "schema": "policy-v3-debugger-diagnostics-v1",
                "count": 1,
                "rows": [{"actual_action": -1, "suggested_action": -2,
                           "belief_marginals": {"seat_1": [0.5]},
                           "search_regret": 1.25}],
                "oracle": False,
            }})
        html = render_html(session)
        self.assertIn("Policy-v3 suggestions / belief", html)
        self.assertIn("suggested_action", html)
        self.assertIn("search_regret", html)
        self.assertIn("belief_marginals", html)

    def test_cursor_rejects_missing_seq_without_mutation(self):
        bundle = EvidenceBundle(game_id="g")
        event = normalize_event({"type": "tile_discarded", "seat": 0,
                                 "tile": "1w", "seq": 4}, source=SourceRole.SERVER_TIMELINE)
        bundle.events.append(event)
        session = compile_bundle(bundle)
        index = TimelineIndex(session.frames, session.local_steps, "g")
        cursor = session.cursor
        new_cursor, message = index.jump_seq(cursor, 999)
        self.assertEqual(new_cursor.as_dict(), cursor.as_dict())
        self.assertIn("unavailable", message)


if __name__ == "__main__":
    unittest.main()
