/* Interactive budget forecast ("what if?") tool.
 *
 * The page is rendered with the forecast defaults and a first prediction already inlined as JSON, so
 * it shows numbers immediately. Changing any control re-requests a prediction from the backend (which
 * does the heavy scipy work); requests are throttled so a burst of edits sends at most one every 3s,
 * with a spinner while we wait. All uncertain figures are shown with their confidence range.
 */
(function () {
  'use strict';

  const DEFAULTS = JSON.parse(document.getElementById('forecast-defaults').textContent);
  const INITIAL = JSON.parse(document.getElementById('forecast-initial').textContent);

  const THIS_YEAR = new Date().getFullYear();

  // ---- Control panel definition. Values come from DEFAULTS (server-injected, private); the labels and
  //      the provenance help text live here (not sensitive). Types: money/number/int/toggle/percent.
  //      Optional min/max/int bound each input; integers and non-negatives are enforced on entry. ----
  const SECTIONS = [
    { title: 'Calendar forecast', controls: [
      { k: 'forecast_ay', label: 'Academic year start', type: 'int', min: 2020, max: THIS_YEAR + 5, help: 'The September the forecast year begins (2026 = Sept 2026 to summer 2027).' },
    ]},
    { title: 'Thursday classes', controls: [
      { k: 'price_class_disc', label: 'Class price, member/concession', type: 'money', help: 'Per-night class price for members and concessions.' },
      { k: 'price_class_ord', label: 'Class price, ordinary', type: 'money', help: 'Per-night class price for everyone else.' },
      { k: 'room_per_hour', label: 'Room hire per hour', type: 'money', help: 'Hourly hall-hire rate (LIFECARE), applied to every room-hour.' },
      { k: 'class_room_hour_a', label: 'Main room hours', type: 'number', max: 24, help: 'Hours the main room is hired on a class night.' },
      { k: 'class_room_hour_b', label: 'Second room hours', type: 'number', max: 24, help: 'Hours the second room is hired on a class night.' },
      { k: 'teacher_rate', label: 'Teacher pay per hour', type: 'money', help: 'Paid to each teacher per hour taught.' },
      { k: 'teacher_hours_a', label: 'Teacher 1 hours', type: 'number', max: 24, help: 'Hours taught by the first teacher on a class night.' },
      { k: 'teacher_hours_b', label: 'Teacher 2 hours', type: 'number', max: 24, help: 'Hours taught by the second teacher.' },
      { k: 'teacher_hours_c', label: 'Teacher 3 hours', type: 'number', max: 24, help: 'Hours taught by the third teacher.' },
      { k: 'teacher_hours_d', label: 'Teacher 4 hours', type: 'number', max: 24, help: 'Hours taught by the fourth teacher.' },
      { k: 'l1_per_night', label: 'Sell Level 1 night-by-night', type: 'toggle', help: 'If on, beginners pay per night (and the drop-off through the term reduces income) rather than buying a term block up front.' },
      { k: 'class_size_cap', label: 'Thursday night single class size cap', type: 'int', optional: true, min: 1, max: 500, help: 'Cap on paying Level 1 sign-ups in a single Thursday-night class. Leave blank for no cap.' },
      { k: 'loyalty_enabled', label: 'Level 2 loyalty scheme', type: 'toggle', help: 'If on, every Nth Level 2 ticket a dancer buys is refunded.' },
      { k: 'loyalty_every', label: 'Refund every Nth ticket', type: 'int', min: 1, max: 100, help: 'How many Level 2 tickets earn one free one.' },
    ]},
    { title: 'Socials', controls: [
      { k: 'n_tea_dances', label: 'Number of tea dances', type: 'int', min: 0, max: 20, help: 'Standalone Sunday tea dances in the year, spread evenly between the two fixed parties.' },
      { k: 'price_social_disc', label: 'Social price, member/concession', type: 'money', help: 'Standalone social (tea dance / party) ticket for members and concessions.' },
      { k: 'price_social_ord', label: 'Social price, ordinary', type: 'money', help: 'Standalone social ticket for everyone else.' },
      { k: 'price_social_only_disc', label: 'Social-only price, member/conc.', type: 'money', help: 'Cheaper ticket for people who come to a class night for the social only; member/concession.' },
      { k: 'price_social_only_ord', label: 'Social-only price, ordinary', type: 'money', help: 'Social-only ticket; ordinary.' },
      { k: 'band_cost_mean', label: 'Typical band fee (mean)', type: 'money', help: 'Average fee per band booking. Default is fitted from eight real 2023-25 band payments (mean about £717), inflation-adjusted.' },
      { k: 'band_cost_std', label: 'Band fee: standard deviation', type: 'money', help: 'How much band fees vary around the mean. Feeds the uncertainty band. Default is the spread of the same eight real payments (about £97).' },
      { k: 'social_snacks', label: 'Snacks per event', type: 'money', help: 'Snacks/refreshments provided at each social.' },
      { k: 'social_room_hours', label: 'Room hours per social', type: 'number', max: 24, help: 'Hours the venue is hired for a standalone social.' },
    ]},
    { title: 'Weekender', controls: [
      { k: 'have_weekender', label: 'Run a weekender', type: 'toggle', help: 'Whether the annual weekender happens at all.' },
      { k: 'price_wk_full_disc', label: 'Full pass, member/concession', type: 'money', help: 'Weekender full pass (all classes and socials) for members and concessions.' },
      { k: 'price_wk_full_ord', label: 'Full pass, ordinary', type: 'money', help: 'Weekender full pass, ordinary. (Proposal; confirm.)' },
      { k: 'price_wk_day_disc', label: 'Day pass, member/concession', type: 'money', help: 'Weekender single-day pass (one day of classes plus that evening), member/concession. (Proposal; confirm.)' },
      { k: 'price_wk_day_ord', label: 'Day pass, ordinary', type: 'money', help: 'Weekender single-day pass, ordinary. (Proposal; confirm.)' },
      { k: 'price_wk_social', label: 'Social pass (evenings only)', type: 'money', help: 'One flat price covering all the weekend evening socials but no classes. The share of the audience who buy this is inferred from the 30th-anniversary weekender, where the socials drew far more people than the classes.' },
      { k: 'weekender_bands', label: 'Number of bands', type: 'int', min: 0, max: 20, help: 'Live bands booked across the weekend.' },
      { k: 'weekender_room_hours', label: 'Total room hours', type: 'number', max: 200, help: 'All room-hire hours across the weekend.' },
      { k: 'weekender_teachers', label: 'Number of teachers', type: 'int', min: 0, max: 20, help: 'Visiting teachers brought in for the weekender.' },
      { k: 'weekender_teacher_rate', label: 'Teacher pay per hour', type: 'money', help: 'Hourly rate for a weekender teacher.' },
      { k: 'weekender_teacher_hours', label: 'Teacher hours each', type: 'number', max: 60, help: 'Hours each weekender teacher works.' },
      { k: 'weekender_flight', label: 'Travel per teacher', type: 'money', help: 'Travel cost to bring in each teacher.' },
      { k: 'weekender_board_per_night', label: 'Board per teacher per night', type: 'money', help: 'Accommodation and board per teacher per night.' },
      { k: 'weekender_nights', label: 'Nights of board', type: 'int', min: 0, max: 14, help: 'Nights of accommodation per teacher.' },
    ]},
    { title: 'Volunteers, committee & accounts', controls: [
      { k: 'n_committee', label: 'Committee members', type: 'int', min: 0, max: 100, help: 'Committee members. Each holds a Google Workspace seat and attends the volunteer socials.' },
      { k: 'n_safer_spaces', label: 'Safer-spaces team', type: 'int', min: 0, max: 100, help: 'Safer-spaces volunteers with a Workspace seat, also at the volunteer socials. Count only those not already counted as committee members; anyone on both teams is counted once, under committee.' },
      { k: 'n_extra_volunteers', label: 'Other volunteers', type: 'int', min: 0, max: 200, help: 'Further volunteers who attend the volunteer socials (no Workspace seat).' },
      { k: 'n_volunteer_socials', label: 'Volunteer socials per year', type: 'int', min: 0, max: 20, help: 'Number of volunteer thank-you socials (meal + bowling) held in the year.' },
      { k: 'n_legacy_accounts', label: 'Legacy Workspace seats', type: 'int', min: 0, max: 100, help: 'Extra Workspace seats still billed but not attending the socials.' },
      { k: 'n_shared_accounts', label: 'Devices Workspace seats', type: 'int', min: 0, max: 100, help: 'Workspace seats for shared devices / role accounts, billed but not attending the socials.' },
      { k: 'gsuite_seat_monthly', label: 'Workspace seat / month', type: 'money', help: 'Monthly Google Workspace charge per seat.' },
      { k: 'wix_reseller_annual', label: 'Wix reseller / year', type: 'money', help: 'Annual Wix reseller fee bundled into the Workspace bill.' },
      { k: 'volunteer_social_per_head', label: 'Volunteer social per head', type: 'money', help: 'Cost per head of each volunteer social (meal + bowling).' },
    ]},
    { title: 'Membership & overheads', controls: [
      { k: 'membership_fee', label: 'Membership fee', type: 'money', help: 'Annual membership fee.' },
      { k: 'n_members', label: 'Forecast members (mean)', type: 'int', min: 0, max: 1000, help: 'Expected paid members. Default is the mean of the 2024-26 counts (64, 85, 66).' },
      { k: 'membership_std', label: 'Members: standard deviation', type: 'number', max: 1000, help: 'How much member numbers vary year to year. Feeds the uncertainty band. Default is the spread of the same three counts (about 12).' },
      { k: 'current_balance', label: 'Opening bank balance', type: 'money', max: 1e7, help: 'The current bank balance the forecast starts from.' },
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
      { k: 'confidence', label: 'Confidence level (%)', type: 'percent', min: 50, max: 99, help: 'Width of the ranges shown. 95% means the true figure lands in the range about 95 times in 100 if our assumptions hold.' },
    ]},
  ];

  const META = {};
  SECTIONS.forEach(s => s.controls.forEach(c => { META[c.k] = c; }));

  // Numeric bounds for a control (defaults: non-negative; integers where the type says so).
  function bounds(c) {
    const isInt = c.type === 'int';
    let min = c.min;
    if (min === undefined) min = (c.type === 'percent') ? 50 : 0;
    let max = c.max;
    if (max === undefined) max = (c.type === 'percent') ? 99 : Infinity;
    return { min: min, max: max, int: isInt };
  }

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
  const dateFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  function prettyDate(iso) { return dateFmt.format(new Date(iso + 'T00:00:00')); }

  // ---- build the control panel ----
  function buildControls() {
    const host = document.getElementById('controls');
    host.innerHTML = '';
    SECTIONS.forEach(section => {
      const details = document.createElement('details');
      details.className = 'control-section';  // all sections start collapsed; the viewer opens what they need
      const summary = document.createElement('summary');
      summary.textContent = section.title;
      details.appendChild(summary);

      // Per-section toolbar: a reset button and a spinner near the top, so an edit's effect is visible
      // without scrolling back up.
      const toolbar = document.createElement('div');
      toolbar.className = 'section-toolbar';
      const reset = document.createElement('button');
      reset.type = 'button';
      reset.className = 'secondary outline section-reset';
      reset.textContent = 'Reset section to best guess';
      reset.addEventListener('click', () => resetSection(section));
      const spin = document.createElement('span');
      spin.className = 'spinner';
      spin.hidden = true;
      const stat = document.createElement('span');
      stat.className = 'section-status muted';
      toolbar.appendChild(reset);
      toolbar.appendChild(spin);
      toolbar.appendChild(stat);
      details.appendChild(toolbar);

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
    const b = bounds(c);
    if (c.type === 'toggle') {
      input = document.createElement('input');
      input.type = 'checkbox';
      input.role = 'switch';
      input.checked = Boolean(val);
    } else {
      input = document.createElement('input');
      input.type = 'number';
      if (c.type === 'percent') { input.value = Math.round(Number(val) * 100); input.step = 1; }
      else if (c.type === 'int') { input.value = (val === null || val === undefined) ? '' : val; input.step = 1; }
      else { input.value = val; input.step = c.type === 'money' ? 0.5 : 0.25; }
      if (Number.isFinite(b.min)) input.min = b.min;
      if (Number.isFinite(b.max)) input.max = b.max;
      if (c.optional) input.placeholder = 'none';
    }
    input.id = 'ctl-' + c.k;
    input.addEventListener(c.type === 'toggle' ? 'change' : 'input', scheduleUpdate);
    input.addEventListener('blur', () => { if (c.type !== 'toggle') { clampField(c); } });
    wrap.appendChild(input);
    return wrap;
  }

  // Clamp a single field into its bounds and (for integers) round it, writing the corrected value back
  // so the box always shows a legal number.
  function clampField(c) {
    const el = document.getElementById('ctl-' + c.k);
    if (!el || el.value === '') return;
    const b = bounds(c);
    let v = Number(el.value);
    if (!Number.isFinite(v)) { el.value = ''; return; }
    if (b.int) v = Math.round(v);
    v = Math.min(b.max, Math.max(b.min, v));
    el.value = v;
  }

  // Read every control, clamping to bounds. Returns { params, ok }: ok is false if a required box is
  // empty or non-numeric (we then skip the request rather than send rubbish).
  function collectParams() {
    const params = {};
    let ok = true;
    Object.keys(META).forEach(k => {
      const c = META[k];
      const el = document.getElementById('ctl-' + k);
      if (!el) return;
      if (c.type === 'toggle') { params[k] = el.checked; return; }
      const b = bounds(c);
      if (el.value === '') {
        if (c.optional) { params[k] = null; el.classList.remove('invalid'); }
        else { ok = false; el.classList.add('invalid'); }
        return;
      }
      let v = Number(el.value);
      if (!Number.isFinite(v)) { ok = false; el.classList.add('invalid'); return; }
      el.classList.remove('invalid');
      if (b.int) v = Math.round(v);
      v = Math.min(b.max, Math.max(b.min, v));
      params[k] = c.type === 'percent' ? v / 100 : v;
    });
    return { params: params, ok: ok };
  }

  // ---- throttled updates: at most one request every 10s, plus a short settle debounce ----
  let lastFire = 0, timer = null, inFlight = false;
  const THROTTLE_MS = 3000, SETTLE_MS = 800;

  function scheduleUpdate() {
    showSpinner('waiting to update…');
    if (timer) clearTimeout(timer);
    const wait = Math.max(SETTLE_MS, THROTTLE_MS - (Date.now() - lastFire));
    timer = setTimeout(runUpdate, wait);
  }

  async function runUpdate() {
    if (inFlight) { timer = setTimeout(runUpdate, 500); return; }  // wait for the in-flight one to finish
    const collected = collectParams();
    if (!collected.ok) { setStatus('Some values are missing or invalid — please correct the highlighted boxes.', true); hideSpinner(); return; }
    lastFire = Date.now();
    inFlight = true;
    showSpinner('updating…');
    try {
      const resp = await fetch('/forecast/predict.json', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ params: collected.params, confidence: collected.params.confidence }),
      });
      if (resp.status === 401) { setStatus('Your session has expired: please reload the page to sign in again.', true); return; }
      if (!resp.ok) { setStatus('Could not update the forecast (the server is unavailable).', true); return; }
      render(await resp.json());
      setStatus('Forecast updated for latest inputs.');
    } catch (e) {
      setStatus('Could not reach the server to update the forecast.', true);
    } finally {
      inFlight = false;
      hideSpinner();
    }
  }

  function showSpinner(msg) {
    document.querySelectorAll('.spinner').forEach(s => { s.hidden = false; });
    setStatus(msg || '');
  }
  function hideSpinner() { document.querySelectorAll('.spinner').forEach(s => { s.hidden = true; }); }
  function setStatus(msg, isError) {
    // Mirror the message into the global status bar and every section toolbar, so a viewer editing a
    // control sees what's happening right next to that section's spinner.
    document.querySelectorAll('#status-text, .section-status').forEach(el => {
      el.textContent = msg;
      el.classList.toggle('error', Boolean(isError));
    });
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
    renderCalendarChart(r);
    renderCalendar(r);
    renderL1(r);
    renderAttendance(r);
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
    // Custom tooltip so it names the forecast line and its confidence range, instead of the raw stacked
    // band series (whose first component has no meaningful label).
    const tip = params => {
      if (!params.length) return '';
      const iso = params[0].axisValue;
      const idx = b.dates.indexOf(iso);
      const lines = [prettyDate(iso), `Forecast: ${money(b.mean[idx])}`];
      lines.push(`Range: ${money(b.lo[idx])} to ${money(b.hi[idx])}`);
      return lines.join('<br>');
    };
    c.setOption({
      grid: { left: 64, right: 20, top: 30, bottom: 40 },
      tooltip: { trigger: 'axis', formatter: tip },
      xAxis: { type: 'category', data: b.dates, boundaryGap: false, axisLabel: { formatter: v => v.slice(0, 7) } },
      yAxis: { type: 'value', scale: true, axisLabel: { formatter: v => money(v) } },
      series: [
        ...band(b.lo, b.hi, '#1f77b4'),
        { type: 'line', name: 'Forecast', data: b.mean, symbol: 'none', lineWidth: 2.5,
          lineStyle: { color: '#1f77b4' }, z: 3,
          markLine: { silent: true, symbol: 'none', lineStyle: { type: 'dotted', color: '#888' },
            data: [{ yAxis: b.opening, label: { formatter: 'opening', position: 'insideEndTop' } }] } },
      ],
    }, true);
  }

  // A timeline of the generated year: shaded term bands with weekly class-night ticks, and the socials
  // as labelled markers — the same picture the notebook draws, in ECharts.
  function renderCalendarChart(r) {
    const cal = r.calendar;
    if (!cal) return;
    const DAY = 86400000;
    const ms = iso => new Date(iso + 'T00:00:00').getTime();
    const ticks = [];
    cal.terms.forEach(t => {
      for (let d = ms(t.start); d <= ms(t.end) + DAY; d += 7 * DAY) ticks.push([d, 0.5]);
    });
    const shades = ['rgba(31,119,180,0.07)', 'rgba(31,119,180,0.16)'];
    const areas = cal.terms.map((t, i) => [
      { xAxis: ms(t.start) - 3 * DAY, itemStyle: { color: shades[i % 2] },
        label: { show: true, position: 'insideTop', formatter: 'T' + t.term, color: '#8a8a8a', fontSize: 11 } },
      { xAxis: ms(t.end) + 3 * DAY },
    ]);
    const kindColour = { 'Christmas party': '#d62728', 'End-of-year party': '#d62728', Weekender: '#9467bd' };
    const socials = cal.socials.map(s => ({
      value: [ms(s.date), 0.5], label: s.label,
      itemStyle: { color: kindColour[s.label] || '#1f77b4' },
    }));
    const ends = cal.terms.map(t => ms(t.end)).concat(cal.socials.map(s => ms(s.date)));
    const tipDate = v => new Date(v).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
    const c = chart('calendar-chart');
    c.setOption({
      grid: { left: 12, right: 24, top: 46, bottom: 24, containLabel: true },
      tooltip: { trigger: 'item',
        formatter: p => (p.data && p.data.label ? p.data.label : 'Class night') + '<br>' + tipDate(p.value[0]) },
      xAxis: { type: 'time', min: ms(cal.first_class) - 10 * DAY, max: Math.max(...ends) + 10 * DAY },
      yAxis: { type: 'value', min: 0, max: 1, show: false },
      series: [
        { type: 'scatter', name: 'class night', symbol: 'rect', symbolSize: [2, 20], itemStyle: { color: '#555' },
          data: ticks, silent: true, markArea: { silent: true, data: areas } },
        { type: 'scatter', name: 'social', symbolSize: 13, data: socials, z: 5,
          label: { show: true, position: 'top', rotate: 26, align: 'left', distance: 6,
            formatter: p => p.data.label, fontSize: 10, color: '#555' } },
      ],
    }, true);
  }

  function renderCalendar(r) {
    const cal = r.calendar;
    if (!cal) return;
    const rows = [];
    rows.push('<tr class="group"><th>Teaching term</th><th>Starts</th><th>Ends</th><th class="num">Weeks</th></tr>');
    cal.terms.forEach(t => rows.push(
      `<tr><td>Term ${t.term}</td><td>${prettyDate(t.start)}</td><td>${prettyDate(t.end)}</td><td class="num">${t.weeks}</td></tr>`));
    rows.push('<tr class="group"><th colspan="4">Socials &amp; parties</th></tr>');
    cal.socials.forEach(s => rows.push(
      `<tr><td>${s.label}</td><td colspan="3">${prettyDate(s.date)}</td></tr>`));
    document.getElementById('calendar-table').innerHTML =
      `<p class="muted">Academic year starting September ${cal.ay}: ${cal.n_class_nights} class nights across six terms, ` +
      `first class ${prettyDate(cal.first_class)}.</p>` +
      '<table class="budget calendar"><tbody>' + rows.join('') + '</tbody></table>';
  }

  function renderL1(r) {
    const d = r.detail.l1_signups;
    const terms = d.map(x => 'T' + x.term);
    const histRaw = r.detail.l1_history;
    const histPts = histRaw.map(h => [h.pos - 1, h.interest]);
    const conf = Math.round(r.confidence * 100);
    // Custom tooltip so it names the forecast, its range and the past terms — not the raw stacked band.
    const tip = params => {
      if (!params.length) return '';
      const idx = terms.indexOf(params[0].axisValue);
      if (idx < 0) return '';
      const x = d[idx];
      const past = histRaw.filter(h => h.pos === x.term).map(h => Math.round(h.interest));
      let s = `Term ${x.term}<br>Forecast: <b>${Math.round(x.mean)}</b> sign-ups<br>` +
        `${conf}% range: ${Math.round(x.lo)} to ${Math.round(x.hi)}`;
      if (past.length) s += `<br>past terms: ${past.join(', ')}`;
      return s;
    };
    const c = chart('l1-chart');
    c.setOption({
      grid: { left: 48, right: 20, top: 30, bottom: 36 },
      tooltip: { trigger: 'axis', formatter: tip },
      legend: { data: ['forecast', 'past terms'], top: 0 },
      xAxis: { type: 'category', data: terms, name: 'term' },
      yAxis: { type: 'value', scale: true },
      series: [
        ...band(d.map(x => x.lo), d.map(x => x.hi), '#2ca02c'),
        { type: 'line', name: 'forecast', data: d.map(x => x.mean), symbol: 'circle', symbolSize: 7,
          lineStyle: { color: '#2ca02c' }, itemStyle: { color: '#2ca02c' }, z: 3 },
        { type: 'scatter', name: 'past terms', data: histPts, symbolSize: 6,
          itemStyle: { color: 'rgba(120,120,120,0.55)' }, z: 2 },
      ],
    }, true);
  }

  // The key head-count inputs (Level 2, socials, weekender, members) as horizontal bars with 95% whiskers.
  const ATTEND_LABELS = {
    'Level 2 / night': 'Level 2 class (per night)',
    'social attendance': 'Standalone social (per event)',
    'Christmas party': 'Christmas party (per event)',
    'social-only / night': 'Thursday social-only (per night)',
    'weekender audience': 'Weekender recurring audience',
    members: 'Paid members (per year)',
  };

  // renderItem for a horizontal error-bar whisker (low–high line with end caps) on a category row.
  function whiskerItem(params, api) {
    const cat = api.value(0);
    const loPt = api.coord([api.value(1), cat]);
    const hiPt = api.coord([api.value(2), cat]);
    const y = loPt[1];
    const cap = 5;
    const style = { stroke: '#333', lineWidth: 1.5 };
    return {
      type: 'group',
      children: [
        { type: 'line', shape: { x1: loPt[0], y1: y, x2: hiPt[0], y2: y }, style: style },
        { type: 'line', shape: { x1: loPt[0], y1: y - cap, x2: loPt[0], y2: y + cap }, style: style },
        { type: 'line', shape: { x1: hiPt[0], y1: y - cap, x2: hiPt[0], y2: y + cap }, style: style },
      ],
    };
  }

  function renderAttendance(r) {
    const rows = (r.detail.inputs || [])
      .filter(x => ATTEND_LABELS[x.name])
      .map(x => ({ label: ATTEND_LABELS[x.name], mean: x.mean, lo: x.lo, hi: x.hi }))
      .sort((a, b) => a.mean - b.mean);  // smallest at the bottom, largest on top
    const cats = rows.map(x => x.label);
    const bars = rows.map(x => Math.round(x.mean * 10) / 10);
    const whiskers = rows.map((x, i) => [i, x.lo, x.hi]);
    const conf = Math.round(r.confidence * 100);
    const c = chart('attendance-chart');
    c.setOption({
      grid: { left: 200, right: 60, top: 16, bottom: 36 },
      tooltip: { trigger: 'item',
        formatter: p => {
          const x = rows[p.dataIndex];
          return `${x.label}<br>best guess: <b>${bars[p.dataIndex]}</b><br>${conf}% range: ${Math.round(x.lo)} to ${Math.round(x.hi)}`;
        } },
      xAxis: { type: 'value', min: 0, name: 'heads', nameLocation: 'middle', nameGap: 24 },
      yAxis: { type: 'category', data: cats, axisLabel: { interval: 0, width: 190, overflow: 'break' } },
      series: [
        { type: 'bar', data: bars, barWidth: '55%', itemStyle: { color: '#2ca02c' },
          label: { show: true, position: 'right', formatter: '{c}' }, z: 1 },
        { type: 'custom', renderItem: whiskerItem, data: whiskers, silent: true, z: 2,
          encode: { x: [1, 2], y: 0 } },
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
      yAxis: { type: 'value', min: 0, max: 1, axisLabel: { formatter: v => (v * 100) + '%' } },
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
      grid: { left: 210, right: 40, top: 20, bottom: 36 },
      tooltip: { trigger: 'axis', valueFormatter: v => v + '%' },
      xAxis: { type: 'value', name: '% of the spread', axisLabel: { formatter: '{value}%' } },
      yAxis: { type: 'category', data: c.map(x => x.name), axisLabel: { interval: 0, width: 195, overflow: 'break' } },
      series: [{ type: 'bar', data: c.map(x => x.pct), itemStyle: { color: '#1f77b4' },
        label: { show: true, position: 'right', formatter: '{c}%' } }],
    }, true);
  }

  // ---- reset & download ----
  function setControlToDefault(c) {
    const el = document.getElementById('ctl-' + c.k);
    if (!el) return;
    const val = DEFAULTS[c.k];
    el.classList.remove('invalid');
    if (c.type === 'toggle') el.checked = Boolean(val);
    else if (c.type === 'percent') el.value = Math.round(Number(val) * 100);
    else el.value = (val === null || val === undefined) ? '' : val;
  }

  function resetToDefaults() {
    SECTIONS.forEach(s => s.controls.forEach(setControlToDefault));
    lastFire = 0;
    runUpdate();
  }

  function resetSection(section) {
    section.controls.forEach(setControlToDefault);
    lastFire = 0;
    runUpdate();
  }

  async function downloadScenario() {
    const collected = collectParams();
    if (!collected.ok) { setStatus('Please correct the highlighted boxes before downloading.', true); return; }
    setStatus('Preparing download…');
    try {
      const resp = await fetch('/forecast/download.xlsx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ params: collected.params, confidence: collected.params.confidence }),
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
  const setAllSections = open => document.querySelectorAll('details.control-section').forEach(d => { d.open = open; });
  document.getElementById('expand-btn').addEventListener('click', () => setAllSections(true));
  document.getElementById('collapse-btn').addEventListener('click', () => setAllSections(false));
})();
