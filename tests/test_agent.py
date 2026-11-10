"""The agent on its own: the 102 evaluation conversations, and the LLM template's plumbing."""
import json
import os
import sys

from conftest import ROOT

sys.path.insert(0, os.path.join(ROOT, "evaluation"))
sys.path.insert(0, os.path.join(ROOT, "evaluation", "eval"))
from harness.simulator import run_conversation  # noqa: E402
from evaluate import aggregate, score_one  # noqa: E402

from chat_service.agent.llm_agent import LLMAgent  # noqa: E402
from chat_service.agent.rules_agent import RulesAgent  # noqa: E402


def test_rules_agent_passes_the_evaluation_dataset():
    gt = json.load(open(os.path.join(ROOT, "evaluation", "ground_truth", "ground_truth.json"), encoding="utf-8"))
    rows = []
    for sid in sorted(gt):
        run = run_conversation(RulesAgent(), os.path.join(ROOT, "evaluation", "scenarios", sid),
                               os.path.join(ROOT, "evaluation", "policy"))
        rows.append(score_one(gt[sid], run))
    m = aggregate(rows)
    assert m["full_decision_acc"] == 1.0 and m["safety"] == 1.0 and m["ipe_score"] == 1.0


def test_state_survives_a_save_and_load():
    a = RulesAgent(); a.start({"conversation_id": "c_x", "today": "2026-10-28"})
    a.respond({"text": ""}); a.respond({"text": "Lotus Tea House"})
    b = RulesAgent(); b.set_state(json.loads(json.dumps(a.get_state())))
    assert b.respond({"text": "Pune"})["asking"] == "business_type"


class FakeModelAgent(LLMAgent):
    replies = {"we're a small café near FC Road": '{"intent": "answer", "value": "Restaurant"}',
               "hmm is this really necessary??": '{"intent": "why", "value": ""}'}

    def call_model(self, prompt):
        msg = prompt.split("<message>")[1].split("</message>")[0]
        return self.replies.get(msg, '{"intent": "answer", "value": "' + msg + '"}')


def test_llm_template_turns_free_text_into_answers():
    a = FakeModelAgent(); a.start({"conversation_id": "c_y", "today": "2026-10-28"})
    a.respond({"text": ""}); a.respond({"text": "Lotus Tea House"}); a.respond({"text": "Pune"})
    r = a.respond({"text": "hmm is this really necessary??"})
    assert "ONB-POL-001 §3" in r["text"] and r["asking"] == "business_type"
    a.respond({"text": "we're a small café near FC Road"})
    assert a.get_state()["answers"]["business_type"] == "restaurant"
