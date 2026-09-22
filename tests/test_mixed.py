"""Offline bridge invariants; no native app or paid model requests."""

import pytest

from jev_ultrafast import mixed
from jev_ultrafast.browser import StalePage


@pytest.fixture
def observed():
    bindings = {
        "textedit": {"name": "文本编辑"},
        "browser": {"name": "浏览器"},
    }
    actions = [
        {
            "id": "cu:textedit/e1",
            "kind": "fill",
            "label": "正文",
            "app_key": "textedit",
            "surface": "cu:textedit",
            "channel": "CU",
            "app_name": "文本编辑",
            "value": "",
            "native": {},
        },
        {
            "id": "cu:browser/e1",
            "kind": "fill",
            "label": "关键词",
            "app_key": "browser",
            "surface": "cu:browser",
            "channel": "CU",
            "app_name": "浏览器",
            "value": "",
            "native": {},
        },
        {
            "id": "browser:browser/e1",
            "kind": "fill",
            "label": "关键词",
            "app_key": "browser",
            "surface": "browser:browser",
            "channel": "Browser Use",
            "app_name": "浏览器",
            "value": "",
            "native": {},
        },
    ]
    observation = {
        "actions": actions,
        "surfaces": {a["surface"]: {"title": a["app_name"], "text": "observed text"} for a in actions},
    }
    return observation, bindings


def answer(choice, keys):
    return {"choice": choice, "confidence": 1.0, "probabilities": {k: float(k == choice) for k in keys}}


def test_cu_compaction_keeps_every_target_and_full_observation(observed):
    observation, bindings = observed
    observation["actions"] = [a for a in observation["actions"] if a["channel"] == "CU"]
    observation["tool_availability"] = {"execution_mode": "computer_use"}
    for n, action in enumerate(observation["actions"]):
        action["element_index"] = str(n + 12)
        action["value"] = "长字段内容" * 500
        observation["surfaces"][action["surface"]]["text"] = f"[{n + 12}] {action['value']}"
    request, mapped = mixed.build_request(observation, bindings, [], [])
    candidates = request["questions"]["type_text_target"]["criteria"]
    assert set(candidates) == set(mapped["type_text_target"]) == {"1", "2"}
    for index, action in mapped["type_text_target"].items():
        assert f"AX [{action['element_index']}]" in candidates[index]
        assert action["value"] not in candidates[index]
        assert any(action["value"] in page["text"] for page in request["state"]["observations"])
    assert request["state"]["execution_rules"] == mixed.NATIVE_NEXT_ACTION + "\n" + mixed.POLICY


def stub(monkeypatch, answers):
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-not-a-key")
    monkeypatch.setattr(mixed, "post_json", lambda *_: {"model": "offline", "answers": answers})


def test_discovery_state_exposes_surfaces_before_any_page_is_observed():
    action = {"id": "auto:tab/inspect", "kind": "inspect", "label": "实验页", "role": "tab",
              "value": "http://localhost:8770/", "app_key": "tab", "app_name": "实验页",
              "channel": "Browser Use", "surface": "auto:tab"}
    body, mapped = mixed.build_request({"surfaces": {}, "actions": [action]}, {},
                                       [{"text": "只勾选包含归档，不搜索", "final": True}], [])
    assert body["state"]["observations"] == []
    target = body["state"]["available_targets"]["SWITCH_APP"]["1"]
    assert target["current_value"] == action["value"]
    assert target["role"] == "tab" and target["channel"] == "Browser Use"
    assert mapped["switch_app_target"]["1"] == action


@pytest.mark.parametrize("repaired", [True, False])
@pytest.mark.parametrize("prior_kind", ["click", "inspect", None])
def test_wrong_outcome_reconsiders_current_targets_once(monkeypatch, observed, repaired, prior_kind):
    observation, bindings = observed
    requests = []
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline")

    def post(_url, _key, body):
        requests.append(body)
        questions = body["questions"]
        if len(requests) == 1:
            return {"answers": {"operation": answer("LISTEN", questions["operation"]["criteria"]),
                                "outcome": answer("UNSATISFIED", questions["outcome"]["criteria"])}}
        assert "LISTEN" not in questions["operation"]["criteria"]
        assert body["state"]["outcome_review"]["status"] == "UNSATISFIED"
        result = {"operation": answer("TYPE_TEXT" if repaired else "BLOCKED",
                                      questions["operation"]["criteria"])}
        if repaired:
            result["type_text_target"] = answer("2", questions["type_text_target"]["criteria"])
        return {"answers": result}

    monkeypatch.setattr(mixed, "post_json", post)
    action, details = mixed.choose_mixed(observation, bindings, [{"text": "edit the field", "final": True}],
                                         [{"kind": prior_kind, "status": "returned"}] if prior_kind else [])
    assert len(requests) == 2
    assert action == (observation["actions"][1] if repaired else None)
    assert details["operation"] == ("TYPE_TEXT" if repaired else "BLOCKED")


