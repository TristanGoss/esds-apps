/* Interactive budget forecast ("what if?") tool.
 *
 * The page is rendered with the forecast defaults and a first prediction already inlined as JSON, so
 * it shows numbers immediately. Changing any control re-requests a prediction from the backend (which
 * does the heavy scipy work); requests are throttled so a burst of edits sends at most one every 10s,
 * with a spinner while we wait. All uncertain figures are shown with their confidence range.
 */
(function () {
  'use strict';

  const DEFAULTS = JSON.parse(document.getElementById('forecast-defaults').textContent);
  const INITIAL = JSON.parse(document.getElementById('forecast-initial').textContent);

  // ---- Control panel definition. Values come from DEFAULTS (server-injected, private); the labels and
  //      the provenance help text live here (not sensitive). Types: money/number/int/toggle/percent. ----
  const SECTIONS = [
    { title: 'Calendar forecast', controls: [
      { k: 'forecast_ay', label: 'Academic year start', type: 'int', help: 'The September the forecast year begins (2026 = Sept 2026 to summer 2027).' },
      { k: 'n_tea_dances', label: 'Number of tea dances', type: 'int', help: 'Standalone Sunday tea dances in the year, spread evenly between the two fixed parties.' },
    ]},
    { title: 'Thursday classes', controls: [
      { k: 'price_class_disc', label: 'Class price, member/concession', type: 'money', help: 'Per-night class price for members and concessions.' },
      { k: 'price_class_ord', label: 'Class price, ordinary', type: 'money', help: 'Per-night class price for everyone else.' },
      { k: 'room_per_hour', label: 'Room hire per hour', type: 'money', help: 'Hourly hall-hire rate (LIFECARE), applied to every room-hour.' },
      { k: 'class_room_hour_a', label: 'Main room hours', type: 'number', help: 'Hours the main room is hired on a class night.' },
      { k: 'class_room_hour_b', label: 'Second room hours', type: 'number', help: 'Hours the second room is hired on a class night.' },
      { k: 'teacher_rate', label: 'Teacher pay per hour', type: 'money', help: 'Paid to each teacher per hour taught.' },
      { k: 'teacher_hours_a', label: 'Teacher 1 hours', type: 'number', help: 'Hours taught by the first teacher on a class night.' },
      { k: 'teacher_hours_b', label: 'Teacher 2 hours', type: 'number', help: 'Hours taught by the second teacher.' },
      { k: 'teacher_hours_c', label: 'Teacher 3 hours', type: 'number', help: 'Hours taught by the third teacher.' },
      { k: 'teacher_hours_d', label: 'Teacher 4 hours', type: 'number', help: 'Hours taught by the fourth teacher.' },
      { k: 'l1_per_night', label: 'Sell Level 1 night-by-night', type: 'toggle', help: 'If on, beginners pay per night (and the drop-off through the term reduces income) rather than buying a term block up front.' },
      { k: 'class_size_cap', label: 'Level 1 size cap (blank = none)', type: 'int', optional: true, help: 'Optional cap on paying Level 1 sign-ups per term. Leave blank for no cap.' },
      { k: 'loyalty_enabled', label: 'Level 2 loyalty scheme', type: 'toggle', help: 'If on, every Nth Level 2 ticket a dancer buys is refunded.' },
      { k: 'loyalty_every', label: 'Refund every Nth ticket', type: 'int', help: 'How many Level 2 tickets earn one free one.' },
    ]},
    { title: 'Socials', controls: [
      { k: 'price_social_disc', label: 'Social price, member/concession', type: 'money', help: 'Standalone social (tea dance / party) ticket for members and concessions.' },
      { k: 'price_social_ord', label: 'Social price, ordinary', type: 'money', help: 'Standalone social ticket for everyone else.' },
      { k: 'price_social_only_disc', label: 'Social-only price, member/conc.', type: 'money', help: 'Cheaper ticket for people who come to a class night for the social only, member/concession.' },
      { k: 'price_social_only_ord', label: 'Social-only price, ordinary', type: 'money', help: 'Social-only ticket, ordinary.' },
      { k: 'band_cost_mean', label: 'Typical band fee', type: 'money', help: 'Fee per band booking. Default is fitted from eight real 2023-25 band payments (mean about £672, inflation-adjusted).' },
      { k: 'social_snacks', label: 'Snacks per event', type: 'money', help: 'Snacks/refreshments provided at each social.' },
      { k: 'social_room_hours', label: 'Room hours per social', type: 'number', help: 'Hours the venue is hired for a standalone social.' },
    ]},
    { title: 'Weekender', controls: [
      { k: 'have_weekender', label: 'Run a weekender', type: 'toggle', help: 'Whether the annual weekender happens at all.' },
      { k: 'price_wk_full_disc', label: 'Full pass, member/concession', type: 'money', help: 'Weekender full pass for members and concessions.' },
      { k: 'price_wk_full_ord', label: 'Full pass, ordinary', type: 'money', help: 'Weekender full pass, ordinary. (Proposal — confirm.)' },
      { k: 'price_wk_day_disc', label: 'Day pass, member/concession', type: 'money', help: 'Weekender single-day pass, member/concession. (Proposal — confirm.)' },
      { k: 'price_wk_day_ord', label: 'Day pass, ordinary', type: 'money', help: 'Weekender single-day pass, ordinary. (Proposal — confirm.)' },
      { k: 'weekender_bands', label: 'Number of bands', type: 'int', help: 'Live bands booked across the weekend.' },
      { k: 'weekender_room_hours', label: 'Total room hours', type: 'number', help: 'All room-hire hours across the weekend.' },
      { k: 'weekender_teachers', label: 'Number of teachers', type: 'int', help: 'Visiting teachers brought in for the weekender.' },
      { k: 'weekender_teacher_rate', label: 'Teacher pay per hour', type: 'money', help: 'Hourly rate for a weekender teacher.' },
      { k: 'weekender_teacher_hours', label: 'Teacher hours each', type: 'number', help: 'Hours each weekender teacher works.' },
      { k: 'weekender_flight', label: 'Travel per teacher', type: 'money', help: 'Travel cost to bring in each teacher.' },
      { k: 'weekender_board_per_night', label: 'Board per teacher per night', type: 'money', help: 'Accommodation and board per teacher per night.' },
      { k: 'weekender_nights', label: 'Nights of board', type: 'int', help: 'Nights of accommodation per teacher.' },
    ]},
    { title: 'Volunteers, committee & accounts', controls: [
      { k: 'n_committee', label: 'Committee members', type: 'int', help: 'Committee members. Each holds a Google Workspace seat and attends the volunteer socials.' },
      { k: 'n_safer_spaces', label: 'Safer-spaces team', type: 'int', help: 'Safer-spaces volunteers with a Workspace seat, also at the volunteer socials.' },
      { k: 'n_extra_volunteers', label: 'Other volunteers', type: 'int', help: 'Further volunteers who attend the volunteer socials (no Workspace seat).' },
      { k: 'n_legacy_accounts', label: 'Legacy Workspace seats', type: 'int', help: 'Extra Workspace seats still billed but not attending the socials.' },
      { k: 'n_shared_accounts', label: 'Shared Workspace seats', type: 'int', help: 'Shared/role Workspace seats, billed but not attending the socials.' },
      { k: 'gsuite_seat_monthly', label: 'Workspace seat / month', type: 'money', help: 'Monthly Google Workspace charge per seat.' },
      { k: 'wix_reseller_annual', label: 'Wix reseller / year', type: 'money', help: 'Annual Wix reseller fee bundled into the Workspace bill.' },
      { k: 'volunteer_social_per_head', label: 'Volunteer social per head', type: 'money', help: 'Cost per head of each volunteer social (meal + bowling), held twice a year.' },
    ]},
    { title: 'Membership & overheads', controls: [
      { k: 'membership_fee', label: 'Membership fee', type: 'money', help: 'Annual membership fee. Member numbers are forecast from recent counts (see the models below).' },
      { k: 'current_balance', label: 'Opening bank balance', type: 'money', help: 'The current bank balance the forecast starts from.' },
      { k: 'oh_website', label: 'Website', type: 'money', help: 'Annual website hosting (Wix).' },
      { k: 'oh_email_marketing', label: 'Email marketing', type: 'money', help: 'Email-marketing subscription (billed every two years, halved to a yearly figure).' },
      { k: 'oh_insurance', label: 'Insurance', type: 'money', help: 'Public liability insurance, inflation-adjusted from the 2023 premium.' },
      { k: 'oh_storage_container', label: 'Storage', type: 'money', help: "ESDS's half of the shared storage container, per year." },
      { k: 'oh_spotify', label: 'Spotify', type: 'money', help: 'Music subscription, inflation-adjusted.' },
      { k: 'oh_survey_monkey', label: 'SurveyMonkey', type: 'money', help: 'Survey subscription, inflation-adjusted.' },
      { k: 'oh_pat_testing', label: 'PAT testing', type: 'money', help: 'Electrical (PAT) testing of society equipment.' },
      { k: 'oh_equipment_recap', label: 'Equipment', type: 'money', help: 'Amortised PA / equipment replacement (estimate).' },
      { k: 'oh_society_phone', label: 'Society phone', type: 'money', help: 'Society mobile / SIM (estimate).' },
      { k: 'oh_posters', label: 'Posters', type: 'money', help: 'Poster printing (part of volunteer reimbursements).' },
      { k: 'oh_stationery', label: 'Stationery', type: 'money', help: 'Stationery and materials (part of volunteer reimbursements).' },
      { k: 'oh_canva', label: 'Canva', type: 'money', help: 'Canva Pro subscription (estimate).' },
    ]},
    { title: 'Analysis', controls: [
      { k: 'confidence', label: 'Confidence level (%)', type: 'percent', help: 'Width of the ranges shown. 95% means the true figure lands in the range about 95 times in 100 if our assumptions hold.' },
    ]},
  ];

  const META = {};
  SECTIONS.forEach(s => s.controls.forEach(c => { META[c.k] = c; }));

  // ---- formatting ----
  const nf = new Intl.NumberFormat('en-GB');
  function money(v) {
    const r = Math.round(v);
    return (r < 0 ? '−£' : '£') + nf.format(Math.abs(r));
  }
  function signedMoney(v) {
    const r = Math.round(v);
    return (r < 0 ? '−£' : '+£') + nf.format(Math.abs(r));
  }

  // ---- build the control panel ----
  function buildControls() {
    const host = document.getElementById('controls');
    host.innerHTML = '';
    SECTIONS.forEach((section, idx) => {
      const details = document.createElement('details');
      details.className = 'control-section';
      if (idx < 2) details.open = true;
      const summary = document.createElement('summary');
      summary.textContent = section.title;
      details.appendChild(summary);
      const grid = document.createElement('div');
      grid.className = 'control-grid';
      section.controls.forEach(c => grid.appendChild(buildControl(c)));
      details.appendChild(grid);
      host.appendChild(details);
    });
  }

  function buildControl(c) {
    const wrap = document.createElement('label');
    wrap.className = 'control';
    const labelRow = document.createElement('span');
    labelRow.className = 'control-label';
    labelRow.textContent = c.label + ' ';
    const info = document.createElement('span');
    info.className = 'info-dot';
    info.textContent = '?';
    info.title = c.help;
    labelRow.appendChild(info);
    wrap.appendChild(labelRow);

    let input;
    const val = DEFAULTS[c.k];
    if (c.type === 'toggle') {
      input = document.createElement('input');
      input.type = 'checkbox';
      input.role = 'switch';
      input.checked = Boolean(val);
    } else {
      input = document.createElement('input');
      input.type = 'number';
      if (c.type === 'percent') { input.value = Math.round(Number(val) * 100); input.min = 50; input.max = 99; input.step = 1; }
      else if (c.type === 'int') { input.value = (val === null || val === undefined) ? '' : val; input.step = 1; if (!c.optional) input.min = 0; }
      else { input.value = val; input.step = c.type === 'money' ? 0.5 : 0.25; input.min = 0; }
      if (c.optional) input.placeholder = 'none';
    }
    input.id = 'ctl-' + c.k;
    input.addEventListener(c.type === 'toggle' ? 'change' : 'input', scheduleUpdate);
    wrap.appendChild(input);
    return wrap;
  }

  function collectParams() {
    const params = {};
    Object.keys(META).forEach(k => {
      const c = META[k];
      const el = document.getElementById('ctl-' + k);
      if (!el) return;
      if (c.type === 'toggle') params[k] = el.checked;
      else if (c.optional) params[k] = el.value === '' ? null : Number(el.value);
      else if (c.type === 'percent') params[k] = Number(el.value) / 100;
      else params[k] = Number(el.value);
    });
    return params;
  }

  // ---- throttled updates: at most one request every 10s, plus a short settle debounce ----
  let lastFire = 0, timer = null, inFlight = false;
  const THROTTLE_MS = 10000, SETTLE_MS = 800;

  function scheduleUpdate() {
    showSpinner('waiting to update…');
    if (timer) clearTimeout(timer);
    const wait = Math.max(SETTLE_MS, THROTTLE_MS - (Date.now() - lastFire));
    timer = setTimeout(runUpdate, wait);
  }

  async function runUpdate() {
    if (inFlight) { timer = setTimeout(runUpdate, 500); return; }  // wait for the in-flight one to finish
    lastFire = Date.now();
    inFlight = true;
    showSpinner('updating…');
    const params = collectParams();
    try {
      const resp = await fetch('/forecast/predict.json', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ params: params, confidence: params.confidence }),
      });
      if (resp.status === 401) { setStatus('Your session has expired — please reload the page to sign in again.', true); return; }
      if (!resp.ok) { setStatus('Could not update the forecast (the server is unavailable).', true); return; }
      render(await resp.json());
      setStatus('Updated.');
    } catch (e) {
      setStatus('Could not reach the server to update the forecast.', true);
    } finally {
      inFlight = false;
      hideSpinner();
    }
  }

  function showSpinner(msg) { document.getElementById('spinner').hidden = false; setStatus(msg || ''); }
  function hideSpinner() { document.getElementById('spinner').hidden = true; }
  function setStatus(msg, isError) {
    const el = document.getElementById('status-text');
    el.textContent = msg;
    el.classList.toggle('error', Boolean(isError));
  }

  // ---- rendering ----
  const charts = {};
  function chart(id) {
    if (!charts[id]) {
      charts[id] = echarts.init(document.getElementById(id));
      window.addEventListener('resize', () => charts[id].resize());
    }
    return charts[id];
  }

  function render(r) {
    renderHeadline(r);
    renderBudget(r);
    renderBalance(r);
    renderL1(r);
    renderSurvival(r);
    renderVariance(r);
    document.getElementById('loyalty-every-label').textContent = ordinal(r.detail.loyalty_every);
  }

  function ordinal(n) {
    const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  function renderHeadline(r) {
    const net = r.net, conf = Math.round(r.confidence * 100);
    const netEl = document.getElementById('hl-net');
    netEl.textContent = signedMoney(net.mean);
    netEl.className = 'headline-figure ' + (net.mean >= 0 ? 'good' : 'bad');
    document.getElementById('hl-net-ci').textContent =
      `${conf}% confidence: ${signedMoney(net.lo)} to ${signedMoney(net.hi)}`;

    const bal = r.year_end_balance;
    document.getElementById('hl-balance').textContent = money(bal.mean);
    document.getElementById('hl-balance-ci').textContent =
      `${conf}% confidence: ${money(bal.lo)} to ${money(bal.hi)}`;

    const pg = Math.round(r.p_green * 100);
    const pgEl = document.getElementById('hl-pgreen');
    pgEl.textContent = pg + '%';
    pgEl.className = 'headline-figure ' + (pg >= 50 ? 'good' : 'bad');
  }

  function renderBudget(r) {
    const rev = r.budget.filter(b => b.kind === 'revenue');
    const cost = r.budget.filter(b => b.kind === 'cost');
    const totRev = rev.reduce((a, b) => a + b.amount, 0);
    const totCost = cost.reduce((a, b) => a + b.amount, 0);
    const rows = [];
    rows.push('<tr class="group"><th colspan="2">Income</th></tr>');
    rev.forEach(b => rows.push(`<tr><td>${b.category}</td><td class="num">${money(b.amount)}</td></tr>`));
    rows.push(`<tr class="subtotal"><td>Total income</td><td class="num">${money(totRev)}</td></tr>`);
    rows.push('<tr class="group"><th colspan="2">Costs</th></tr>');
    cost.forEach(b => rows.push(`<tr><td>${b.category}</td><td class="num">−${money(b.amount)}</td></tr>`));
    rows.push(`<tr class="subtotal"><td>Total costs</td><td class="num">−${money(totCost)}</td></tr>`);
    const net = r.net;
    rows.push(`<tr class="net ${net.mean >= 0 ? 'good' : 'bad'}"><td>Year result</td><td class="num">${signedMoney(net.mean)}</td></tr>`);
    rows.push(`<tr class="net-ci"><td>${Math.round(r.confidence * 100)}% confidence range</td><td class="num">${signedMoney(net.lo)} to ${signedMoney(net.hi)}</td></tr>`);
    document.getElementById('budget-table').innerHTML =
      '<table class="budget"><tbody>' + rows.join('') + '</tbody></table>';
  }

  // A shaded confidence band = a transparent lower line + a stacked, filled "hi-lo" area on top.
  function band(lo, hi, colour) {
    return [
      { type: 'line', data: lo, stack: 'ci', symbol: 'none', lineStyle: { opacity: 0 }, silent: true, z: 1 },
      { type: 'line', name: 'confidence band', data: hi.map((h, i) => h - lo[i]), stack: 'ci', symbol: 'none',
        lineStyle: { opacity: 0 }, areaStyle: { color: colour, opacity: 0.25 }, silent: true, z: 1 },
    ];
  }

  function renderBalance(r) {
    const b = r.balance;
    const c = chart('balance-chart');
    c.setOption({
      grid: { left: 64, right: 20, top: 30, bottom: 40 },
      tooltip: { trigger: 'axis', valueFormatter: v => money(v) },
      xAxis: { type: 'category', data: b.dates, boundaryGap: false, axisLabel: { formatter: v => v.slice(0, 7) } },
      yAxis: { type: 'value', scale: true, axisLabel: { formatter: v => money(v) } },
      series: [
        ...band(b.lo, b.hi, '#1f77b4'),
        { type: 'line', name: 'balance', data: b.mean, symbol: 'none', lineWidth: 2.5,
          lineStyle: { color: '#1f77b4' }, z: 3,
          markLine: { silent: true, symbol: 'none', lineStyle: { type: 'dotted', color: '#888' },
            data: [{ yAxis: b.opening, label: { formatter: 'opening', position: 'insideEndTop' } }] } },
      ],
    }, true);
  }

  function renderL1(r) {
    const d = r.detail.l1_signups;
    const terms = d.map(x => 'T' + x.term);
    const hist = r.detail.l1_history.map(h => [h.pos - 1, h.interest]);
    const c = chart('l1-chart');
    c.setOption({
      grid: { left: 48, right: 20, top: 30, bottom: 36 },
      tooltip: { trigger: 'axis' },
      legend: { data: ['forecast', 'past terms'], top: 0 },
      xAxis: { type: 'category', data: terms, name: 'term' },
      yAxis: { type: 'value', scale: true, name: 'sign-ups' },
      series: [
        ...band(d.map(x => x.lo), d.map(x => x.hi), '#2ca02c'),
        { type: 'line', name: 'forecast', data: d.map(x => x.mean), symbol: 'circle', symbolSize: 7,
          lineStyle: { color: '#2ca02c' }, itemStyle: { color: '#2ca02c' }, z: 3 },
        { type: 'scatter', name: 'past terms', data: hist, symbolSize: 6,
          itemStyle: { color: 'rgba(120,120,120,0.55)' }, z: 2 },
      ],
    }, true);
  }

  function renderSurvival(r) {
    const s = r.detail.l2_survival;
    const every = r.detail.loyalty_every;
    const marks = [];
    for (let m = every; m <= s.k[s.k.length - 1]; m += every) marks.push({ xAxis: m - 1 });
    const c = chart('survival-chart');
    c.setOption({
      grid: { left: 48, right: 20, top: 30, bottom: 36 },
      tooltip: { trigger: 'axis', valueFormatter: v => (v * 100).toFixed(0) + '%' },
      legend: { data: ['actual', 'fitted'], top: 0 },
      xAxis: { type: 'category', data: s.k, name: 'nights attended' },
      yAxis: { type: 'value', min: 0, max: 1, name: 'share attending ≥ this', axisLabel: { formatter: v => (v * 100) + '%' } },
      series: [
        { type: 'scatter', name: 'actual', data: s.empirical, symbolSize: 7, itemStyle: { color: '#1f77b4' } },
        { type: 'line', name: 'fitted', data: s.fit, symbol: 'none', lineStyle: { color: '#d62728', type: 'dashed' },
          markLine: marks.length ? { silent: true, symbol: 'none', lineStyle: { color: '#bbb', type: 'dotted' },
            label: { show: false }, data: marks } : undefined },
      ],
    }, true);
  }

  function renderVariance(r) {
    const c = r.contributors.slice().reverse();
    const ch = chart('variance-chart');
    ch.setOption({
      grid: { left: 140, right: 30, top: 20, bottom: 36 },
      tooltip: { trigger: 'axis', valueFormatter: v => v + '%' },
      xAxis: { type: 'value', name: '% of the spread', axisLabel: { formatter: '{value}%' } },
      yAxis: { type: 'category', data: c.map(x => x.name) },
      series: [{ type: 'bar', data: c.map(x => x.pct), itemStyle: { color: '#1f77b4' },
        label: { show: true, position: 'right', formatter: '{c}%' } }],
    }, true);
  }

  // ---- reset & download ----
  function resetToDefaults() {
    SECTIONS.forEach(s => s.controls.forEach(c => {
      const el = document.getElementById('ctl-' + c.k);
      const val = DEFAULTS[c.k];
      if (c.type === 'toggle') el.checked = Boolean(val);
      else if (c.type === 'percent') el.value = Math.round(Number(val) * 100);
      else el.value = (val === null || val === undefined) ? '' : val;
    }));
    lastFire = 0;
    runUpdate();
  }

  async function downloadScenario() {
    const params = collectParams();
    setStatus('Preparing download…');
    try {
      const resp = await fetch('/forecast/download.xlsx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ params: params, confidence: params.confidence }),
      });
      if (!resp.ok) { setStatus('Could not generate the download.', true); return; }
      const blob = await resp.blob();
      const dispo = resp.headers.get('Content-Disposition') || '';
      const m = dispo.match(/filename=([^;]+)/);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = m ? m[1].trim() : 'esds_forecast.xlsx';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setStatus('Downloaded.');
    } catch (e) {
      setStatus('Could not generate the download.', true);
    }
  }

  // ---- init ----
  buildControls();
  render(INITIAL);
  setStatus('Showing the forecast defaults. Change any control to explore.');
  document.getElementById('reset-btn').addEventListener('click', resetToDefaults);
  document.getElementById('download-btn').addEventListener('click', downloadScenario);
})();
