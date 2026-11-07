"""Проверки без сети: python -m unittest test_core"""
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import check_results
import grader
from cqa_score import critical_flag, final_score
from scene_facts import scene_facts

CL = grader.load_checklist()
C = CL["criteria"]
DEV = pathlib.Path(__file__).parent / "data" / "dev.json"
RECORDS = {r["conversation_id"]: r for r in json.loads(DEV.read_text(encoding="utf-8"))["data"]["records"]}


def answer(**over):
    """Заглушка ответа модели только для проверки кода вокруг неё."""
    vals = dict(contact="yes", discovery="na", identification="na", consultation="15", empathy="yes",
                dialogue="7", closure="yes", critical="no")
    vals.update(over)
    return json.dumps({"criteria": {k: {"rationale": "т", "evidence_turns": [1], "value": v, "confidence": "high"}
                                    for k, v in vals.items()}})


class Score(unittest.TestCase):
    def test_appendix_example(self):
        v = dict(contact=True, discovery=None, identification=None, consultation=15, empathy=True,
                 dialogue=7, closure=True, critical=False)
        self.assertEqual(final_score(C, v), 85.38)

    def test_half_up(self):
        v = dict(contact=True, discovery=False, identification=None, consultation=10, empathy=True,
                 dialogue=7, closure=True, critical=False)
        self.assertEqual(final_score(C, v), 63.13)

    def test_flag_does_not_zero(self):
        v = dict(contact=True, discovery=None, identification=None, consultation=15, empathy=True,
                 dialogue=7, closure=True, critical=True)
        self.assertEqual(final_score(C, v), 85.38)
        self.assertIs(critical_flag(C, v), True)

    def test_replaced_scale_criterion(self):
        cl = [dict(c) for c in C]
        for c in cl:
            if c["id"] == "consultation":
                c.update(scale=[0, 5, 10], weight=30)
        v = dict(contact=True, discovery=None, identification=None, consultation=10, empathy=True,
                 dialogue=7, closure=True, critical=False)
        self.assertEqual(final_score(cl, v), round((10 + 30 + 15 + 10.5 + 5) / 75 * 100, 2))


class Facts(unittest.TestCase):
    def test_call_tail(self):
        f = scene_facts(RECORDS["p4-ru-004"]["transcript"])
        self.assertEqual((f["last_operator"], f["after_end"]), (112, [115]))

    def test_hold(self):
        f = scene_facts(RECORDS["p4-ru-002"]["transcript"])
        self.assertEqual(f["hold"], [[18, 59]])
        self.assertEqual(f["last_operator"], 71)

    def test_first_operator_computed(self):
        self.assertEqual(scene_facts(RECORDS["p1-ru-003"]["transcript"])["first_operator"], 11)


class Grade(unittest.TestCase):
    def run_with(self, replies, cid="p1-ru-001", checklist=CL):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(grader, "RUNS", pathlib.Path(d)), \
                mock.patch.object(grader, "route", return_value=("gemini", "m")), \
                mock.patch.object(grader, "call_model", side_effect=[(r, {}) for r in replies]) as cm:
            return grader.grade_record(RECORDS[cid], checklist), cm

    def test_ok_and_score_from_code(self):
        r, _ = self.run_with([answer()])
        self.assertEqual(r["score"], {"final_score": 85.38, "critical_flag": False})

    def test_invalid_value_retried(self):
        r, cm = self.run_with([answer(contact="na"), answer()])
        self.assertEqual((r["status"], cm.call_count), ("ok", 2))

    def test_bad_json_gives_error_not_zero(self):
        r, cm = self.run_with(["нет"] * 4)
        self.assertEqual((r["status"], r["score"], r["criteria"]), ("error", None, []))

    def test_guard_reasks_once_and_keeps_model_value(self):
        # p1-ru-018: оператор закрыл чат без прощания
        r, cm = self.run_with([answer(closure="yes"), answer(closure="yes")], cid="p1-ru-018")
        self.assertEqual(cm.call_count, 2)
        self.assertIs({c["criterion_id"]: c["value"] for c in r["criteria"]}["closure"], True)
        self.assertTrue(r["_meta"]["review"])

    def test_no_operator_is_error(self):
        rec = {"conversation_id": "x", "channel": "agent chat", "language": "RU",
               "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "а"}]}
        with mock.patch.object(grader, "route", return_value=("gemini", "m")):
            self.assertEqual(grader.grade_record(rec, CL)["status"], "error")

    def test_prompt_follows_checklist(self):
        cl = json.loads(json.dumps(CL))
        cl["criteria"] = [c for c in cl["criteria"] if c["id"] != "closure"] + [
            {"id": "deadline", "section": "Новая", "title": "Срок", "rule": "Да, если назвал срок.", "type": "bool",
             "weight": 5, "na_allowed": False, "guidance": "", "guards": []}]
        rec = RECORDS["p1-ru-018"]
        p = grader.build_prompt(rec, cl, scene_facts(rec["transcript"]))
        self.assertNotIn("Смотри конец чата", p)
        self.assertIn("Да, если назвал срок.", p)
        self.assertIn("deadline", grader.build_schema(cl)["properties"]["criteria"]["properties"])

    def test_output_passes_checker(self):
        r, _ = self.run_with([answer()])
        with tempfile.TemporaryDirectory() as d, mock.patch.object(grader, "RUNS", pathlib.Path(d)):
            p = pathlib.Path(d) / "results.json"
            grader.write_results(p, [r])
            errs = check_results.check(p, {"p1-ru-001": len(RECORDS["p1-ru-001"]["transcript"])})
            self.assertEqual(errs, ["team_name"] if not json.loads(p.read_text(encoding="utf-8"))["team_name"] else [])
            self.assertNotIn("_meta", p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