@pytest.mark.parametrize("status", ["SATISFIED", "UNKNOWN", "CANCELLED"])
def test_outcome_review_does_not_force_action_without_contradiction(monkeypatch, observed, status):
    observation, bindings = observed
    requests = []
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline")

    def post(_url, _key, body):
        requests.append(body)
        return {"answers": {"operation": answer("LISTEN", body["questions"]["operation"]["criteria"]),
                            "outcome": answer(status, body["questions"]["outcome"]["criteria"])}}

    monkeypatch.setattr(mixed, "post_json", post)
    action, details = mixed.choose_mixed(observation, bindings, [], [{"kind": "click"}])
    assert action is None and len(requests) == 1
    assert details["outcome_review"]["status"] == status


@pytest.mark.parametrize("operation", ["LISTEN", "TYPE_TEXT"])
def test_interim_speech_still_selects_actions_but_defers_stop_review(monkeypatch, observed, operation):
    observation, bindings = observed
    requests = []
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline")

    def post(_url, _key, body):
        requests.append(body)
        assert "outcome" not in body["questions"]
        answers = {"operation": answer(operation, body["questions"]["operation"]["criteria"])}
        if operation == "TYPE_TEXT":
            answers["type_text_target"] = answer("1", body["questions"]["type_text_target"]["criteria"])
        return {"answers": answers}

    monkeypatch.setattr(mixed, "post_json", post)
    action, details = mixed.choose_mixed(observation, bindings, [{"text": "补充中", "final": False}],
                                         [{"kind": "click"}])
    assert len(requests) == 1
    assert action == (None if operation == "LISTEN" else observation["actions"][0])
    assert details["outcome_review"] is None


def test_listen_never_consumes_speculative_heads(monkeypatch, observed):
    o, b = observed
    stub(
        monkeypatch,
        {
            "operation": answer("LISTEN", ["TYPE_TEXT", "LISTEN"]),
            "type_text_target": {"choice": "arbitrary invalid output"},
        },
    )
    action, decision = mixed.choose_mixed(o, b, [], [])
    assert action is None and decision["operation"] == "LISTEN"


def test_target_selects_app_without_separate_app_question(monkeypatch, observed):
    o, b = observed
    stub(
        monkeypatch,
        {
            "operation": answer("TYPE_TEXT", ["TYPE_TEXT", "LISTEN"]),
            "type_text_target": answer("1", ["1", "2", "3"]),
        },
    )
    action, decision = mixed.choose_mixed(o, b, [], [])
    assert action["id"] == "cu:textedit/e1"
    assert set(decision["request"]["questions"]) == {"operation", "type_text_target"}


def test_browser_channel_is_selected_by_target(monkeypatch, observed):
    o, b = observed
    stub(
        monkeypatch,
        {
            "operation": answer("TYPE_TEXT", ["TYPE_TEXT", "LISTEN"]),
            "type_text_target": answer("3", ["1", "2", "3"]),
        },
    )
    action, _ = mixed.choose_mixed(o, b, [], [])
    assert action["channel"] == "Browser Use"


def test_unobserved_target_is_rejected(monkeypatch, observed):
    o, b = observed
    stub(
        monkeypatch,
        {
            "operation": answer("TYPE_TEXT", ["TYPE_TEXT", "LISTEN"]),
            "type_text_target": answer("99", ["99"]),
        },
    )
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        mixed.choose_mixed(o, b, [], [])


def test_candidate_overflow_is_not_truncated(observed):
    o, b = observed
    o["actions"] = [{**o["actions"][0], "id": str(i)} for i in range(256)]
    body, mapped = mixed.build_request(o, b, [], [])
    assert len(mapped["type_text_target"]) == 256
    questions = body["questions"]
    assert set(questions["type_text_target"]["criteria"]) == {"1", "2"}
    assert len(questions["type_text_target_group_1"]["criteria"]) == 255
    assert set(questions["type_text_target_group_2"]["criteria"]) == {"256"}
    assert all(len(q["criteria"]) <= 255 for q in questions.values())


