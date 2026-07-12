// Renders the per-term summary charts on /attendance with ECharts, from the datasets served by
// /attendance/summaries.json (built server-side in attendance/analysis.py, itself a port of the
// matplotlib charts in working/attendance.ipynb). Five charts:
//   1. beginner (Level 1) intake per term, one line per academic year (solid attended / dashed registered + waitlist);
//   2. Level 2 class attendance per term with the paired social-only turnout (solid / dashed);
//   3. cohort-retention heatmap (teaching team shown in the cell tooltip);
//   4. the 2026 community survival curve (with / without the 30th anniversary);
//   5. termly active-community counts since 2026 (active >= 1 / regulars >= 2, incl / excl the 30th).

// Matplotlib's turbo at 17 evenly spaced stops, interpolated in RGB below. The per-year charts
// colour each academic year from this ramp (see yearColour); turbo is used over viridis because it
// stays discriminable across its whole range, so adjacent years don't blur together.
const TURBO = [
  '#30123b', '#4040a2', '#466be3', '#4294ff', '#28bceb', '#18ddc2', '#32f298', '#6dfe62', '#a4fc3c',
  '#cdec34', '#eecf3a', '#fdac34', '#fb7e21', '#eb500e', '#d02f05', '#a91601', '#7a0403',
];

function _lerpHex(a, b, t) {
  const ai = parseInt(a.slice(1), 16);
  const bi = parseInt(b.slice(1), 16);
  const r = Math.round((ai >> 16) + (((bi >> 16) - (ai >> 16)) * t));
  const g = Math.round(((ai >> 8) & 255) + ((((bi >> 8) & 255) - ((ai >> 8) & 255)) * t));
  const bl = Math.round((ai & 255) + (((bi & 255) - (ai & 255)) * t));
  return `rgb(${r}, ${g}, ${bl})`;
}

// Sample the turbo ramp at s in [0, 1].
function turbo(s) {
  const clamped = Math.max(0, Math.min(1, s));
  const scaled = clamped * (TURBO.length - 1);
  const i = Math.min(Math.floor(scaled), TURBO.length - 2);
  return _lerpHex(TURBO[i], TURBO[i + 1], scaled - i);
}

// Position each academic year on [0, 1] for the colour ramp. Years are ranked densely (not placed by
// absolute value) so adjacent years are well separated; a single empty slot is inserted across any
// break in the run, so the COVID shutdown (no 2019/20 or 2020/21 classes) still reads as a jump.
// Derived from whatever years are present, so the colours re-rank smoothly as more data is added.
function yearPositions(years) {
  const ordered = [...new Set(years)].sort((a, b) => a - b);
  const slots = {};
  let slot = 0;
  let prev = null;
  ordered.forEach((y) => {
    if (prev !== null && y - prev > 1) slot += 1; // one empty slot for a skipped year
    slots[y] = slot;
    slot += 1;
    prev = y;
  });
  const slotMax = Math.max(0, ...Object.values(slots));
  const positions = {};
  ordered.forEach((y) => (positions[y] = slotMax ? slots[y] / slotMax : 0));
  return positions;
}

// Per-academic-year colour: turbo sampled at the year's ranked position, shared across both per-year
// charts (positions are built once over the union of their years) so a year matches on each.
function yearColour(acadYear, positions) {
  return turbo(positions[acadYear] ?? 0);
}

// Matplotlib's YlGnBu, evenly spaced light -> dark, so 0% retention reads as a gentle pale yellow
// and 100% as a dark blue. Fed to the heatmap's visualMap as an evenly-interpolated colour list.
const YLGNBU = [
  '#ffffd9', '#edf8b1', '#c7e9b4', '#7fcdbb', '#41b6c4', '#1d91c0', '#225ea8', '#253494', '#081d58',
];

// Matplotlib's tab10, used to tint the retention heatmap's row labels by teaching team.
const TAB10 = [
  '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
  '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
];

// team (teacher) id -> colour; negative ids are "team unknown" and read grey.
function teamColour(teacherId) {
  return teacherId < 0 ? '#999999' : TAB10[teacherId % TAB10.length];
}

const GRID_LINE = { lineStyle: { color: '#e7e7e7' } };

// Init an ECharts instance on the given element id, apply the option, and keep it responsive.
function initChart(id, option) {
  const chart = echarts.init(document.getElementById(id));
  chart.setOption(option);
  window.addEventListener('resize', () => chart.resize());
  return chart;
}

