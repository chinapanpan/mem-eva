from memeva.common import pack_context, ntokens
from memeva.judge import judge_prompt, parse_yes, parse_coverage
from memeva.stats import wilson, mcnemar_exact, pct
from memeva.backends.agentcore import _chunks
from memeva.datasets import _lme_date


def test_pack_respects_budget():
    blocks = ["a " * 300, "b " * 300, "c " * 300]
    ctx, kept = pack_context(blocks, 500)
    assert ntokens(ctx) <= 500 and kept[0] == 0


def test_judge_templates():
    q = {"category": "update", "question": "q", "answer": "a"}
    assert "updated answer" in judge_prompt(q, "r")
    q = {"category": "fact", "question": "q", "answer": "x", "abstention": True}
    assert "unanswerable" in judge_prompt(q, "r")
    assert parse_yes("Yes.") and not parse_yes("no")


def test_coverage_parse():
    assert parse_coverage('blah {"covered": [1, 0]}', 3) == [1, 0, 0]
    assert parse_coverage("garbage", 2) == [0, 0]


def test_stats():
    lo, hi = wilson(13, 14)
    assert 0.6 < lo < 0.93 < hi <= 1
    assert mcnemar_exact(0, 0) == 1.0 and mcnemar_exact(8, 0) < 0.01
    assert pct([1, 2, 3, 4], 0.5) == 2.5


def test_chunks():
    msgs = [{"role": "user", "content": "hi"}] * 45
    assert [len(c) for c in _chunks(msgs)] == [20, 20, 5]


def test_lme_date():
    assert _lme_date("2023/05/20 (Sat) 02:21") == "2023-05-20T02:21:00.000Z"


def test_parse_verdicts():
    assert parse_yes("Rubric points ok...\nVERDICT: yes")
    assert not parse_yes("reasoning yes yes\nVERDICT: no")
    assert parse_yes("Yes. The response recalls")
    assert not parse_yes("")
    assert parse_yes("The rubric is met, so the answer is yes")
