/* ECE Academic Advisor — front-end (vanilla JS, no build step) */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const state = { me: null, courses: [], streams: ['Computer','Communication','Control','Power'], grades: [],
  plan: { year: 3, sem: 1, stream: null, failed: [], added: [], dropped: [] } };

/* ---------- api / ui helpers ---------- */
async function api(path, opts = {}) {
  const o = { headers: { 'Content-Type': 'application/json' }, ...opts };
  if (o.body && typeof o.body !== 'string') o.body = JSON.stringify(o.body);
  let r, data;
  try { r = await fetch('/api/' + path, o); data = await r.json(); }
  catch { throw new Error('Cannot reach the server. Is it running?'); }
  if (!data.ok) { const e = new Error(data.error || 'Something went wrong'); e.status = r.status; throw e; }
  return data;
}
let toastT;
function toast(msg, bad) { const t = $('#toast'); t.textContent = msg; t.className = 'show' + (bad ? ' bad' : ''); clearTimeout(toastT); toastT = setTimeout(() => t.className = '', 3200); }
const semLabel = (y, s) => `Year ${y} · ${s === 3 ? 'Summer' : 'Sem ' + s}`;
const semsOfYear = y => y === 4 ? [1, 2, 3] : [1, 2];
const streaming = (y, s) => y > 4 || (y === 4 && s >= 2);
function semSeq(maxYear = 10) { const out = []; for (let y = 1; y <= maxYear; y++) semsOfYear(y).forEach(s => out.push([y, s])); return out; }
function seg(el, items, value, onPick) {
  el.innerHTML = items.map(i => `<button type="button" data-v="${esc(i.v)}" ${i.disabled ? 'disabled' : ''} class="${String(i.v) === String(value) ? 'on' : ''}">${esc(i.t)}</button>`).join('');
  el.onclick = e => { const b = e.target.closest('button'); if (!b || b.disabled) return; $$('button', el).forEach(x => x.classList.toggle('on', x === b)); onPick(b.dataset.v); };
}
const modal = (id, open) => { const m = $(id); m.classList.toggle('open', open); m.setAttribute('aria-hidden', !open); };
document.addEventListener('click', e => { if (e.target.closest('[data-close]')) { modal('#drawer', false); modal('#login-modal', false); } });
document.addEventListener('keydown', e => { if (e.key === 'Escape') { modal('#drawer', false); modal('#login-modal', false); } });

/* ---------- router ---------- */
const routes = ['home', 'planner', 'courses', 'gpa', 'feedback'];
function route() {
  const r = routes.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'home';
  $$('.view').forEach(v => v.classList.toggle('on', v.id === 'view-' + r));
  $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.route === r));
  window.scrollTo({ top: 0 });
  renderGates();
}
window.addEventListener('hashchange', route);

/* ---------- account ---------- */
function renderAccount() {
  const a = $('#account');
  a.innerHTML = state.me
    ? `<div class="chip-user">👤 ${esc(state.me.id)} <button id="logout">Sign out</button></div>`
    : `<button class="btn primary sm" id="signin">Sign in</button>`;
  if ($('#logout')) $('#logout').onclick = async () => { await api('logout', { method: 'POST' }); state.me = null; renderAccount(); renderGates(); toast('Signed out'); };
  if ($('#signin')) $('#signin').onclick = openLogin;
  renderGates();
}
function renderGates() {
  const gate = (el, what) => {
    $(el).innerHTML = state.me ? '' : `<span>🔐 Sign in with your student ID to ${what}.</span><button class="btn primary sm" data-login>Sign in</button>`;
  };
  gate('#planner-gate', 'generate and save your course plan');
  gate('#fb-gate', 'send feedback');
  $$('[data-login]').forEach(b => b.onclick = openLogin);
}
function openLogin() { $('#login-err').textContent = ''; modal('#login-modal', true); setTimeout(() => $('#login-id').focus(), 50); }
$('#login-form').onsubmit = async e => {
  e.preventDefault();
  try {
    const d = await api('login', { method: 'POST', body: { student_id: $('#login-id').value } });
    state.me = d.student; modal('#login-modal', false); renderAccount();
    toast(d.created ? `Welcome! Profile created for ${d.student.id}` : `Welcome back, ${d.student.id}`);
  } catch (err) { $('#login-err').textContent = err.message; }
};
async function requireLogin() { if (state.me) return true; openLogin(); return false; }