def test_stale_rejection_is_logged_without_replay(observed):
    o, _ = observed
    desktop = mixed.MixedDesktop.__new__(mixed.MixedDesktop)
    desktop.events = []
    calls = []

    class Adapter:
        def act(self, *args, **kwargs):
            calls.append(args)
            raise StalePage("changed")

    desktop.adapters = {"cu:textedit": Adapter()}
    with pytest.raises(StalePage):
        desktop.act(o["actions"][0], o, text="value")
    assert len(calls) == 1
    assert desktop.events[0]["status"] == "rejected_stale"


def test_timeline_replaces_asr_hypothesis_and_retains_segment_order(monkeypatch):
    from benchmarks.cu100 import run

    now = [100.0]
    monkeypatch.setattr(run.time, "monotonic", lambda: now[0])
    timeline = run.Timeline(
        [
            {"text": "旧识别", "at_ms": 0, "segment": 0},
            {"text": "新识别", "at_ms": 100, "segment": 0},
            {"text": "补充", "at_ms": 200, "segment": 1},
        ]
    )
    try:
        now[0] += 0.3
        version, speech = timeline.snapshot()
        assert version == 3
        assert [s["text"] for s in speech] == ["新识别", "补充"]
        assert speech[0]["revision"] == 2
    finally:
        timeline.close()


def test_timeline_action_trigger_not_released_by_wall_clock(monkeypatch):
    from benchmarks.cu100 import run

    now = [100.0]
    monkeypatch.setattr(run.time, "monotonic", lambda: now[0])
    timeline = run.Timeline(
        [
            {"text": "先写", "at_ms": 0, "segment": 0},
            {"text": "现在改", "at_ms": 0, "segment": 1, "after_actions": 1},
        ]
    )
    try:
        now[0] += 10
        assert timeline.snapshot()[0] == 1
        timeline.executed()
        assert timeline.snapshot()[0] == 2
    finally:
        timeline.close()


def test_text_handoff_preserves_the_selected_operation_contract(monkeypatch):
    from jev_ultrafast import mixed
    from jev_ultrafast.actions import DESCRIPTIONS

    contexts = []
    monkeypatch.setattr(mixed, 'field_text', lambda context: (contexts.append(context) or 'value', {}))
    observation = {'surfaces': {}}
    for kind, operation in [('insert', 'INSERT_TEXT'), ('fill', 'TYPE_TEXT'), ('navigate', 'NAVIGATE')]:
        action = {'kind': kind, 'app_name': 'Observed App', 'label': 'Observed target', 'value': 'existing'}
        value, details = mixed.mixed_field_text(action, observation, {}, [{'text': 'request'}], [])
        assert value == 'value'
        assert contexts[-1]['operation'] == {'name': operation, 'description': DESCRIPTIONS[operation]}
        assert contexts[-1]['field']['value'] == 'existing'


def test_action_ignores_speculative_outcome_answer(monkeypatch, observed):
    observation, bindings = observed
    stub(monkeypatch, {'operation': answer('TYPE_TEXT', ['TYPE_TEXT', 'LISTEN']),
                      'type_text_target': answer('1', ['1', '2', '3']),
                      'outcome': {'malformed': 'unused speculative answer'}})
    action, detail = mixed.choose_mixed(observation, bindings, [{'text': 'write', 'final': True}],
                                        [{'kind': 'inspect'}])
    assert action == observation['actions'][0]
    assert detail['outcome_review'] is None
    assert 'outcome' in detail['request']['questions']


def test_shared_tool_constraint_is_visible_to_operation_and_text_models(monkeypatch, observed):
    observation, bindings = observed
    for action in observation['actions']:
        action['help'] = 'Shared capability limit'
    body, _ = mixed.build_request(observation, bindings, [], [])
    assert 'Shared capability limit' in body['questions']['operation']['criteria']['TYPE_TEXT']
    captured = []
    monkeypatch.setattr(mixed, 'field_text', lambda context: (captured.append(context) or 'value', {}))
    mixed.mixed_field_text(observation['actions'][0], observation, bindings, [], [])
    assert captured[0]['tool_constraints'] == 'Shared capability limit'
    observation['actions'][-1].pop('help')
    body, _ = mixed.build_request(observation, bindings, [], [])
    assert 'Shared capability limit' not in body['questions']['operation']['criteria']['TYPE_TEXT']