// Charts 1 and 2 share a shape: per year a solid line (primary series) and an optional dashed
// line (secondary series) in the same colour. solidKey/dashKey name the per-point fields; the
// colour is the year's turbo colour, shared across both charts via the ranked-position map.
// Solid and dashed for one year share a series name, so a single legend entry toggles both.
function yearLineSeries(years, pointsKey, dashPointsKey, solidLabel, dashLabel, positions) {
  const series = [];
  years.forEach((y) => {
    const colour = yearColour(y.acad_year, positions);
    const solid = y[pointsKey] || [];
    series.push({
      name: y.label, type: 'line', color: colour, lineStyle: { color: colour, width: 2 },
      itemStyle: { color: colour },
      data: solid.map((p) => ({
        value: [p.term_num, p.attended ?? p.value],
        tip: `${y.label} ${solidLabel}<br>term ${p.term_num}: ${(p.attended ?? p.value).toFixed(1)}`,
      })),
    });
    // Pre-database years carry no secondary figures (null registered / empty socials), so drop the
    // null points; a year left with no dashed points gets no dashed line.
    const dashed = (y[dashPointsKey] || []).filter((p) => (p.registered ?? p.value) != null);
    if (dashed.length) {
      series.push({
        name: y.label, type: 'line', color: colour,
        lineStyle: { color: colour, width: 2, type: 'dashed' }, itemStyle: { color: colour },
        data: dashed.map((p) => ({
          value: [p.term_num, p.registered ?? p.value],
          tip: `${y.label} ${dashLabel}<br>term ${p.term_num}: ${(p.registered ?? p.value).toFixed(1)}`,
        })),
      });
    }
  });
  // Grey, data-less lines so the solid/dashed distinction gets its own legend entry.
  series.push({ name: solidLabel, type: 'line', data: [], color: '#4d4d4d', lineStyle: { color: '#4d4d4d' } });
  series.push({ name: dashLabel, type: 'line', data: [], color: '#4d4d4d', lineStyle: { color: '#4d4d4d', type: 'dashed' } });
  return series;
}

function maxTerm(years, pointsKeys) {
  let m = 1;
  years.forEach((y) => pointsKeys.forEach((k) => (y[k] || []).forEach((p) => {
    if (p.term_num > m) m = p.term_num;
  })));
  return m;
}

// Shared layout for the two per-year line charts. Solid and dashed lines share a series name, so
// one legend entry toggles both; the chart's title is the page's own heading above it. The legend
// wraps along the bottom so every year is visible at once, no scrolling.
function yearLineOption(series, xMax, yTitle) {
  return {
    grid: { left: 8, right: 16, top: 15, bottom: 100, containLabel: true },
    tooltip: { trigger: 'item', confine: true, formatter: (p) => p.data.tip },
    legend: { bottom: 4, textStyle: { fontSize: 11 } },
    xAxis: {
      type: 'value', min: 1, max: xMax, interval: 1, splitLine: { show: true, ...GRID_LINE },
      name: 'term', nameLocation: 'middle', nameGap: 26,
    },
    yAxis: { type: 'value', name: yTitle, nameLocation: 'middle', nameGap: 38, min: 0, splitLine: GRID_LINE },
    series,
  };
}

function renderBeginnerIntake(years, positions) {
  const series = yearLineSeries(years, 'points', 'points', 'attended', 'registered + waitlist', positions);
  initChart('beginner-intake-chart', yearLineOption(
    series, maxTerm(years, ['points']), 'mean Level 1 dancers per lesson',
  ));
}

function renderLevel2Socials(years, positions) {
  const series = yearLineSeries(
    years, 'class_points', 'social_points', 'Level 2 class', 'social-only tickets', positions
  );
  initChart('level2-socials-chart', yearLineOption(
    series, maxTerm(years, ['class_points', 'social_points']), 'mean attendees per session',
  ));
}

// Last-rendered datasets, kept so we can re-render in place when names are unlocked or re-locked.
let retentionData = null;
let retentionChart = null;
let communitySelection = null; // { scope, minDates, dancers } of the clicked community point
let termlySelection = null; // { scope, minActivities, termStart, label, dancers } of the clicked termly point

// team.id -> label. Locked (or no passphrase yet): the stripped DNC- codes the server sent.
// Unlocked: each team's teachers decrypted to first names, locally. Async because decryption is.
async function teamLabelMap(data) {
  const encByDancer = data.teacher_enc || {};
  const unlocked = AttendanceCrypto.isUnlocked();
  const map = {};
  for (const team of data.teams) {
    if (unlocked && team.ids && team.ids.length) {
      const firsts = [];
      for (const id of team.ids) {
        let name = null;
        try {
          name = await AttendanceCrypto.decryptName(encByDancer[id]);
        } catch (e) {
          name = null; // a teacher with no name on file falls back to the id code
        }
        firsts.push((name && name.first_name) || id.replace('DNC-', ''));
      }
      firsts.sort();
      map[team.id] = firsts.join('+');
    } else {
      map[team.id] = team.label;
    }
  }
  return map;
}