/* ---------- course picker (autocomplete chips) ---------- */
function initPicker(el) {
  const key = el.dataset.key;
  el.innerHTML = `<div class="chips"></div><input placeholder="Type a course name or code…"><div class="sugg"></div>`;
  const chips = $('.chips', el), input = $('input', el), sugg = $('.sugg', el);
  let hl = -1, opts = [];
  const draw = () => {
    chips.innerHTML = state.plan[key].map(c => { const co = state.courses.find(x => x.code === c); return `<span class="chip"><b>${esc(c)}</b> ${esc(co ? co.name : '')}<button type="button" data-rm="${esc(c)}">×</button></span>`; }).join('');
  };
  const pick = code => { if (!state.plan[key].includes(code)) state.plan[key].push(code); input.value = ''; sugg.classList.remove('open'); draw(); input.focus(); };
  const search = () => {
    const q = input.value.trim().toLowerCase(); if (!q) return sugg.classList.remove('open');
    opts = state.courses.filter(c => !state.plan[key].includes(c.code) && (c.code.toLowerCase().includes(q) || c.name.toLowerCase().includes(q))).slice(0, 8);
    hl = opts.length ? 0 : -1;
    sugg.innerHTML = opts.length ? opts.map((c, i) => `<div data-c="${esc(c.code)}" class="${i === hl ? 'hl' : ''}">${esc(c.name)}<span>${esc(c.code)} · Y${c.year}S${c.semester}</span></div>`).join('') : '<div>No match</div>';
    sugg.classList.add('open');
  };
  input.oninput = search;
  input.onkeydown = e => {
    if (e.key === 'Enter') { e.preventDefault(); if (opts[hl]) pick(opts[hl].code); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); hl = (hl + (e.key === 'ArrowDown' ? 1 : -1) + opts.length) % (opts.length || 1); $$('div', sugg).forEach((d, i) => d.classList.toggle('hl', i === hl)); }
  };
  input.onblur = () => setTimeout(() => sugg.classList.remove('open'), 150);
  sugg.onmousedown = e => { const d = e.target.closest('[data-c]'); if (d) pick(d.dataset.c); };
  chips.onclick = e => { const b = e.target.closest('[data-rm]'); if (b) { state.plan[key] = state.plan[key].filter(c => c !== b.dataset.rm); draw(); } };
  draw();
}

