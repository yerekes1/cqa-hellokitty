// Обычный JS без сборки: адрес сервера в одной настройке, вся работа через REST,
// ID диалога в адресе страницы. Так интерфейс проще перенести в HTML-компонент Dynamics 365.
const API_BASE = "";
const $view = document.getElementById("view");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const SPEAKER = {client:"Клиент",operator:"Оператор",bot:"Бот",system:"Система"};

async function api(path, opts) {
  const r = await fetch(API_BASE + path, opts && {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(opts)});
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : "Ошибка запроса");
  return d;
}
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.style.display = "block";
  clearTimeout(toast.t); toast.t = setTimeout(() => t.style.display = "none", 4000);
}
const guard = fn => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message); } };
function fmtValue(c, v) {
  if (v === null || v === undefined) return "N/A";
  if (c.type === "scale") return `${v} из ${Math.max(...c.scale)}`;
  return v ? "Да" : "Нет";
}
function valueTag(c, v) {
  if (c.type === "flag") return v ? `<span class="tag bad">Да, нарушение</span>` : `<span class="tag good">Нет</span>`;
  if (v === null) return `<span class="tag">N/A</span>`;
  const full = c.type === "scale" ? v === Math.max(...c.scale) : v;
  const zero = c.type === "scale" ? v === 0 : !v;
  return `<span class="tag ${full ? "good" : zero ? "bad" : "warn"}">${esc(fmtValue(c, v))}</span>`;
}
const scoreText = v => v === null || v === undefined ? "—" : Number(v).toFixed(2);

