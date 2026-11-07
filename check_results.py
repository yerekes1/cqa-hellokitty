"""Проверка results.json по формату приложения. Запускать перед каждым push:
python check_results.py results/prod/results.json data/prod.json [--expect 10]"""
import json
import sys

from cqa_score import final_score

CRITERIA = [
    {"id": "contact", "type": "bool", "weight": 10},
    {"id": "discovery", "type": "bool", "weight": 15},
    {"id": "identification", "type": "bool", "weight": 20},
    {"id": "consultation", "type": "scale", "weight": 20, "scale": [0, 5, 10, 15, 20]},
    {"id": "empathy", "type": "bool", "weight": 15},
    {"id": "dialogue", "type": "scale", "weight": 15, "scale": [0, 4, 7, 10]},
    {"id": "closure", "type": "bool", "weight": 5},
    {"id": "critical", "type": "flag", "weight": 0},
]
ALLOWED = {
    "contact": [True, False],
    "discovery": [True, False, None],
    "identification": [True, False, None],
    "consultation": [0, 5, 10, 15, 20, None],
    "empathy": [True, False],
    "dialogue": [0, 4, 7, 10],
    "closure": [True, False, None],
    "critical": [True, False],
}


def same(a, b):  # в Python True == 1, поэтому сверяем и тип
    return type(a) is type(b) and a == b


def check(path, turns):
    """turns: {conversation_id: число реплик в ответе API}."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    errs = []
    if doc.get("schema_version") != "1.0":
        errs.append("schema_version")
    if not doc.get("team_name"):
        errs.append("team_name")
    m = doc.get("model", {})
    if m.get("mode") not in ("local", "external_api", "hybrid") or not m.get("name"):
        errs.append("model")
    ids = [r["conversation_id"] for r in doc["results"]]
    if sorted(ids) != sorted(turns):
        errs.append("ID не совпадают с API или есть повторы")
    for r in doc["results"]:
        cid = r["conversation_id"]
        if r["status"] == "error":
            if r["criteria"] != [] or r["score"] is not None or not r.get("error"):
                errs.append(f"{cid}: формат ошибки")
            continue
        got = [c["criterion_id"] for c in r["criteria"]]
        if sorted(got) != sorted(ALLOWED):
            errs.append(f"{cid}: нужны все 8 критериев по одному разу")
            continue
        vals = {c["criterion_id"]: c["value"] for c in r["criteria"]}
        for k, v in vals.items():
            if not any(same(v, a) for a in ALLOWED[k]):
                errs.append(f"{cid}.{k}: недопустимое значение {v!r}")
        for c in r["criteria"]:
            if not c.get("rationale"):
                errs.append(f"{cid}.{c['criterion_id']}: нет rationale")
            if not c.get("evidence_turns"):
                errs.append(f"{cid}.{c['criterion_id']}: нет evidence_turns")
            for n in c.get("evidence_turns", []):
                if not 1 <= n <= turns.get(cid, 0):
                    errs.append(f"{cid}.{c['criterion_id']}: нет реплики {n}")
        if r["score"]["critical_flag"] is not vals["critical"]:
            errs.append(f"{cid}: critical_flag")
        if r["score"]["final_score"] != final_score(CRITERIA, vals):
            errs.append(f"{cid}: неверный final_score")
    return errs


if __name__ == "__main__":
    with open(sys.argv[2], encoding="utf-8") as fh:
        recs = json.load(fh)["data"]["records"]
    errs = check(sys.argv[1], {r["conversation_id"]: len(r["transcript"]) for r in recs})
    sys.stdout.reconfigure(encoding="utf-8")
    print("OK" if not errs else "\n".join(errs))
    sys.exit(1 if errs else 0)