/* ---------- PLANNER ---------- */
function drawPlannerSeg() {
  const p = state.plan;
  seg($('#pl-year'), [1, 2, 3, 4, 5].map(y => ({ v: y, t: 'Year ' + y })), p.year, v => { p.year = +v; if (!semsOfYear(p.year).includes(p.sem)) p.sem = 1; drawPlannerSeg(); });
  seg($('#pl-sem'), [1, 2, 3].map(s => ({ v: s, t: s === 3 ? 'Summer' : 'Sem ' + s, disabled: !semsOfYear(p.year).includes(s) })), p.sem, v => { p.sem = +v; drawPlannerSeg(); });
  const need = streaming(p.year, p.sem);
  $('#pl-stream-step').hidden = !need;
  if (need) seg($('#pl-stream'), [...state.streams.map(s => ({ v: s, t: s })), { v: '', t: 'Compare all' }], p.stream || '', v => p.stream = v || null);
  else p.stream = null;
}
$('#plan-form').onsubmit = async e => {
  e.preventDefault();
  if (!await requireLogin()) return;
  const p = state.plan, btn = $('#plan-go');
  btn.disabled = true; btn.textContent = 'Calculating…';
  $('#plan-results').innerHTML = `<div class="empty"><div class="big">⏳</div><h3>Crunching your academic path…</h3></div>`;
  try {
    const d = await api('plan', { method: 'POST', body: { year: p.year, semester: p.sem, stream: p.stream, failed: p.failed, added: p.added, dropped: p.dropped } });
    renderPlan(d);
    if (window.innerWidth < 900) $('#plan-results').scrollIntoView({ behavior: 'smooth' });
  } catch (err) {
    if (err.status === 401) { state.me = null; renderAccount(); openLogin(); }
    $('#plan-results').innerHTML = `<div class="banner bad">❌ ${esc(err.message)}</div>`;
  } finally { btn.disabled = false; btn.textContent = 'Generate my plan'; }
};
function planBody(plan) {
  if (!plan.feasible) {
    const msgs = { RETAKE_LIMIT_EXCEEDED: "You've used all allowed attempts for at least one required course. Please contact the department office.", CAPSTONE_UNSCHEDULABLE: "A final requirement (e.g. the National Exit Exam) couldn't be placed in the planning window." };
    return `<div class="banner bad"><b>No feasible plan found.</b><br>${esc(msgs[plan.status] || 'No valid path could be found with the information provided.')}${plan.violations.length ? '<ul>' + plan.violations.map(v => `<li>${esc(v)}</li>`).join('') + '</ul>' : ''}</div>`;
  }
  const g = plan.graduation;
  let h = `<div class="summary">
    <div class="stat grad"><span>Expected graduation</span><b>Year ${g.year} · Sem ${g.semester}</b></div>
    <div class="stat"><span>Terms remaining</span><b>${plan.terms.length}</b></div>
    <div class="stat"><span>Courses</span><b>${plan.total_courses}</b></div>
    <div class="stat"><span>Credit hours</span><b>${plan.total_credits}</b></div></div>`;
  if (plan.exceeds_5_year_policy) h += `<div class="banner warn">⚠️ <b>This plan takes longer than the standard 5-year timeline.</b></div>`;
  if (plan.waivers.length) h += `<div class="banner info">📌 <b>Practical exception applied:</b> this plan doesn't require ${esc(plan.waivers.join(' and '))} to be finished before Final Year Project II. It's an accommodation some departments allow, not a formal rule — confirm with the department office.</div>`;
  h += `<div class="timeline">` + plan.terms.map((t, i) => `<div class="term ${t.semester === 3 ? 'summer' : ''} ${i === plan.terms.length - 1 ? 'last' : ''}" style="animation-delay:${i * 50}ms">
    <div class="term-h"><h4>${t.semester === 3 ? '☀️' : '📖'} Year ${t.year} · ${esc(t.label)}</h4><span class="badge">${t.total_credits} cr</span></div>
    ${t.courses.map(c => `<div class="crs" data-code="${esc(c.code)}"><span class="nm">${esc(c.name)}${c.cross_dept_note ? `<span class="tag">with ${esc(c.cross_dept_note)}</span>` : ''}</span><span class="cr">${c.credit_hours} cr</span></div>`).join('')}</div>`).join('') + `</div>`;
  if (plan.warnings.length) h += `<div class="banner info"><b>ℹ️ Notes</b><ul>${plan.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>`;
  if (plan.explanation_html) h += `<details class="why"><summary>💡 Why does my plan look like this?</summary><div class="richtext" style="margin-top:10px">${plan.explanation_html}</div></details>`;
  h += `<p class="disclaimer">The generated course plan might not always be optimal and should be verified with the department registrar.</p>`;
  return h;
}
function renderPlan(d) {
  const box = $('#plan-results');
  const actions = `<div class="actions"><button class="btn ghost sm" onclick="window.print()">🖨️ Print / Save PDF</button></div>`;
  if (d.mode === 'single') { box.innerHTML = `<h3 style="margin-bottom:12px">🎯 Your ${esc(d.stream)} stream plan</h3>${actions}${planBody(d.plan)}`; }
  else {
    const ok = d.streams.filter(s => s.plan.feasible);
    box.innerHTML = `<h3>⚖️ Stream comparison</h3><p class="hint" style="margin:4px 0 14px">Not committed to a stream yet — here's how each one looks. Select one for the full plan.</p>
      <div class="stream-tabs">${d.streams.map((s, i) => `<button class="stab ${s.plan.feasible ? '' : 'x'}" data-i="${i}"><b>${esc(s.stream)}</b><small>${s.plan.feasible ? `🎓 Y${s.plan.graduation.year} S${s.plan.graduation.semester}${s.plan.exceeds_5_year_policy ? ' ⚠️' : ''}${s.plan.waivers.length ? ' 📌' : ''}` : '❌ no feasible plan'}</small></button>`).join('')}</div>${actions}<div id="stream-plan"></div>`;
    const show = i => { $$('.stab', box).forEach((b, j) => b.classList.toggle('on', i === j)); $('#stream-plan').innerHTML = planBody(d.streams[i].plan); };
    $$('.stab', box).forEach(b => b.onclick = () => show(+b.dataset.i));
    show(Math.max(0, d.streams.findIndex(s => s.plan.feasible)));
  }
}
document.addEventListener('click', e => { const c = e.target.closest('.crs[data-code]'); if (c && c.dataset.code !== 'null') openCourse(c.dataset.code); });

