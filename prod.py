"""Контрольный час одной командой.

Оценка без отправки:  python prod.py --group <group_id>
Оценка и отправка:    python prod.py --group <group_id> --push
Только отправка:      python prod.py --push-only
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

import httpx
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).parent
TIMEOUT_API = 120
RETRIES = 2


def step(start, text):
    print(f"[{(time.time() - start) / 60:5.1f} мин] {text}", flush=True)


def fetch(group, path, ids=None):
    url = os.environ.get("ORGANIZER_API_URL", "")
    if not url:
        sys.exit("В .env не задан ORGANIZER_API_URL")
    r = httpx.get(url, params={"group_id": group}, timeout=TIMEOUT_API)
    r.raise_for_status()
    d = r.json()
    if not d.get("isSuccess"):
        sys.exit(f"API организаторов вернул ошибку: {d.get('error')}")
    if ids:  # только для репетиции
        d["data"]["records"] = [x for x in d["data"]["records"] if x["conversation_id"] in ids]
        d["data"]["total_records"] = len(d["data"]["records"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d["data"]["records"]


def run(*args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env, text=True, encoding="utf-8",
                          capture_output=True)


def failed(out):
    doc = json.loads(pathlib.Path(out).read_text(encoding="utf-8"))
    return [r["conversation_id"] for r in doc["results"] if r["status"] != "ok"]


def push(out, expected):
    rel = pathlib.Path(out).resolve().relative_to(ROOT.resolve()).as_posix()
    subprocess.run(["git", "add", rel], cwd=ROOT, check=True)
    msg = "Add control set results\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
    subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
    subprocess.run(["git", "push"], cwd=ROOT, check=True)
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True).stdout
    repo = remote.strip().removesuffix(".git").split("github.com/")[1]
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    raw = f"https://raw.githubusercontent.com/{repo}/{branch}/{rel}"
    for _ in range(6):
        try:
            n = len(httpx.get(raw, params={"t": time.time()}, timeout=30).json()["results"])
            if n == expected:
                print(f"Проверено на GitHub: {n} записей. {raw}")
                return
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        time.sleep(20)
    print(f"ВНИМАНИЕ: не удалось подтвердить файл на GitHub, откройте вручную: {raw}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--group")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--push-only", action="store_true")
    ap.add_argument("--data", default="data/prod.json")
    ap.add_argument("--out", default="results/prod/results.json")
    ap.add_argument("--expect", type=int, default=10, help="сколько записей должно быть")
    ap.add_argument("--only-ids", help="репетиция: взять только эти ID")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    start = time.time()

    if a.push_only:
        push(a.out, a.expect)
        return
    if not a.group:
        sys.exit("Укажите --group")

    records = fetch(a.group, ROOT / a.data, set(a.only_ids.split(",")) if a.only_ids else None)
    step(start, f"Получено записей: {len(records)}")
    if len(records) != a.expect:
        sys.exit(f"Ожидалось записей: {a.expect}. Проверьте group_id; при другом числе укажите --expect.")

    r = run("grader.py", "--input", a.data, "--out", a.out, "--workers", str(a.workers))
    print(r.stdout[-1500:], r.stderr[-800:])
    for i in range(RETRIES):
        bad = failed(ROOT / a.out)
        if not bad:
            break
        step(start, f"Повтор {i + 1}, не оценены: {','.join(bad)}")
        r = run("grader.py", "--input", a.data, "--out", a.out, "--workers", "1", "--resume")
        print(r.stdout[-800:], r.stderr[-400:])
    step(start, "Оценка завершена")

    c = run("check_results.py", a.out, a.data)
    print(c.stdout)
    if c.returncode != 0:
        sys.exit("Проверка формата не пройдена. Файл не отправлен.")
    bad = failed(ROOT / a.out)
    if bad:
        print("Записаны со status=error:", ", ".join(bad))
    step(start, "Формат проверен")
    if a.push:
        push(a.out, a.expect)
        step(start, "Отправлено")
    else:
        print("Отправка не выполнялась. Чтобы отправить: python prod.py --push-only")


if __name__ == "__main__":
    main()