// ---------- Список ----------
let listFilter = {q:"", lang:"", only:""};
async function viewList() {
  const rows = await api("/api/conversations");
  const draw = () => {
    const f = listFilter;
    const shown = rows.filter(r => (!f.q || r.cid.includes(f.q)) && (!f.lang || r.language === f.lang) &&
      (!f.only || (f.only === "flag" && r.flag) || (f.only === "review" && r.review) ||
       (f.only === "none" && !r.status) || (f.only === "error" && r.status === "error") || (f.only === "appeal" && r.open_appeals)));
    document.getElementById("rows").innerHTML = shown.map(r => `
      <tr class="link" tabindex="0" data-cid="${esc(r.cid)}">
        <td>${esc(r.cid)}</td><td>${r.channel === "voice call" ? "Звонок" : "Чат"}</td><td>${esc(r.language)}</td>
        <td class="num">${r.turns}</td>
        <td class="num"><strong>${scoreText(r.score)}</strong>${r.original_score !== r.score && r.status === "ok" ? ` <span class="was small">${scoreText(r.original_score)}</span>` : ""}</td>
        <td>${!r.status ? `<span class="tag">Не оценён</span>` : r.status === "error" ? `<span class="tag bad" title="${esc(r.error)}">Ошибка</span>` : `<span class="tag good">Оценён</span>`}
          ${r.flag ? `<span class="tag bad">Грубая ошибка</span>` : ""}${r.review ? ` <span class="tag warn">Проверить</span>` : ""}${r.open_appeals ? ` <span class="tag warn">Апелляция</span>` : ""}</td>
        <td class="small muted">${esc(r.checklist || "")}</td></tr>`).join("") ||
      `<tr><td colspan="7" class="muted">Под этот фильтр диалогов нет.</td></tr>`;
    document.getElementById("count").textContent = `${shown.length} из ${rows.length}`;
  };
  const ok = rows.filter(r => r.status === "ok"), avg = a => a.length ? a.reduce((s, r) => s + r.score, 0) / a.length : null;
  const langs = ["RU", "KZ", "EN"].map(l => [l, ok.filter(r => r.language === l && r.channel !== "voice call")])
    .concat([["Звонки", ok.filter(r => r.channel === "voice call")]]).filter(([, a]) => a.length);
  $view.innerHTML = `<h1>Пульт качества</h1>
    <div class="kpis">
      <div class="kpi ok"><span class="small muted">Средний балл</span><b>${scoreText(avg(ok))}</b><span class="small muted">из 100</span></div>
      <div class="kpi"><span class="small muted">Оценено</span><b>${ok.length}<span class="muted" style="font-size:18px"> / ${rows.length}</span></b><span class="small muted">диалогов</span></div>
      <div class="kpi hot"><span class="small muted">Грубые ошибки</span><b>${ok.filter(r => r.flag).length}</b><span class="small muted">итог не обнуляют</span></div>
      <div class="kpi warn"><span class="small muted">Проверить человеку</span><b>${ok.filter(r => r.review).length}</b><span class="small muted">спорные оценки</span></div>
      <div class="kpi"><span class="small muted">Апелляции</span><b>${rows.reduce((s, r) => s + (r.open_appeals || 0), 0)}</b><span class="small muted">ждут решения</span></div>
    </div>
    ${langs.length ? `<div class="card"><h2>Средний балл по языкам и каналам</h2><div class="langs">${langs.map(([l, a]) => `<div class="lang"><span>${esc(l)} <span class="muted small">· ${a.length}</span></span><div class="bar"><i style="width:${avg(a)}%"></i></div><b class="num">${scoreText(avg(a))}</b></div>`).join("")}</div></div>` : ""}
    <div class="card"><div class="row">
      <input id="q" placeholder="Поиск по ID" aria-label="Поиск по ID" value="${esc(listFilter.q)}">
      <select id="lang" aria-label="Язык"><option value="">Все языки</option><option>RU</option><option>KZ</option><option>EN</option></select>
      <select id="only" aria-label="Фильтр"><option value="">Все</option><option value="flag">Грубые ошибки</option><option value="review">Проверить человеку</option><option value="appeal">С апелляцией</option><option value="none">Не оценённые</option><option value="error">С ошибкой</option></select>
      <span class="muted small" id="count"></span></div></div>
    <div class="card tablewrap"><table><thead><tr><th>ID</th><th>Канал</th><th>Язык</th><th class="num">Реплик</th><th class="num">Итог</th><th>Статус</th><th>Чек-лист</th></tr></thead><tbody id="rows"></tbody></table></div>`;
  document.getElementById("lang").value = listFilter.lang;
  document.getElementById("only").value = listFilter.only;
  document.getElementById("q").oninput = e => { listFilter.q = e.target.value.trim(); draw(); };
  document.getElementById("lang").onchange = e => { listFilter.lang = e.target.value; draw(); };
  document.getElementById("only").onchange = e => { listFilter.only = e.target.value; draw(); };
  document.getElementById("rows").onclick = e => { const t = e.target.closest("tr[data-cid]"); if (t) location.hash = "#/c/" + t.dataset.cid; };
  document.getElementById("rows").onkeydown = e => { if (e.key === "Enter") e.target.closest("tr[data-cid]")?.click(); };
  draw();
}