@pytest.mark.parametrize('recover', [True, False])
def test_invalid_consumed_decision_retries_once_without_changing_request(monkeypatch, observed, recover):
    observation, bindings = observed
    calls = []
    monkeypatch.setenv('TYPESAFE_API_KEY', 'offline')
    def post(_url, _key, body):
        calls.append(body)
        operation = answer('TYPE_TEXT', body['questions']['operation']['criteria'])
        if len(calls) == 1 or not recover:
            operation['choice'] = 'LISTEN'  # Declared choice disagrees with the distribution.
        return {'answers': {'operation': operation,
                            'type_text_target': answer('1', body['questions']['type_text_target']['criteria'])}}
    monkeypatch.setattr(mixed, 'post_json', post)
    if recover:
        action, _ = mixed.choose_mixed(observation, bindings, [], [])
        assert action == observation['actions'][0]
    else:
        with pytest.raises(mixed.InvalidChoice):
            mixed.choose_mixed(observation, bindings, [], [])
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.parametrize('action_answer,text_answer,expected', [
    ('EXECUTE', 'REWRITE', False), ('EXECUTE', 'KEEP', True), ('DISCARD', None, False),
])
def test_pending_text_is_checked_in_same_request_only_if_action_remains_valid(
        monkeypatch, observed, action_answer, text_answer, expected):
    observation, bindings = observed
    calls = []
    monkeypatch.setenv('TYPESAFE_API_KEY', 'offline')
    def post(_url, _key, body):
        calls.append(body)
        return {'answers': {'pending': answer(action_answer, body['questions']['pending']['criteria']),
                            'pending_text': answer(text_answer, body['questions']['pending_text']['criteria'])
                            if text_answer else {'malformed': 'unused'}}}
    monkeypatch.setattr(mixed, 'post_json', post)
    valid, details = mixed.pending_valid(observation['actions'][0], 'old value', observation, bindings, [], [])
    assert valid is expected and len(calls) == 1
    assert details['text_review'] == text_answer


def test_pending_without_text_does_not_request_or_consume_text_review(monkeypatch, observed):
    observation, bindings = observed
    monkeypatch.setenv('TYPESAFE_API_KEY', 'offline')
    def post(_url, _key, body):
        assert 'pending_text' not in body['questions']
        return {'answers': {'pending': answer('EXECUTE', body['questions']['pending']['criteria'])}}
    monkeypatch.setattr(mixed, 'post_json', post)
    valid, details = mixed.pending_valid(observation['actions'][0], None, observation, bindings, [], [])
    assert valid and details['text_review'] is None


@pytest.mark.parametrize("native", [False, True])
def test_grouped_target_consumes_only_selected_branch(monkeypatch, observed, native):
    o, b = observed
    o["actions"] = [{**o["actions"][0], "id": str(i)} for i in range(511)]
    if native:
        o["tool_availability"] = {"execution_mode": "computer_use"}
    body, _ = mixed.build_request(o, b, [], [])
    questions = body["questions"]
    stub(monkeypatch, {
        "operation": answer("TYPE_TEXT", ["TYPE_TEXT", "LISTEN"]),
        "type_text_target": answer("3", ["1", "2", "3"]),
        "type_text_target_group_1": {"choice": "invented"},
        "type_text_target_group_2": {},
        "type_text_target_group_3": answer("511", ["511"]),
    })
    action, details = mixed.choose_mixed(o, b, [], [])
    assert action["id"] == "510"
    assert details["selected_heads"] == ["operation", "type_text_target", "type_text_target_group_3"]
    assert all(len(q["criteria"]) <= 255 for q in questions.values())
    if native:
        assert questions["type_text_target"]["criteria"]["3"]["target_ids"] == ["511"]
        assert len(body["state"]["available_targets"]["TYPE_TEXT"]) == 511
    else:
        assert questions["type_text_target"]["criteria"]["3"] == questions["type_text_target_group_3"]["criteria"]


@pytest.mark.parametrize("invalid", ["group", "leaf"])
def test_grouped_target_rejects_invalid_consumed_answer(monkeypatch, observed, invalid):
    o, b = observed
    o["actions"] = [{**o["actions"][0], "id": str(i)} for i in range(256)]
    answers = {
        "operation": answer("TYPE_TEXT", ["TYPE_TEXT", "LISTEN"]),
        "type_text_target": answer("2", ["1", "2"]),
        "type_text_target_group_2": answer("256", ["256"]),
    }
    answers["type_text_target" if invalid == "group" else "type_text_target_group_2"] = {}
    stub(monkeypatch, answers)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        mixed.choose_mixed(o, b, [], [])