// Cohort-retention heatmap: rows are joining cohorts (earliest at top), columns are terms-since,
// colour is % of the cohort still active. The teaching team is carried in each cell's tooltip.
async function renderCohortRetention(data) {
  retentionData = data;
  const labelMap = await teamLabelMap(data);
  const terms = data.terms; // ordered by term idx
  const n = terms.length;
  const z = data.matrix; // z[cohort][offset], null where impossible / no cohort
  const offsets = Array.from({ length: n }, (_, j) => String(j));
  const cohortLabels = terms.map((t) => `${t.label}  (n=${t.total})`);

  // One data item per non-null cell: [offset, cohort, %], carrying the cohort / team text for
  // the tooltip.
  const cells = [];
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const v = z[i][j];
      if (v === null) continue;
      cells.push({
        value: [j, i, v],
        coh: terms[i].label, team: labelMap[terms[i].teacher_id],
      });
    }
  }

  const option = {
    grid: { left: 8, right: 65, top: 15, bottom: 40, containLabel: true },
    tooltip: {
      trigger: 'item', confine: true,
      formatter: (p) => `joined ${p.data.coh} (team ${p.data.team})<br>` +
        `${p.value[0]} terms later<br>${Math.round(p.value[2])}% still active`,
    },
    xAxis: { type: 'category', data: offsets, name: 'terms since joining', nameLocation: 'middle', nameGap: 26, splitArea: { show: true } },
    yAxis: {
      type: 'category', data: cohortLabels, inverse: true, splitArea: { show: true },
      // Tint each row label by its teaching team, so intakes that shared a team read at a glance.
      axisLabel: { fontSize: 9, color: (value, index) => teamColour(terms[index].teacher_id) },
    },
    visualMap: {
      type: 'continuous', min: 0, max: 100, calculable: true, right: 0, top: 'middle',
      itemHeight: 160, inRange: { color: YLGNBU }, text: ['100%', '0%'],
      textStyle: { fontSize: 10 },
    },
    series: [{ type: 'heatmap', data: cells }],
  };

  if (!retentionChart) {
    retentionChart = echarts.init(document.getElementById('cohort-retention-chart'));
    window.addEventListener('resize', () => retentionChart.resize());
  }
  retentionChart.setOption(option, true); // notMerge: labels/tooltips fully replaced on unlock
}

// Community survival curve for 2026: dancers attending at least each share of the calendar, with
// and without the 30th anniversary. Clicking a point downloads that group's DNC ids.
function renderCommunity2026(data) {
  const totalDates = data.total_dates || 0;
  const defs = [
    { key: 'incl_30th', label: 'incl. 30th anniversary', colour: '#1f77b4', scope: 'incl' },
    { key: 'excl_30th', label: 'excl. 30th anniversary', colour: '#d62728', scope: 'excl' },
  ];
  const series = defs.map((s) => ({
    name: s.label, type: 'line', color: s.colour, symbolSize: 7,
    lineStyle: { color: s.colour, width: 2 }, itemStyle: { color: s.colour },
    data: (data[s.key] || []).map((p) => ({
      value: [p.pct, p.dancers], raw: [s.scope, p.min_dates],
      tip: `${s.label}<br>at least ${p.min_dates} dates (${Math.round(p.pct)}% of calendar)<br>` +
        `${p.dancers} dancers<br><i>click to download their ids</i>`,
    })),
  }));

  const chart = initChart('community-2026-chart', {
    grid: { left: 8, right: 12, top: 15, bottom: 60, containLabel: true },
    tooltip: { trigger: 'item', confine: true, formatter: (p) => p.data.tip },
    legend: { bottom: 4 },
    xAxis: {
      type: 'value', min: 0, max: 100, interval: 10, splitLine: { show: true, ...GRID_LINE },
      name: `% of 2026 calendar (${totalDates} dates)`, nameLocation: 'middle', nameGap: 26,
    },
    yAxis: {
      type: 'value', name: 'dancers attending at least this share', nameLocation: 'middle',
      nameGap: 38, min: 0, splitLine: GRID_LINE,
    },
    // Pinch / scroll to zoom and drag to pan the horizontal axis only, as on the scatter.
    dataZoom: [{ type: 'inside', xAxisIndex: 0, filterMode: 'none' }],
    series,
  });

  chart.on('click', (p) => {
    if (!p.data || !p.data.raw) return;
    const [scope, minDates] = p.data.raw;
    communitySelection = { scope, minDates, dancers: p.value[1] };
    renderCommunityPanel();
  });
  chart.getZr().on('dblclick', () => chart.dispatchAction({ type: 'dataZoom', start: 0, end: 100 }));
}