/* ---------- COURSE DRAWER ---------- */
async function openCourse(code) {
  const body = $('#drawer-body'); body.innerHTML = '<p class="loading">Loading…</p>'; modal('#drawer', true);
  const q = 'q=' + encodeURIComponent(code);
  try {
    const [c, dep, down] = await Promise.all(['course', 'dependants', 'downstream'].map(k => api(`lookup/${k}?${q}`)));
    body.innerHTML = `<div class="richtext">${c.html}</div>
      <div class="dsec"><h5>🔗 Directly required by</h5><div class="richtext">${dep.html}</div></div>
      <div class="dsec"><h5>⚠️ Impact of failing / dropping</h5><div class="richtext">${down.html}</div></div>`;
  } catch (err) { body.innerHTML = `<div class="banner bad">${esc(err.message)}</div>`; }
}

/* ---------- CURRICULUM EXPLORER ---------- */
const cf = { q: '', year: '', stream: '', mode: 'all' };
function drawCourses() {
  const box = $('#c-list');
  if (cf.mode !== 'all') {
    box.innerHTML = '<p class="loading">Loading…</p>';
    api('lookup/' + (cf.mode === 'dept' ? 'cross-department' : 'cross-stream')).then(d => box.innerHTML = `<div class="card richtext">${d.html}</div>`);
    return;
  }
  const q = cf.q.toLowerCase();
  const list = state.courses.filter(c => (!q || c.name.toLowerCase().includes(q) || c.code.toLowerCase().includes(q)) && (!cf.year || c.year == cf.year) && (!cf.stream || !c.streams || c.streams.includes(cf.stream)));
  if (!list.length) { box.innerHTML = '<div class="empty"><div class="big">🔍</div><h3>No courses match</h3></div>'; return; }
  const groups = {}; list.forEach(c => (groups[`${c.year}-${c.semester}`] ||= []).push(c));
  box.innerHTML = Object.entries(groups).map(([k, cs]) => { const [y, s] = k.split('-').map(Number);
    return `<div class="grp"><h3>${semLabel(y, s)} <small>${cs.length} courses · ${cs.reduce((a, c) => a + c.credit_hours, 0)} cr</small></h3><div class="grid">${cs.map(c => `
      <div class="ccard ${c.streams ? 's' : ''}" data-code="${esc(c.code)}"><div class="code">${esc(c.code)}</div><div class="name">${esc(c.name)}</div>
      <div class="meta"><span class="tg">${c.credit_hours} cr</span>${c.streams ? `<span class="tg t">${esc(c.streams.join(' · '))}</span>` : ''}${c.college_wide ? '<span class="tg">College-wide</span>' : ''}${c.cross_department ? '<span class="tg">Cross-dept</span>' : ''}</div></div>`).join('')}</div></div>`; }).join('');
}
$('#c-list').onclick = e => { const c = e.target.closest('.ccard'); if (c) openCourse(c.dataset.code); };
$('#c-search').oninput = e => { cf.q = e.target.value; drawCourses(); };
$('#c-year').onchange = e => { cf.year = e.target.value; drawCourses(); };
$('#c-stream').onchange = e => { cf.stream = e.target.value; drawCourses(); };
$('#c-mode').onclick = e => { const b = e.target.closest('button'); if (!b) return; $$('#c-mode button').forEach(x => x.classList.toggle('on', x === b)); cf.mode = b.dataset.m; drawCourses(); };

