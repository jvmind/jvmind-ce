from react_agent.summarizer import maybe_summarize


class _Mem:
    def __init__(self):
        self.msgs = []
        self.facts = {}

    def get_messages(self, sid):
        return list(self.msgs)

    def set_context_fact(self, sid, key, value):
        self.facts[(sid, key)] = value


def test_skips_below_threshold():
    m = _Mem()
    m.msgs = [{"role": "user", "content": "x"}] * 10

    def _llm(msgs):
        raise AssertionError("should not be called")

    assert maybe_summarize("s1", m, _llm) is False
    assert m.facts == {}


def test_summarizes_above_threshold_and_stores_in_context_fact():
    m = _Mem()
    m.msgs = [
        msg
        for i in range(25)
        for msg in (
            {"role": "user", "content": f"q{i}"},
            {"role": "assistant", "content": f"a{i}"},
        )
    ]

    calls = []

    def _llm(messages):
        calls.append(messages)
        return "summary text"

    ok = maybe_summarize("s2", m, _llm, threshold=40, keep_last=20)
    assert ok is True
    assert m.facts.get(("s2", "summary")) == "summary text"
    assert "q14" in calls[0][-1]["content"]
    assert "q15" not in calls[0][-1]["content"]


def test_truncates_summary_to_max_chars():
    m = _Mem()
    m.msgs = [{"role": "user", "content": f"u{i}"} for i in range(50)]

    def _llm(messages):
        return "X" * 5000

    ok = maybe_summarize("s3", m, _llm, max_chars=200)
    assert ok is True
    assert len(m.facts[("s3", "summary")]) <= 200


def test_swallows_llm_failure():
    m = _Mem()
    m.msgs = [{"role": "user", "content": f"u{i}"} for i in range(50)]

    def _llm(messages):
        raise RuntimeError("boom")

    assert maybe_summarize("s4", m, _llm) is False
