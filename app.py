"""Сервер CQA: чек-листы, диалоги, оценка, апелляции. Запуск: python app.py"""
import json
import os
import pathlib
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import grader
from cqa_score import critical_flag, final_score, share

ROOT = pathlib.Path(__file__).parent
load_dotenv(ROOT / ".env")
DB = ROOT / "data" / "cqa.db"
app = FastAPI(title="CQA")
JOBS = {}


def db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    DB.parent.mkdir(exist_ok=True)
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS checklists(id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS conversations(cid TEXT PRIMARY KEY, dataset TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS evaluations(id INTEGER PRIMARY KEY AUTOINCREMENT, cid TEXT, checklist_id INTEGER,
            status TEXT, body TEXT, created REAL, run_log TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS appeals(id INTEGER PRIMARY KEY AUTOINCREMENT, evaluation_id INTEGER, criterion_id TEXT,
            comment TEXT, status TEXT, decision TEXT, new_value TEXT, reason TEXT, created REAL, decided REAL);
        """)
        if not c.execute("SELECT 1 FROM checklists").fetchone():
            c.execute("INSERT INTO checklists(body,created) VALUES(?,?)",
                      (json.dumps(grader.load_checklist(), ensure_ascii=False), time.time()))
        elif c.execute("SELECT COUNT(*) FROM checklists").fetchone()[0] == 1:
            # пока пользователь не сохранял свои версии, база следует за исходным чек-листом
            c.execute("UPDATE checklists SET body=?", (json.dumps(grader.load_checklist(), ensure_ascii=False),))
        dev = ROOT / "data" / "dev.json"
        if dev.exists() and not c.execute("SELECT 1 FROM conversations").fetchone():
            store_records(c, json.loads(dev.read_text(encoding="utf-8"))["data"]["records"], "DEV")


def store_records(c, records, dataset):
    for r in records:
        c.execute("INSERT OR REPLACE INTO conversations VALUES(?,?,?)",
                  (r["conversation_id"], dataset, json.dumps(r, ensure_ascii=False)))


def import_cli_runs():
    """Оценки, сделанные через командную строку, тоже видны в интерфейсе."""
    with db() as c:
        cl_id = c.execute("SELECT MIN(id) FROM checklists").fetchone()[0]
        for name in ("dev", "prod"):  # экспериментальные прогоны в интерфейс не попадают
            state = grader.load_state(ROOT / "results" / name / "results.json")
            for r in state.values():
                log = (r.get("_meta") or {}).get("run_log")
                path = ROOT / "runs" / log if log else None
                if log and r["status"] == "ok" and path.exists():
                    c.execute("INSERT OR IGNORE INTO evaluations(cid,checklist_id,status,body,created,run_log) "
                              "VALUES(?,?,?,?,?,?)", (r["conversation_id"], cl_id, r["status"],
                                                      json.dumps(r, ensure_ascii=False), path.stat().st_mtime, log))


def get_checklist(c, cid=None):
    row = (c.execute("SELECT * FROM checklists WHERE id=?", (cid,)).fetchone() if cid
           else c.execute("SELECT * FROM checklists ORDER BY id DESC LIMIT 1").fetchone())
    if not row:
        raise HTTPException(404, "Чек-лист не найден")
    return {"id": row["id"], "created": row["created"], **json.loads(row["body"])}


def view_evaluation(c, row):
    """Исходная оценка, решения по апелляциям и финальный результат."""
    ev = json.loads(row["body"])
    cl = get_checklist(c, row["checklist_id"])
    appeals = [dict(a) for a in c.execute("SELECT * FROM appeals WHERE evaluation_id=? ORDER BY id", (row["id"],))]
    out = {"id": row["id"], "cid": row["cid"], "status": row["status"], "checklist_id": row["checklist_id"],
           "checklist_version": cl.get("version"), "created": row["created"], "appeals": appeals,
           "error": ev.get("error"), "meta": ev.get("_meta") or {}}
    if row["status"] != "ok":
        return out
    original = {x["criterion_id"]: x["value"] for x in ev["criteria"]}
    final = dict(original)
    for a in appeals:
        if a["status"] == "decided":
            final[a["criterion_id"]] = json.loads(a["new_value"])
    known = [x for x in cl["criteria"] if x["id"] in original]
    applicable = sum(x["weight"] for x in known if x["type"] != "flag" and final[x["id"]] is not None)
    rows, by_id = [], {x["criterion_id"]: x for x in ev["criteria"]}
    for x in known:
        v = final[x["id"]]
        scored = x["type"] != "flag" and v is not None and applicable
        rows.append({"id": x["id"], "title": x["title"], "section": x.get("section"), "type": x["type"],
                     "scale": x.get("scale"), "weight": x["weight"], "na_allowed": x.get("na_allowed"),
                     "original_value": original[x["id"]], "value": v,
                     "effective_weight": round(x["weight"] / applicable * 100, 2) if scored else 0,
                     "points": round(float(share(x, v)) * x["weight"] / applicable * 100, 2) if scored else 0,
                     "rationale": by_id[x["id"]]["rationale"], "evidence_turns": by_id[x["id"]]["evidence_turns"],
                     "low_confidence": x["id"] in out["meta"].get("low_confidence", [])})
    sections = {}
    for x in known:
        if x["type"] != "flag" and x.get("section"):
            sections[x["section"]] = final_score(known, final, section=x["section"])
    out.update(criteria=rows, sections=sections,
               original_score=final_score(known, original), final_score=final_score(known, final),
               original_flag=critical_flag(known, original), critical_flag=critical_flag(known, final))
    return out


def latest_eval(c, cid, checklist_id=None):
    q, p = "SELECT * FROM evaluations WHERE cid=?", [cid]
    if checklist_id:
        q += " AND checklist_id=?"
        p.append(checklist_id)
    return c.execute(q + " ORDER BY created DESC, id DESC LIMIT 1", p).fetchone()


# ---------- Чек-лист ----------
class ChecklistIn(BaseModel):
    title: str = "Чек-лист"
    criteria: list[dict]


@app.get("/api/checklists")
def checklists():
    with db() as c:
        rows = c.execute("SELECT * FROM checklists ORDER BY id DESC").fetchall()
        return [{"id": r["id"], "created": r["created"], **json.loads(r["body"])} for r in rows]


@app.post("/api/checklists")
def save_checklist(b: ChecklistIn):
    ids = [x.get("id", "").strip() for x in b.criteria]
    if not ids or "" in ids or len(set(ids)) != len(ids):
        raise HTTPException(400, "У каждого критерия нужен свой непустой код")
    if not any(x.get("type") != "flag" and float(x.get("weight") or 0) > 0 for x in b.criteria):
        raise HTTPException(400, "Нужен хотя бы один критерий с весом")
    with db() as c:
        prev = {x["id"]: x for x in get_checklist(c)["criteria"]}
        crit = []
        for x in b.criteria:
            keep = x.pop("keep_guidance", False)
            if x.get("type") not in ("bool", "scale", "flag"):
                raise HTTPException(400, f"{x['id']}: неизвестный тип")
            if x["type"] == "scale":
                scale = sorted({int(s) for s in x.get("scale") or []})
                if len(scale) < 2 or max(scale) <= 0:
                    raise HTTPException(400, f"{x['id']}: шкале нужны минимум две ступени")
                x["scale"] = scale
            else:
                x.pop("scale", None)
            x["weight"] = 0 if x["type"] == "flag" else float(x.get("weight") or 0)
            old = prev.get(x["id"])
            if (old is None or old.get("rule") != x.get("rule")) and not keep:
                # старая подсказка не должна спорить с новым вопросом
                x.update(guidance="", guards=[])
                x.pop("modes", None)
                x.pop("mode", None)
            crit.append(x)
        n = c.execute("SELECT COUNT(*) FROM checklists").fetchone()[0] + 1
        body = {"version": f"v{n}", "title": b.title, "criteria": crit}
        cur = c.execute("INSERT INTO checklists(body,created) VALUES(?,?)",
                        (json.dumps(body, ensure_ascii=False), time.time()))
        return {"id": cur.lastrowid, **body}


# ---------- Диалоги ----------
class ImportIn(BaseModel):
    group_id: str | None = None
    payload: dict | list | None = None
    dataset: str = "Загрузка"


@app.post("/api/import")
def import_records(b: ImportIn):
    if b.group_id:
        base = os.environ.get("ORGANIZER_API_URL", "")
        if not base:
            raise HTTPException(400, "В .env не задан ORGANIZER_API_URL")
        try:
            r = httpx.get(base, params={"group_id": b.group_id}, timeout=120)
            d = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(502, f"API организаторов: {e}")
        if not d.get("isSuccess"):
            raise HTTPException(502, f"API организаторов: {d.get('error')}")
        records, dataset = d["data"]["records"], b.group_id
    else:
        p = b.payload
        if isinstance(p, dict):
            p = p.get("data", {}).get("records") if "data" in p else p.get("records", [p])
        records, dataset = p or [], b.dataset
    for r in records:
        if not all(k in r for k in ("conversation_id", "transcript")) or not isinstance(r["transcript"], list):
            raise HTTPException(400, "Нужны поля conversation_id и transcript (массив реплик)")
        r.setdefault("channel", "agent chat")
        r.setdefault("language", "RU")
        r.setdefault("audio_url", None)
    with db() as c:
        store_records(c, records, dataset)
    return {"imported": len(records), "ids": [r["conversation_id"] for r in records], "dataset": dataset}


@app.get("/api/conversations")
def conversations():
    import_cli_runs()
    with db() as c:
        out = []
        for row in c.execute("SELECT * FROM conversations ORDER BY rowid").fetchall():
            r = json.loads(row["body"])
            item = {"cid": row["cid"], "dataset": row["dataset"], "channel": r["channel"],
                    "language": r["language"], "turns": len(r["transcript"])}
            e = latest_eval(c, row["cid"])
            if e:
                v = view_evaluation(c, e)
                item.update(status=v["status"], score=v.get("final_score"), original_score=v.get("original_score"),
                            flag=v.get("critical_flag"), checklist=v["checklist_version"], error=v.get("error"),
                            review=bool(v["meta"].get("review") or v["meta"].get("low_confidence")),
                            open_appeals=sum(a["status"] == "open" for a in v["appeals"]))
            out.append(item)
        return out


@app.get("/api/conversations/{cid}")
def conversation(cid: str):
    import_cli_runs()
    with db() as c:
        row = c.execute("SELECT * FROM conversations WHERE cid=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Диалог не найден")
        r = json.loads(row["body"])
        e = latest_eval(c, cid)
        return {"cid": cid, "dataset": row["dataset"], "channel": r["channel"], "language": r["language"],
                "transcript": r["transcript"], "has_audio": bool(r.get("audio_url")),
                "evaluation": view_evaluation(c, e) if e else None}


@app.get("/api/audio/{cid}")
def audio(cid: str):
    """Подписанная ссылка на запись остаётся на сервере и не попадает в код страницы."""
    with db() as c:
        row = c.execute("SELECT body FROM conversations WHERE cid=?", (cid,)).fetchone()
    url = json.loads(row["body"]).get("audio_url") if row else None
    if not url:
        raise HTTPException(404, "Записи нет")
    return RedirectResponse(url)


@app.get("/api/runs/{name}")
def run_log(name: str):
    path = (ROOT / "runs" / name).resolve()
    if path.parent != (ROOT / "runs").resolve() or not path.exists():
        raise HTTPException(404, "Журнал не найден")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- Оценка ----------
class EvaluateIn(BaseModel):
    cids: list[str]
    checklist_id: int | None = None


def run_job(job_id, cids, checklist):
    job = JOBS[job_id]

    def one(cid):
        job["items"][cid] = {"status": "running"}
        with db() as c:
            rec = json.loads(c.execute("SELECT body FROM conversations WHERE cid=?", (cid,)).fetchone()["body"])
        res = grader.grade_record(rec, checklist)
        with db() as c:
            c.execute("INSERT INTO evaluations(cid,checklist_id,status,body,created,run_log) VALUES(?,?,?,?,?,?)",
                      (cid, checklist["id"], res["status"], json.dumps(res, ensure_ascii=False), time.time(),
                       (res.get("_meta") or {}).get("run_log")))
        job["items"][cid] = {"status": res["status"], "error": res.get("error"),
                             "score": (res.get("score") or {}).get("final_score")}

    with ThreadPoolExecutor(max_workers=int(os.environ.get("WORKERS", "1"))) as ex:
        list(ex.map(one, cids))
    job["done"] = True


@app.post("/api/evaluate")
def evaluate(b: EvaluateIn):
    with db() as c:
        checklist = get_checklist(c, b.checklist_id)
        known = {r[0] for r in c.execute("SELECT cid FROM conversations")}
    missing = [x for x in b.cids if x not in known]
    if not b.cids or missing:
        raise HTTPException(400, f"Нет таких диалогов: {missing}" if missing else "Не выбраны диалоги")
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"id": job_id, "checklist": checklist["version"], "done": False,
                    "items": {x: {"status": "queued"} for x in b.cids}}
    threading.Thread(target=run_job, args=(job_id, b.cids, checklist), daemon=True).start()
    return JOBS[job_id]


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "Задача не найдена")
    return JOBS[job_id]


# ---------- Апелляции ----------
class AppealIn(BaseModel):
    evaluation_id: int
    criterion_id: str
    comment: str


class DecisionIn(BaseModel):
    decision: str  # confirm | change
    value: bool | int | None = None
    reason: str


@app.post("/api/appeals")
def create_appeal(b: AppealIn):
    if not b.comment.strip():
        raise HTTPException(400, "Добавьте комментарий")
    with db() as c:
        if c.execute("SELECT 1 FROM appeals WHERE evaluation_id=? AND criterion_id=? AND status='open'",
                     (b.evaluation_id, b.criterion_id)).fetchone():
            raise HTTPException(400, "По этому критерию уже есть открытая апелляция")
        cur = c.execute("INSERT INTO appeals(evaluation_id,criterion_id,comment,status,created) "
                        "VALUES(?,?,?,'open',?)", (b.evaluation_id, b.criterion_id, b.comment.strip(), time.time()))
        return {"id": cur.lastrowid}


@app.get("/api/appeals")
def list_appeals():
    with db() as c:
        out = []
        for a in c.execute("SELECT * FROM appeals ORDER BY status='open' DESC, id DESC").fetchall():
            e = c.execute("SELECT * FROM evaluations WHERE id=?", (a["evaluation_id"],)).fetchone()
            v = view_evaluation(c, e)
            crit = next((x for x in v["criteria"] if x["id"] == a["criterion_id"]), None)
            rec = json.loads(c.execute("SELECT body FROM conversations WHERE cid=?", (e["cid"],)).fetchone()["body"])
            turns = (crit or {}).get("evidence_turns", [])
            lo, hi = (max(min(turns) - 2, 1), max(turns) + 1) if turns else (1, 0)
            fragment = [{"n": n, **rec["transcript"][n - 1]} for n in range(lo, min(hi, len(rec["transcript"])) + 1)]
            out.append({**dict(a), "cid": e["cid"], "criterion": crit, "fragment": fragment,
                        "original_score": v["original_score"], "final_score": v["final_score"]})
        return out


@app.post("/api/appeals/{appeal_id}/decision")
def decide(appeal_id: int, b: DecisionIn):
    if not b.reason.strip():
        raise HTTPException(400, "Укажите причину решения")
    with db() as c:
        a = c.execute("SELECT * FROM appeals WHERE id=?", (appeal_id,)).fetchone()
        if not a or a["status"] != "open":
            raise HTTPException(400, "Апелляция не найдена или уже решена")
        e = c.execute("SELECT * FROM evaluations WHERE id=?", (a["evaluation_id"],)).fetchone()
        cl = get_checklist(c, e["checklist_id"])
        crit = next(x for x in cl["criteria"] if x["id"] == a["criterion_id"])
        current = next(x["value"] for x in json.loads(e["body"])["criteria"] if x["criterion_id"] == a["criterion_id"])
        value = current
        if b.decision == "change":
            value = b.value
            ok = ((value is None and crit.get("na_allowed")) or
                  (crit["type"] == "scale" and type(value) is int and value in crit["scale"]) or
                  (crit["type"] != "scale" and type(value) is bool))
            if not ok:
                raise HTTPException(400, "Значение не разрешено этим критерием")
        elif b.decision != "confirm":
            raise HTTPException(400, "Решение: confirm или change")
        c.execute("UPDATE appeals SET status='decided',decision=?,new_value=?,reason=?,decided=? WHERE id=?",
                  (b.decision, json.dumps(value), b.reason.strip(), time.time(), appeal_id))
        return view_evaluation(c, e)


# ---------- Сводка ----------
@app.get("/api/stats")
def stats():
    """Где чаще теряются баллы и журнал грубых нарушений."""
    with db() as c:
        crit, violations = {}, []
        for row in c.execute("SELECT cid FROM conversations").fetchall():
            e = latest_eval(c, row["cid"])
            if not e or e["status"] != "ok":
                continue
            v = view_evaluation(c, e)
            for x in v["criteria"]:
                if x["type"] == "flag":
                    if x["value"] is True:
                        violations.append({"cid": row["cid"], "rationale": x["rationale"],
                                           "turns": x["evidence_turns"]})
                    continue
                s = crit.setdefault(x["id"], {"id": x["id"], "title": x["title"], "lost": 0, "na": 0, "n": 0})
                s["n"] += 1
                if x["value"] is None:
                    s["na"] += 1
                else:
                    s["lost"] += round(x["effective_weight"] - x["points"], 2)
        return {"criteria": sorted(crit.values(), key=lambda s: -s["lost"]), "violations": violations}


app.mount("/web", StaticFiles(directory=ROOT / "web"), name="web")


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


init_db()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