/* ---------- GPA TOOLS ---------- */
const gs = { tab: 'sgpa', sg: { year: 1, sem: 1, stream: '', rows: [] } };
const semOpts = (sel, max = 10) => semSeq(max).map(([y, s]) => `<option value="${y}-${s}" ${sel === `${y}-${s}` ? 'selected' : ''}>${semLabel(y, s)}</option>`).join('');
const streamSel = id => `<select id="${id}"><option value="">Select stream…</option>${state.streams.map(s => `<option>${s}</option>`).join('')}</select>`;
function gradeSelect(v) { return `<select>${state.grades.map(g => `<option ${g.grade === v ? 'selected' : ''} value="${g.grade}">${g.grade} (${g.points.toFixed(2)})</option>`).join('')}</select>`; }
function setGpaResult(h) { $('#g-result').innerHTML = h; }

function drawGpa() {
  $$('#g-tabs button').forEach(b => b.classList.toggle('on', b.dataset.t === gs.tab));
  setGpaResult('<div class="empty"><div class="big">🧮</div><h3>Results show up here</h3></div>');
  ({ sgpa: drawSgpa, goal: () => drawForecast('goal'), whatif: () => drawForecast('whatif') })[gs.tab]();
}
$('#g-tabs').onclick = e => { const b = e.target.closest('button'); if (b) { gs.tab = b.dataset.t; drawGpa(); } };

function drawSgpa() {
  const f = $('#g-form'), s = gs.sg;
  f.innerHTML = `<label class="lbl">Semester</label><select id="sg-sem">${semOpts(`${s.year}-${s.sem}`, 5)}</select>
    <div id="sg-stream" ${streaming(s.year, s.sem) ? '' : 'hidden'}><label class="lbl">Stream</label>${streamSel('sg-st')}</div>
    <div id="sg-rows"></div>
    <label class="lbl">Took an extra course? <small>(added)</small></label><div id="sg-add" class="picker"></div>
    <button class="btn primary block" id="sg-go" style="margin-top:16px">Calculate SGPA</button>`;
  if (s.stream) $('#sg-st').value = s.stream;
  $('#sg-sem').onchange = e => { [s.year, s.sem] = e.target.value.split('-').map(Number); drawSgpa(); };
  $('#sg-st').onchange = e => { s.stream = e.target.value; loadSgpaRows(); };
  // add-course picker (reuses autocomplete, separate bucket)
  state.plan._sgadd = []; const pk = $('#sg-add'); pk.dataset.key = '_sgadd'; initPicker(pk);
  const origDraw = pk.querySelector('.chips'); new MutationObserver(() => {
    state.plan._sgadd.splice(0).forEach(code => { const c = state.courses.find(x => x.code === code); if (c && !s.rows.some(r => r.code === code)) s.rows.push({ code, name: c.name, credit_hours: c.credit_hours, grade: 'A' }); });
    drawSgRows();
  }).observe(origDraw, { childList: true });
  $('#sg-go').onclick = calcSgpa; loadSgpaRows();
}
async function loadSgpaRows() {
  const s = gs.sg; if (streaming(s.year, s.sem) && !s.stream) { s.rows = []; return drawSgRows(); }
  try { const d = await api(`gpa/semester-courses?year=${s.year}&sem=${s.sem}&stream=${encodeURIComponent(s.stream || '')}`); s.rows = d.courses.map(c => ({ code: c.code, name: c.name, credit_hours: c.credit_hours, grade: 'A' })); }
  catch (err) { s.rows = []; toast(err.message, true); }
  drawSgRows();
}
function drawSgRows() {
  const s = gs.sg, el = $('#sg-rows'); if (!el) return;
  el.innerHTML = s.rows.length ? `<label class="lbl">Your grades <small>(remove any course you dropped)</small></label>` + s.rows.map((r, i) => `<div class="grade-row" data-i="${i}"><span>${esc(r.name)} <small>${r.credit_hours} cr</small></span>${gradeSelect(r.grade)}<button class="x2" title="Remove">✕</button></div>`).join('') : `<p class="hint" style="margin-top:12px">${streaming(s.year, s.sem) && !s.stream ? 'Pick your stream to load courses.' : 'No courses found — add some below.'}</p>`;
  $$('.grade-row', el).forEach(row => { const i = +row.dataset.i; $('select', row).onchange = e => s.rows[i].grade = e.target.value; $('.x2', row).onclick = () => { s.rows.splice(i, 1); drawSgRows(); }; });
}
async function calcSgpa() {
  try {
    const d = await api('gpa/sgpa', { method: 'POST', body: { courses: gs.sg.rows } });
    setGpaResult(`<div class="resbox good"><span class="hint">Your SGPA for ${semLabel(gs.sg.year, gs.sg.sem)}</span><div class="bigno">${d.sgpa.toFixed(2)}</div><div class="gauge"><i style="width:${d.sgpa / 4 * 100}%"></i></div><p class="hint">${d.credits} credit hours · ${gs.sg.rows.length} courses</p></div>`);
  } catch (err) { toast(err.message, true); }
}