// Render the pinned community-download panel for the currently-selected point. Locked: a hint to
// enter the passphrase. Unlocked: a button that fetches the ciphertext, decrypts names in the
// browser, and downloads the CSV.
function renderCommunityPanel() {
  if (!communitySelection) return;
  const { scope, minDates, dancers } = communitySelection;
  const body = document.getElementById('community-details-body');
  const where = scope === 'incl' ? 'including' : 'excluding';
  const lead =
    `<p>${dancers} dancers attended at least ${minDates} date(s) in 2026 ` +
    `(${where} the 30th anniversary, and excluding Volunteers and the Committee).`;

  if (AttendanceCrypto.isUnlocked()) {
    body.innerHTML = lead + '<br><button type="button" id="community-download-btn">Download (CSV)</button></p>';
    document.getElementById('community-download-btn').addEventListener('click', (ev) => {
      downloadCommunity(scope, minDates, ev.currentTarget);
    });
  } else {
    body.innerHTML =
      lead + '<br><em>Enter the passphrase at the top of the page to download these dancers.</em></p>';
  }
  document.getElementById('community-details').hidden = false;
}

async function downloadCommunity(scope, minDates, btn) {
  btn.setAttribute('aria-busy', 'true');
  try {
    const url = `/attendance/community/dancers.json?scope=${scope}&min_dates=${minDates}`;
    const resp = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
    const payload = await resp.json();
    if (!resp.ok) throw new Error(payload.error || 'Could not load the dancer list.');
    const rows = [];
    for (const d of payload.dancers) {
      const name = await AttendanceCrypto.decryptName(d.enc_name);
      rows.push([d.dancer_id, (name && name.first_name) || '', (name && name.last_name) || '']);
    }
    AttendanceCrypto.downloadCsv(
      `community_2026_${scope}_30th_min${minDates}_dates.csv`,
      ['dancer_id', 'first_name', 'last_name'],
      rows
    );
  } catch (e) {
    alert('Download failed: ' + e.message);
  } finally {
    btn.removeAttribute('aria-busy');
  }
}

// Termly active community since 2026: distinct dancers per term with >= 1 activity (active) and
// >= 2 (regulars), each counted including and excluding the 30th anniversary. Clicking a point
// downloads that term/threshold/scope's dancers, as on the survival curve above.
function renderTermlyActive(points) {
  const labels = points.map((p) => p.label);
  // symbol 'circle' (not the default 'emptyCircle', whose ring uses the fill colour) so hollow
  // regulars markers read as a white disc with a coloured ring rather than vanishing on white.
  const line = (key, colour, dash, hollow, name, scope, minAct) => ({
    name, type: 'line', color: colour, symbol: 'circle', symbolSize: 9,
    lineStyle: { color: colour, width: 2, type: dash },
    itemStyle: { color: hollow ? '#fff' : colour, borderColor: colour, borderWidth: 2 },
    data: points.map((p) => ({
      value: p[key], raw: [scope, minAct, p.term_start, p.label],
      tip: `${name}<br>${p.label}: ${p[key]} dancers<br><i>click to download their ids</i>`,
    })),
  });
  // incl drawn first so the coincident excl marker sits on top where the two lines meet.
  const series = [
    line('active_incl', '#1f77b4', 'solid', false, 'active, incl. 30th (>= 1)', 'incl', 1),
    line('active_excl', '#d62728', 'solid', false, 'active, excl. 30th (>= 1)', 'excl', 1),
    line('regular_incl', '#1f77b4', 'dashed', true, 'regulars, incl. 30th (>= 2)', 'incl', 2),
    line('regular_excl', '#d62728', 'dashed', true, 'regulars, excl. 30th (>= 2)', 'excl', 2),
  ];

  const chart = initChart('termly-active-chart', {
    grid: { left: 8, right: 16, top: 15, bottom: 80, containLabel: true },
    tooltip: { trigger: 'item', confine: true, formatter: (p) => p.data.tip },
    legend: { bottom: 4, textStyle: { fontSize: 11 } },
    xAxis: {
      type: 'category', data: labels, name: 'teaching term', nameLocation: 'middle', nameGap: 26,
      axisLabel: { fontSize: 10 }, splitLine: { show: true, ...GRID_LINE },
    },
    yAxis: { type: 'value', name: 'distinct dancers', nameLocation: 'middle', nameGap: 38, min: 0, splitLine: GRID_LINE },
    series,
  });

  chart.on('click', (p) => {
    if (!p.data || !p.data.raw) return;
    const [scope, minActivities, termStart, label] = p.data.raw;
    termlySelection = { scope, minActivities, termStart, label, dancers: p.value };
    renderTermlyPanel();
  });
}