// ---------- Карточка ----------
function holdTurns(tr) {
  const out = new Set(); let on = false;
  tr.forEach((t, i) => {
    if (t.speaker === "system" && t.text.trim() === "Клиент находится на удержании.") on = true;
    if (on) out.add(i + 1);
    if (t.speaker === "system" && t.text.trim() === "Клиент больше не находится на удержании.") on = false;
  });
  return out;
}
function jumpTo(n) {
  const el = document.getElementById("t" + n);
  if (!el) return;
  el.scrollIntoView({behavior:"smooth",block:"center"});
  document.querySelectorAll(".turn.flash").forEach(x => x.classList.remove("flash"));
  el.classList.add("flash");
}
async function viewCard(cid) {
  const d = await api("/api/conversations/" + encodeURIComponent(cid));
  const ev = d.evaluation, hold = holdTurns(d.transcript);
  const turns = d.transcript.map((t, i) => `<div class="turn ${esc(t.speaker)} ${hold.has(i + 1) ? "hold" : ""}" id="t${i + 1}">
      <div class="meta">[${i + 1}] ${esc(SPEAKER[t.speaker] || t.speaker)} · ${esc(t.timestamp)}${hold.has(i + 1) ? " · клиент не слышит" : ""}</div>${esc(t.text)}</div>`).join("");
  const audio = d.has_audio ? `<div class="card"><h2>Запись звонка</h2>
      <a target="_blank" rel="noopener" href="${API_BASE}/api/audio/${encodeURIComponent(cid)}">Открыть плеер записи в новой вкладке</a></div>` : "";
  $view.innerHTML = `<div class="row spread" style="margin-bottom:12px"><h1 style="margin:0">${esc(cid)}</h1>
      <div class="row"><span class="tag">${d.channel === "voice call" ? "Звонок" : "Чат"}</span><span class="tag">${esc(d.language)}</span>
      <button class="ghost" id="regrade">Оценить заново</button></div></div>
    <div class="split"><div class="pane card" id="tr">${audio}${turns}</div><div id="res">${ev ? renderEval(ev) : `<div class="card muted">Диалог ещё не оценён. Нажмите «Оценить заново».</div>`}</div></div>`;
  document.getElementById("regrade").onclick = guard(async e => {
    e.target.disabled = true; e.target.textContent = "Идёт оценка…";
    const job = await api("/api/evaluate", {cids:[cid]});
    await waitJob(job.id);
    viewCard(cid);
  });
  document.getElementById("res").onclick = guard(onCardClick(ev, cid));
}
function renderEval(ev) {
  if (ev.status !== "ok") return `<div class="card"><div class="banner">Оценить диалог не удалось</div>${esc(ev.error)}</div>`;
  const changed = ev.original_score !== ev.final_score;
  const secs = Object.entries(ev.sections).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${scoreText(v)}</td></tr>`).join("");
  const crits = ev.criteria.map(c => {
    const was = c.original_value !== c.value;
    const app = ev.appeals.filter(a => a.criterion_id === c.id);
    const turns = c.evidence_turns.map(n => `<button class="ev" data-turn="${n}">[${n}]</button>`).join("");
    const tone = c.type === "flag" ? (c.value ? "c-bad" : "c-good") : c.value === null ? "c-na"
      : c.points >= c.effective_weight ? "c-good" : c.points === 0 ? "c-bad" : "c-warn";
    const bar = c.type !== "flag" && c.value !== null && c.effective_weight
      ? `<div class="bar"><i style="width:${Math.round(c.points / c.effective_weight * 100)}%"></i></div>` : "";
    return `<div class="crit ${tone}"><div class="row spread"><strong>${esc(c.title)}</strong><span class="row">
        ${was ? `<span class="was small">${esc(fmtValue(c, c.original_value))}</span>` : ""}${valueTag(c, c.value)}
        ${c.type !== "flag" ? `<span class="small muted">${c.value === null ? "вес перераспределён" : `${c.points.toFixed(2)} из ${c.effective_weight.toFixed(2)}`}</span>` : ""}
        ${c.low_confidence ? `<span class="tag warn">Низкая уверенность</span>` : ""}</span></div>
      <div class="small muted">Вес ${c.weight}</div>${bar}<div style="margin-top:8px">${esc(c.rationale)}</div>
      <div class="row" style="margin-top:6px"><span>${turns}</span><button class="ghost small" data-appeal="${esc(c.id)}">Оспорить</button></div>
      ${app.map(a => `<div class="small" style="margin-top:6px"><span class="tag ${a.status === "open" ? "warn" : "good"}">${a.status === "open" ? "На рассмотрении" : a.decision === "change" ? "Оценка изменена" : "Оценка подтверждена"}</span> ${esc(a.comment)}${a.reason ? `<br><span class="muted">Решение: ${esc(a.reason)}</span>` : ""}</div>`).join("")}
      <div id="ap-${esc(c.id)}"></div></div>`;
  }).join("");
  return `<div class="card">${ev.critical_flag ? `<div class="banner">Выявлена грубая ошибка. Итог она не обнуляет, отметка идёт отдельно.</div>` : ""}
      <div class="row spread"><div class="row" style="gap:16px"><div class="ring" style="--p:${ev.final_score ?? 0};--c:${ev.final_score >= 80 ? "var(--teal)" : ev.final_score >= 50 ? "var(--warn)" : "var(--bad)"}"><div><div><b>${scoreText(ev.final_score)}</b><span>из 100</span></div></div></div>
      <div class="small muted">${changed ? "Финальный итог<br>после апелляции" : "Итоговая<br>оценка"}</div></div>
      ${changed ? `<div><div class="small muted">Исходная оценка</div><div class="was" style="font-size:20px">${scoreText(ev.original_score)}</div></div>` : ""}
      <div class="small muted">Чек-лист ${esc(ev.checklist_version)}<br>Модель ${esc(ev.meta.model || "")}<br>${ev.meta.run_log ? `<a href="#" data-log="${esc(ev.meta.run_log)}">Журнал запуска</a>` : ""}</div></div>
      ${secs ? `<table style="margin-top:10px"><tbody>${secs}</tbody></table>` : ""}
      ${ev.meta.review?.length ? `<p class="small"><span class="tag warn">Проверить человеку</span> ${esc(ev.meta.review.join(" "))}</p>` : ""}
      <div id="logbox"></div></div><div class="card">${crits}</div>`;
}
const onCardClick = (ev, cid) => async e => {
  const t = e.target;
  if (t.dataset.turn) jumpTo(+t.dataset.turn);
  if (t.dataset.log) {
    e.preventDefault();
    const l = await api("/api/runs/" + encodeURIComponent(t.dataset.log));
    document.getElementById("logbox").innerHTML = `<p class="small muted">Модель ${esc(l.model)}, ${esc(l.seconds)} с, вызовов ${l.calls.length}, хэш промпта ${esc(l.prompt_sha256.slice(0, 16))}</p><pre>${esc(l.calls[l.calls.length - 1].raw)}</pre>`;
  }
  if (t.dataset.appeal) {
    const id = t.dataset.appeal;
    document.getElementById("ap-" + id).innerHTML = `<label style="margin-top:8px">Комментарий к апелляции<textarea id="apc-${esc(id)}"></textarea></label><button data-send="${esc(id)}">Подать апелляцию</button>`;
    document.getElementById("apc-" + id).focus();
  }
  if (t.dataset.send) {
    await api("/api/appeals", {evaluation_id:ev.id, criterion_id:t.dataset.send, comment:document.getElementById("apc-" + t.dataset.send).value});
    toast("Апелляция сохранена, её видит проверяющий");
    viewCard(cid);
  }
};

// ---------- Оценка набора ----------
let lastJob = null;
async function waitJob(id, onTick) {
  for (;;) {
    const j = await api("/api/jobs/" + id);
    lastJob = j; onTick?.(j);
    if (j.done) return j;
    await new Promise(r => setTimeout(r, 1500));
  }
}
function jobTable(j) {
  const items = Object.entries(j.items);
  const done = items.filter(([, v]) => v.status === "ok" || v.status === "error").length;
  const label = {queued:"В очереди",running:"Идёт оценка",ok:"Готово",error:"Ошибка"};
  return `<p class="muted">Чек-лист ${esc(j.checklist)}. Обработано ${done} из ${items.length}${j.done ? "." : "…"}</p>
    <div class="tablewrap"><table><thead><tr><th>Диалог</th><th>Статус</th><th class="num">Итог</th></tr></thead><tbody>
    ${items.map(([k, v]) => `<tr><td><a href="#/c/${esc(k)}">${esc(k)}</a></td><td>${esc(label[v.status])}${v.error ? `<div class="small muted">${esc(v.error)}</div>` : ""}</td><td class="num">${scoreText(v.score)}</td></tr>`).join("")}
    </tbody></table></div>${j.done && items.some(([, v]) => v.status === "error") ? `<button id="retry" style="margin-top:10px">Повторить неудачные</button>` : ""}`;
}
async function viewRun() {
  const [cls, conv] = await Promise.all([api("/api/checklists"), api("/api/conversations")]);
  const sets = [...new Set(conv.map(c => c.dataset))];
  $view.innerHTML = `<h1>Оценка набора</h1>
    <div class="card"><h2>1. Получить диалоги</h2><div class="grid2">
      <label>group_id от организаторов<input id="gid" placeholder="например, DEV"></label>
      <label>Файл JSON с транскриптами<input id="file" type="file" accept=".json,application/json"></label></div>
      <button class="ghost" id="imp">Загрузить</button> <span class="small muted" id="impres"></span></div>
    <div class="card"><h2>2. Запустить оценку</h2><div class="grid2">
      <label>Набор<select id="set">${sets.map(s => `<option>${esc(s)}</option>`).join("")}</select></label>
      <label>Чек-лист<select id="cl">${cls.map(c => `<option value="${c.id}">${esc(c.version)} · ${esc(c.title)}</option>`).join("")}</select></label>
      <label>Сколько диалогов (пусто = все)<input id="lim" type="number" min="1"></label></div>
      <button id="go">Оценить весь набор</button></div>
    <div class="card" id="job">${lastJob ? jobTable(lastJob) : `<span class="muted">Прогресс появится здесь после запуска.</span>`}</div>`;
  const run = guard(async cids => {
    const j = await api("/api/evaluate", {cids, checklist_id:+document.getElementById("cl").value});
    await waitJob(j.id, x => {
      const box = document.getElementById("job");
      if (!box) return;
      box.innerHTML = jobTable(x);
      const b = document.getElementById("retry");
      if (b) b.onclick = () => run(Object.entries(x.items).filter(([, v]) => v.status === "error").map(([k]) => k));
    });
  });
  document.getElementById("go").onclick = guard(async () => {
    const fresh = await api("/api/conversations");
    const lim = +document.getElementById("lim").value;
    let ids = fresh.filter(c => c.dataset === document.getElementById("set").value).map(c => c.cid);
    if (lim) ids = ids.slice(0, lim);
    run(ids);
  });
  document.getElementById("imp").onclick = guard(async () => {
    const gid = document.getElementById("gid").value.trim(), f = document.getElementById("file").files[0];
    if (!gid && !f) throw new Error("Укажите group_id или выберите файл");
    const body = gid ? {group_id:gid} : {payload:JSON.parse(await f.text()), dataset:f.name};
    const r = await api("/api/import", body);
    toast(`Загружено диалогов: ${r.imported}`);
    await viewRun();
    document.getElementById("set").value = r.dataset;
    document.getElementById("impres").textContent = `Загружено: ${r.imported}, набор «${r.dataset}»`;
  });
}

// ---------- Чек-лист ----------
let draft = null;
function critForm(c, i) {
  return `<div class="card" data-i="${i}"><div class="row spread"><strong>${esc(c.title || "Новый критерий")}</strong><button class="danger small" data-del="${i}">Удалить</button></div>
    <div class="grid2" style="margin-top:8px">
      <label>Код<input data-f="id" value="${esc(c.id)}"></label>
      <label>Название<input data-f="title" value="${esc(c.title)}"></label>
      <label>Секция<input data-f="section" value="${esc(c.section)}"></label>
      <label>Тип ответа<select data-f="type">
        <option value="bool" ${c.type === "bool" ? "selected" : ""}>Да / Нет</option>
        <option value="scale" ${c.type === "scale" ? "selected" : ""}>Числовая шкала</option>
        <option value="flag" ${c.type === "flag" ? "selected" : ""}>Флаг без баллов</option></select></label>
      <label>Вес<input data-f="weight" type="number" min="0" step="any" value="${esc(c.weight)}" ${c.type === "flag" ? "disabled" : ""}></label>
      ${c.type === "scale" ? `<label>Ступени шкалы через запятую<input data-f="scale" value="${esc((c.scale || []).join(", "))}"></label>` : ""}
    </div>
    <label>Формулировка<textarea data-f="rule">${esc(c.rule)}</textarea></label>
    <label style="display:flex;gap:7px;align-items:center"><input type="checkbox" data-f="na_allowed" ${c.na_allowed ? "checked" : ""}> N/A доступен</label>
    ${c.na_allowed ? `<label>Условие N/A<input data-f="na_condition" value="${esc(c.na_condition)}"></label>` : ""}
    <label>Подсказка для модели (необязательно)<textarea data-f="guidance">${esc(c.guidance)}</textarea></label>
    ${c.modes ? `<label>Чтение критерия<select data-f="mode">${Object.keys(c.modes).map(m => `<option value="${esc(m)}" ${c.mode === m ? "selected" : ""}>${m === "strict" ? "Строгое: простое прощание = Нет" : m === "soft" ? "Мягкое: простое прощание = Да" : esc(m)}</option>`).join("")}</select></label>` : ""}
    <label style="display:flex;gap:7px;align-items:center"><input type="checkbox" data-f="keep_guidance" ${c.keep_guidance ? "checked" : ""}> Сохранить подсказку при изменении формулировки</label></div>`;
}
async function viewChecklist() {
  const cls = await api("/api/checklists");
  const cur = cls[0];
  if (!draft) draft = JSON.parse(JSON.stringify(cur.criteria));
  const total = draft.filter(c => c.type !== "flag").reduce((s, c) => s + (+c.weight || 0), 0);
  $view.innerHTML = `<div class="row spread" style="margin-bottom:12px"><h1 style="margin:0">Чек-лист</h1>
      <div class="row"><span class="muted small">Действует ${esc(cur.version)}. Сумма весов ${total}${total !== 100 ? ", итог нормализуется до 100" : ""}.</span>
      <button class="ghost" id="add">Добавить критерий</button><button id="save">Сохранить новую версию</button></div></div>
    <div id="forms">${draft.map(critForm).join("")}</div>`;
  const forms = document.getElementById("forms");
  const read = t => {
    const c = draft[+t.closest("[data-i]").dataset.i], f = t.dataset.f;
    if (t.type === "checkbox") c[f] = t.checked;
    else if (f === "scale") c.scale = t.value.split(",").map(s => parseInt(s, 10)).filter(n => !isNaN(n));
    else c[f] = t.value;
    return f;
  };
  forms.oninput = e => { if (e.target.dataset.f) read(e.target); };
  forms.onchange = e => {
    if (!e.target.dataset.f) return;
    const f = read(e.target);
    if (f === "type" || f === "na_allowed") {
      const c = draft[+e.target.closest("[data-i]").dataset.i];
      if (c.type === "scale" && !c.scale?.length) c.scale = [0, 5, 10];
      viewChecklist();
    }
  };
  forms.onclick = e => { if (e.target.dataset.del !== undefined) { draft.splice(+e.target.dataset.del, 1); viewChecklist(); } };
  document.getElementById("add").onclick = () => {
    draft.push({id:"new_" + (draft.length + 1), title:"", section:"", type:"bool", weight:10, rule:"", na_allowed:false, na_condition:"", guidance:"", guards:[]});
    viewChecklist().then(() => window.scrollTo(0, document.body.scrollHeight));
  };
  document.getElementById("save").onclick = guard(async () => {
    const r = await api("/api/checklists", {title:cur.title, criteria:draft});
    draft = null;
    toast(`Сохранена версия ${r.version}. Запустите её на экране «Оценка набора».`);
    viewChecklist();
  });
}

// ---------- Апелляции ----------
function valueOptions(c) {
  const opts = c.type === "scale" ? c.scale.map(s => [s, String(s)]) : [[true, "Да"], [false, "Нет"]];
  if (c.na_allowed) opts.push([null, "N/A"]);
  return opts.map(([v, l]) => `<option value='${JSON.stringify(v)}' ${v === c.value ? "selected" : ""}>${l}</option>`).join("");
}
async function viewAppeals() {
  const rows = await api("/api/appeals");
  $view.innerHTML = `<h1>Апелляции</h1>` + (rows.map(a => `<div class="card" data-id="${a.id}">
      <div class="row spread"><div><a href="#/c/${esc(a.cid)}"><strong>${esc(a.cid)}</strong></a> · ${esc(a.criterion?.title)}</div>
      <span class="tag ${a.status === "open" ? "warn" : "good"}">${a.status === "open" ? "Ждёт решения" : a.decision === "change" ? "Оценка изменена" : "Оценка подтверждена"}</span></div>
      <p><span class="muted small">Спорная оценка:</span> ${valueTag(a.criterion, a.criterion.original_value)} ${esc(a.criterion.rationale)}</p>
      <p><span class="muted small">Комментарий:</span> ${esc(a.comment)}</p>
      ${a.fragment.map(t => `<div class="turn ${esc(t.speaker)}"><div class="meta">[${t.n}] ${esc(SPEAKER[t.speaker] || t.speaker)}</div>${esc(t.text)}</div>`).join("")}
      <p class="small muted">Итог: исходный ${scoreText(a.original_score)}, текущий ${scoreText(a.final_score)}</p>
      ${a.status === "open" ? `<div class="grid2"><label>Новое значение (для изменения)<select class="nv">${valueOptions(a.criterion)}</select></label>
        <label>Причина решения<input class="rs"></label></div>
        <div class="row"><button class="ghost" data-act="confirm">Подтвердить оценку</button><button data-act="change">Изменить оценку</button></div>`
      : `<p><span class="muted small">Причина решения:</span> ${esc(a.reason)}</p>`}</div>`).join("") ||
    `<div class="card muted">Апелляций пока нет. Подать её можно в карточке диалога кнопкой «Оспорить».</div>`);
  $view.onclick = guard(async e => {
    const act = e.target.dataset.act;
    if (!act) return;
    const box = e.target.closest("[data-id]");
    const r = await api(`/api/appeals/${box.dataset.id}/decision`, {decision:act, reason:box.querySelector(".rs").value,
      value:act === "change" ? JSON.parse(box.querySelector(".nv").value) : null});
    toast(`Решение сохранено. Итог ${scoreText(r.original_score)} → ${scoreText(r.final_score)}`);
    viewAppeals();
  });
}

// ---------- Сводка ----------
async function viewStats() {
  const s = await api("/api/stats");
  $view.innerHTML = `<h1>Сводка</h1>
    <div class="card"><h2>Журнал грубых нарушений</h2>${s.violations.map(v => `<div class="crit"><a href="#/c/${esc(v.cid)}"><strong>${esc(v.cid)}</strong></a> ${v.turns.map(n => `[${n}]`).join(" ")}<div>${esc(v.rationale)}</div></div>`).join("") || `<span class="muted">Нарушений не найдено.</span>`}</div>
    <div class="card tablewrap"><h2>Где теряются баллы</h2><table><thead><tr><th>Критерий</th><th class="num">Оценено</th><th class="num">N/A</th><th class="num">Потеряно баллов</th></tr></thead>
    <tbody>${s.criteria.map(c => `<tr><td>${esc(c.title)}</td><td class="num">${c.n}</td><td class="num">${c.na}</td><td class="num">${c.lost.toFixed(1)}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">Нет оценённых диалогов.</td></tr>`}</tbody></table></div>`;
}

// ---------- Навигация ----------
const route = guard(async () => {
  const h = location.hash || "#/list";
  $view.onclick = null;
  const [, tab, arg] = h.split("/");
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.dataset.tab === (tab === "c" ? "list" : tab)));
  if (tab === "c") return viewCard(decodeURIComponent(arg));
  return ({run:viewRun, checklist:viewChecklist, appeals:viewAppeals, stats:viewStats}[tab] || viewList)();
});
addEventListener("hashchange", route);
route();