function parseOverrides(txt) { const o = {}; (txt || '').split(/[,\s]+/).filter(Boolean).forEach(p => { const m = p.match(/^(\d{1,2})[yY](\d)[sS]=(\d+)$/); if (m) o[`${m[1]}Y${m[2]}S`] = +m[3]; }); return o; }
function drawForecast(mode) {
  const f = $('#g-form');
  f.innerHTML = `<label class="lbl">Current semester</label><select id="fc-cur">${semOpts('2-1')}</select>
    <div id="fc-stream-w" hidden><label class="lbl">Your stream</label>${streamSel('fc-stream')}</div>
    <div class="row"><div><label class="lbl">Current CGPA</label><input id="fc-cgpa" type="number" step="0.01" min="0" max="4" placeholder="3.10"></div>
    <div><label class="lbl">${mode === 'goal' ? 'Goal CGPA' : 'Project until'}</label>${mode === 'goal' ? '<input id="fc-goal" type="number" step="0.01" min="0" max="4" placeholder="3.50">' : ''}</div></div>
    <label class="lbl">${mode === 'goal' ? 'Reach goal by' : 'Project through'}</label><select id="fc-end">${semOpts('5-2')}</select>
    <div id="fc-extra"></div>
    <details style="margin-top:12px"><summary class="hint" style="cursor:pointer">Advanced: course drops / adds</summary>
      <label class="lbl">Dropped / skipped in previous semesters</label><div class="picker" data-key="_pd"></div>
      <label class="lbl">Taken ahead of schedule in previous semesters</label><div class="picker" data-key="_pa"></div>
      <label class="lbl">Credit overrides for future semesters <small>(e.g. 3Y2S=12, 4Y1S=19)</small></label><input id="fc-ov" placeholder="optional"></details>
    <button class="btn primary block" id="fc-go" style="margin-top:16px">${mode === 'goal' ? 'Find required SGPA' : 'Project my CGPA'}</button>`;
  state.plan._pd = []; state.plan._pa = []; $$('#g-form .picker').forEach(initPicker);
  const win = () => { const [cy, cs] = $('#fc-cur').value.split('-').map(Number), [ey, es] = $('#fc-end').value.split('-').map(Number); const seq = semSeq(); const a = seq.findIndex(x => x[0] === cy && x[1] === cs), b = seq.findIndex(x => x[0] === ey && x[1] === es); return { cy, cs, ey, es, w: b >= a ? seq.slice(a, b + 1) : [] }; };
  const refresh = () => {
    const { cy, cs, ey, es, w } = win();
    $('#fc-stream-w').hidden = !(streaming(cy, cs) || streaming(ey, es));
    const oob = w.filter(([y]) => y > 5);
    $('#fc-extra').innerHTML = oob.map(([y, s]) => `<label class="lbl">Credit hours in ${y}Y${s}S <small>(beyond the 5-year curriculum)</small></label><input type="number" min="1" class="oob" data-t="${y}Y${s}S" placeholder="15">`).join('')
      + (mode === 'whatif' ? `<label class="lbl">Expected SGPA per semester</label>` + w.map(([y, s]) => `<div class="row" style="align-items:center;margin-bottom:6px"><span style="font-size:.9rem">${semLabel(y, s)}</span><input type="number" class="sg" step="0.01" min="0" max="4" placeholder="3.40"></div>`).join('') : '');
  };
  $('#fc-cur').onchange = refresh; $('#fc-end').onchange = refresh; refresh();
  $('#fc-go').onclick = async () => {
    const { cy, cs, ey, es } = win(), oob = {}; $$('.oob').forEach(i => oob[i.dataset.t] = +i.value || 0);
    try {
      const d = await api('gpa/forecast', { method: 'POST', body: { mode, year: cy, semester: cs, end_year: ey, end_semester: es, stream: $('#fc-stream').value, cgpa: $('#fc-cgpa').value, goal_cgpa: $('#fc-goal') ? $('#fc-goal').value : null, sgpas: $$('.sg').map(i => i.value), past_dropped: state.plan._pd, past_added: state.plan._pa, overrides: parseOverrides($('#fc-ov').value), oob_credits: oob } });
      renderForecast(d);
    } catch (err) { toast(err.message, true); }
  };
}
function renderForecast(d) {
  const sems = `<table class="t"><tr><th>Semester</th><th>Credits</th>${d.mode === 'whatif' ? '<th>SGPA</th><th>Running CGPA</th>' : ''}</tr>${d.semesters.map((s, i) => `<tr><td>${esc(s.token)}</td><td>${s.credits}${s.out_of_batch ? ' <small>(entered)</small>' : ''}</td>${d.mode === 'whatif' ? `<td>${d.projection[i].sgpa.toFixed(2)}</td><td><b>${d.projection[i].running_cgpa.toFixed(2)}</b></td>` : ''}</tr>`).join('')}</table>`;
  const basis = `<p class="hint">Based on ${d.prev_credits} earned credit hours at CGPA ${d.current_cgpa.toFixed(2)}.</p>`;
  if (d.mode === 'goal') {
    if (d.already_secured) return setGpaResult(`<div class="resbox good"><h3>🎉 Goal already secured!</h3><p>Even with an SGPA of 0.00 in every remaining semester you'd still finish at ${d.goal_cgpa.toFixed(2)} or higher.</p>${sems}${basis}</div>`);
    return setGpaResult(`<div class="resbox ${d.feasible ? 'good' : 'bad'}"><span class="hint">Minimum SGPA needed each semester for a ${d.goal_cgpa.toFixed(2)} CGPA</span><div class="bigno">${d.required_sgpa.toFixed(2)}</div>
      <div class="gauge"><i style="width:${Math.min(100, d.required_sgpa / 4 * 100)}%"></i></div>
      <p>${d.feasible ? 'Achievable — keep every semester at or above this SGPA.' : '⚠️ This exceeds the 4.00 maximum, so the goal is not reachable in this window. Try a later target or a lower goal.'}</p>${sems}${basis}</div>`);
  }
  const last = d.projection[d.projection.length - 1];
  setGpaResult(`<div class="resbox good"><span class="hint">Projected CGPA after ${esc(d.semesters[d.semesters.length - 1].token)}</span><div class="bigno">${last.running_cgpa.toFixed(2)}</div><div class="gauge"><i style="width:${last.running_cgpa / 4 * 100}%"></i></div>${sems}${basis}</div>`);
}