// Render the pinned termly-download panel for the currently-selected point, mirroring the community
// panel: locked shows a passphrase hint, unlocked shows a button that fetches, decrypts and downloads.
function renderTermlyPanel() {
  if (!termlySelection) return;
  const { scope, minActivities, termStart, label, dancers } = termlySelection;
  const body = document.getElementById('termly-active-details-body');
  const where = scope === 'incl' ? 'including' : 'excluding';
  const kind = minActivities >= 2 ? 'came to two or more activities' : 'attended at least once';
  const lead =
    `<p>${dancers} dancers ${kind} in ${label} ` +
    `(${where} the 30th anniversary, and excluding Volunteers and the Committee).`;

  if (AttendanceCrypto.isUnlocked()) {
    body.innerHTML = lead + '<br><button type="button" id="termly-download-btn">Download (CSV)</button></p>';
    document.getElementById('termly-download-btn').addEventListener('click', (ev) => {
      downloadTermly(termStart, scope, minActivities, label, ev.currentTarget);
    });
  } else {
    body.innerHTML =
      lead + '<br><em>Enter the passphrase at the top of the page to download these dancers.</em></p>';
  }
  document.getElementById('termly-active-details').hidden = false;
}

async function downloadTermly(termStart, scope, minActivities, label, btn) {
  btn.setAttribute('aria-busy', 'true');
  try {
    const url =
      `/attendance/community/term-dancers.json?term_start=${termStart}&scope=${scope}&min_activities=${minActivities}`;
    const resp = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
    const payload = await resp.json();
    if (!resp.ok) throw new Error(payload.error || 'Could not load the dancer list.');
    const rows = [];
    for (const d of payload.dancers) {
      const name = await AttendanceCrypto.decryptName(d.enc_name);
      rows.push([d.dancer_id, (name && name.first_name) || '', (name && name.last_name) || '']);
    }
    const safeLabel = label.replace(/[^0-9a-z]+/gi, '_');
    AttendanceCrypto.downloadCsv(
      `termly_active_${safeLabel}_${scope}_30th_min${minActivities}_activities.csv`,
      ['dancer_id', 'first_name', 'last_name'],
      rows
    );
  } catch (e) {
    alert('Download failed: ' + e.message);
  } finally {
    btn.removeAttribute('aria-busy');
  }
}

async function renderSummaries() {
  const status = document.getElementById('summary-status');
  let payload;
  try {
    const resp = await fetch('/attendance/summaries.json', { credentials: 'same-origin' });
    payload = await resp.json();
    if (!resp.ok) {
      status.innerHTML = `<p class="error">${payload.error || 'Could not load the per-term summaries.'}</p>`;
      return;
    }
  } catch (e) {
    status.innerHTML = '<p class="error">Could not load the per-term summaries.</p>';
    return;
  }

  // Rank the colour ramp over the union of years on both per-year charts, so a given year is the
  // same colour on each and the ranking (and hence every year's colour) updates as data is added.
  const beginner = payload.beginner_intake || [];
  const level2 = payload.level2_socials || [];
  const allYears = [...beginner, ...level2].map((y) => y.acad_year).filter((v) => v != null);
  const positions = yearPositions(allYears);
  renderBeginnerIntake(beginner, positions);
  renderLevel2Socials(level2, positions);
  await renderCohortRetention(payload.cohort_retention || { terms: [], matrix: [], teams: [], teacher_enc: {} });
  renderCommunity2026(payload.community_2026 || { total_dates: 0, incl_30th: [], excl_30th: [] });
  renderTermlyActive(payload.termly_active || []);

  // When names are unlocked or re-locked, retranslate the retention tooltips and refresh the
  // community / termly download panels (the scatter download handles itself in attendance.js).
  AttendanceCrypto.onChange(() => {
    if (retentionData) renderCohortRetention(retentionData);
    renderCommunityPanel();
    renderTermlyPanel();
  });
}

renderSummaries();