/* ---------- FEEDBACK ---------- */
let rating = 0;
$('#stars').innerHTML = [1, 2, 3, 4, 5].map(n => `<button type="button" data-n="${n}">⭐</button>`).join('');
$('#stars').onclick = e => { const b = e.target.closest('button'); if (!b) return; rating = +b.dataset.n; $$('#stars button').forEach(x => x.classList.toggle('on', +x.dataset.n <= rating)); };
$('#fb-form').onsubmit = async e => {
  e.preventDefault(); if (!await requireLogin()) return;
  if (!rating) return toast('Please choose a rating first', true);
  try { await api('feedback', { method: 'POST', body: { rating, comment: $('#fb-comment').value } }); toast('Thank you for your feedback! 🙏'); $('#fb-comment').value = ''; rating = 0; $$('#stars button').forEach(x => x.classList.remove('on')); }
  catch (err) { toast(err.message, true); }
};

/* ---------- boot ---------- */
(async function init() {
  try {
    const [meta, cs, me] = await Promise.all([api('meta'), api('courses'), api('me')]);
    state.streams = meta.streams; state.grades = meta.grades; state.courses = cs.courses; state.me = me.student;
  } catch (err) { toast(err.message, true); }
  $('#c-year').innerHTML += [1, 2, 3, 4, 5].map(y => `<option value="${y}">Year ${y}</option>`).join('');
  $('#c-stream').innerHTML += state.streams.map(s => `<option>${s}</option>`).join('');
  $$('#plan-form .picker').forEach(initPicker);
  drawPlannerSeg(); drawCourses(); drawGpa(); renderAccount(); route();
})();
