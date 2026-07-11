// Renders the three per-term summary charts on /attendance with Plotly, from the datasets
// served by /attendance/summaries.json (built server-side in attendance/analysis.py, itself a
// port of the matplotlib charts in working/attendance.ipynb). Three charts:
//   1. beginner (Level 1) intake per term, one line per academic year (solid attended / dashed registered + waitlist);
//   2. Level 2 class attendance per term with the paired social-only turnout (solid / dashed);
//   3. cohort-retention heatmap with a teaching-team strip down the joining-cohort axis;
//   4. the 2026 community survival curve (with / without the 30th anniversary);
//   5. termly active-community counts since 2026 (active >= 1 / regulars >= 2, incl / excl the 30th).

// Matplotlib's tab10, still used for the teaching-team colours on the retention heatmap.
const TAB10 = [
  '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
  '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
];

// Matplotlib's viridis at ten evenly spaced stops, interpolated in RGB below. The per-year charts
// colour each academic year from this ramp (see yearColour), matching the notebook.
const VIRIDIS = [
  '#440154', '#482878', '#3e4a89', '#31688e', '#26828e',
  '#1f9e89', '#35b779', '#6ece58', '#b5de2b', '#fde725',
];

function _lerpHex(a, b, t) {
  const ai = parseInt(a.slice(1), 16);
  const bi = parseInt(b.slice(1), 16);
  const r = Math.round((ai >> 16) + (((bi >> 16) - (ai >> 16)) * t));
  const g = Math.round(((ai >> 8) & 255) + ((((bi >> 8) & 255) - ((ai >> 8) & 255)) * t));
  const bl = Math.round((ai & 255) + (((bi & 255) - (ai & 255)) * t));
  return `rgb(${r}, ${g}, ${bl})`;
}

// Sample the viridis ramp at s in [0, 1].
function viridis(s) {
  const clamped = Math.max(0, Math.min(1, s));
  const scaled = clamped * (VIRIDIS.length - 1);
  const i = Math.min(Math.floor(scaled), VIRIDIS.length - 2);
  return _lerpHex(VIRIDIS[i], VIRIDIS[i + 1], scaled - i);
}

// Per-academic-year colour: viridis mapped to the real calendar year, so consecutive years sit
// close and the COVID shutdown (no 2019/20 or 2020/21) reads as a jump. Flipped so recent years are
// the dark end and pre-COVID years the yellow end. minYear/maxYear span every year on either per-year
// chart, so a given year gets the same colour on both.
function yearColour(acadYear, minYear, maxYear) {
  const frac = maxYear > minYear ? (acadYear - minYear) / (maxYear - minYear) : 0;
  return viridis(1 - frac);
}

// Matplotlib's YlGnBu, sampled light -> dark, so 0% retention reads as a gentle pale yellow and
// 100% as a dark blue (less aggressive at zero than plasma was).
const YLGNBU = [
  [0.0, '#ffffd9'], [0.125, '#edf8b1'], [0.25, '#c7e9b4'], [0.375, '#7fcdbb'],
  [0.5, '#41b6c4'], [0.625, '#1d91c0'], [0.75, '#225ea8'], [0.875, '#253494'], [1.0, '#081d58'],
];

const PLOT_CONFIG = { responsive: true, displaylogo: false };
const GRID = { showgrid: true, gridcolor: '#e7e7e7' };

// A grey, data-less line so the solid/dashed distinction gets its own legend entry alongside
// the per-year colour entries (Plotly has only one legend, unlike the notebook's two).
function styleStub(name, dash) {
  return {
    type: 'scatter', mode: 'lines', name, x: [null], y: [null],
    line: { color: '#4d4d4d', dash }, hoverinfo: 'skip', legendgroup: 'style',
    legendgrouptitle: dash === 'solid' ? { text: 'Line style' } : undefined,
  };
}

// Charts 1 and 2 share a shape: per year a solid line (primary series) and an optional dashed
// line (secondary series) in the same colour. solidKey/dashKey name the per-point fields; the
// colour is the year's viridis colour, shared across both charts via minYear/maxYear.
function yearLineTraces(years, pointsKey, dashPointsKey, solidLabel, dashLabel, minYear, maxYear) {
  const traces = [];
  years.forEach((y, i) => {
    const colour = yearColour(y.acad_year, minYear, maxYear);
    const solid = y[pointsKey] || [];
    traces.push({
      type: 'scatter', mode: 'lines', name: y.label, legendgroup: 'year-' + i,
      x: solid.map((p) => p.term_num), y: solid.map((p) => p.attended ?? p.value),
      line: { color: colour, width: 2 },
      hovertemplate: `${y.label} ${solidLabel}<br>term %{x}: %{y:.1f}<extra></extra>`,
    });
    // Pre-database years carry no secondary figures (null registered / empty socials), so drop the
    // null points; a year left with no dashed points gets no dashed line.
    const dashed = (y[dashPointsKey] || []).filter((p) => (p.registered ?? p.value) != null);
    if (dashed.length) {
      traces.push({
        type: 'scatter', mode: 'lines', name: y.label, legendgroup: 'year-' + i, showlegend: false,
        x: dashed.map((p) => p.term_num), y: dashed.map((p) => p.registered ?? p.value),
        line: { color: colour, width: 2, dash: 'dash' },
        hovertemplate: `${y.label} ${dashLabel}<br>term %{x}: %{y:.1f}<extra></extra>`,
      });
    }
  });
  traces.push(styleStub(solidLabel, 'solid'));
  traces.push(styleStub(dashLabel, 'dash'));
  return traces;
}

function termAxis(years, pointsKeys) {
  let maxTerm = 1;
  years.forEach((y) => pointsKeys.forEach((k) => (y[k] || []).forEach((p) => {
    if (p.term_num > maxTerm) maxTerm = p.term_num;
  })));
  return {
    title: 'term number within academic year (1 = first after summer break)',
    tickmode: 'array', tickvals: Array.from({ length: maxTerm }, (_, i) => i + 1),
    ...GRID,
  };
}

function renderBeginnerIntake(years, minYear, maxYear) {
  const traces = yearLineTraces(years, 'points', 'points', 'attended', 'registered + waitlist', minYear, maxYear);
  const layout = {
    title: 'Mean Level 1 attendance per lesson, by academic year',
    xaxis: termAxis(years, ['points']),
    yaxis: { title: 'mean Level 1 dancers per lesson', rangemode: 'tozero', ...GRID },
    legend: { groupclick: 'toggleitem' }, hovermode: 'closest',
    margin: { l: 60, r: 30, t: 50, b: 60 }, plot_bgcolor: 'white', paper_bgcolor: 'white',
  };
  Plotly.newPlot('beginner-intake-chart', traces, layout, PLOT_CONFIG);
}

function renderLevel2Socials(years, minYear, maxYear) {
  const traces = yearLineTraces(
    years, 'class_points', 'social_points', 'Level 2 class', 'social-only tickets', minYear, maxYear
  );
  const layout = {
    title: 'Mean Level 2 and social-only attendance per term, by academic year',
    xaxis: termAxis(years, ['class_points', 'social_points']),
    yaxis: { title: 'mean attendees per session', rangemode: 'tozero', ...GRID },
    legend: { groupclick: 'toggleitem' }, hovermode: 'closest',
    margin: { l: 60, r: 30, t: 50, b: 60 }, plot_bgcolor: 'white', paper_bgcolor: 'white',
  };
  Plotly.newPlot('level2-socials-chart', traces, layout, PLOT_CONFIG);
}

function teamColour(teacherId) {
  return teacherId < 0 ? '#d9d9d9' : TAB10[teacherId % TAB10.length];
}

// Last-rendered datasets, kept so we can re-render in place when names are unlocked or re-locked.
let retentionData = null;
let communitySelection = null; // { scope, minDates, dancers } of the clicked community point
let termlySelection = null; // { scope, minActivities, termStart, label, dancers } of the clicked termly point

// team.id -> legend label. Locked (or no passphrase yet): the stripped DNC- codes the server sent.
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

async function renderCohortRetention(data) {
  retentionData = data;
  const labelMap = await teamLabelMap(data);
  const terms = data.terms; // ordered by term idx
  const n = terms.length;
  const z = data.matrix; // z[cohort][offset], null where impossible / no cohort
  const offsets = Array.from({ length: n }, (_, j) => j);
  const yIdx = Array.from({ length: n }, (_, i) => i);
  const labels = terms.map((t) => `${t.label}  (n=${t.total})`);

  const heatmap = {
    type: 'heatmap', x: offsets, y: yIdx, z, zmin: 0, zmax: 100,
    colorscale: YLGNBU, hoverongaps: false,
    colorbar: { title: { text: '% of cohort<br>still active', side: 'right' }, thickness: 14 },
    // customdata must match the z grid (rows x cols) for per-cell hover; carry the cohort label
    // across every cell in its row. A 1D array isn't mapped to cells, so %{customdata} stays literal.
    customdata: z.map((row, i) => row.map(() => terms[i].label)),
    hovertemplate: 'joined %{customdata}<br>%{x} terms later<br>%{z:.0f}% still active<extra></extra>',
  };

  // Per-cell percentage labels, white on the dark (high) end and black on the light end, as in
  // the notebook. Plotly heatmap text shares one font colour, so use per-cell annotations.
  const annotations = [];
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      if (z[i][j] === null) continue;
      annotations.push({
        x: j, y: i, text: String(Math.round(z[i][j])), showarrow: false,
        font: { size: 9, color: z[i][j] > 55 ? 'white' : 'black' },
      });
    }
  }

  // Teaching-team strip: a coloured rectangle per joining cohort, just left of the grid. Shapes
  // give precise placement; Plotly can't render a categorical second colour-axis on a heatmap.
  const shapes = [];
  terms.forEach((t, i) => {
    shapes.push({
      type: 'rect', xref: 'x', yref: 'y', x0: -1.6, x1: -0.6, y0: i - 0.5, y1: i + 0.5,
      fillcolor: teamColour(t.teacher_id), line: { width: 0 },
    });
  });

  // Data-less square markers so each distinct teaching team gets a legend entry.
  const teamLegend = data.teams.map((team) => ({
    type: 'scatter', mode: 'markers', name: labelMap[team.id], x: [null], y: [null],
    marker: { size: 10, color: teamColour(team.id), symbol: 'square' }, hoverinfo: 'skip',
  }));

  const layout = {
    title: 'Cohort retention: % of each joining term still active N terms later',
    annotations, shapes,
    xaxis: {
      title: 'terms since joining', range: [-2, n - 0.5],
      tickmode: 'array', tickvals: offsets, constrain: 'domain',
    },
    yaxis: {
      autorange: 'reversed', // cohort 0 (earliest) at the top, as in the notebook
      tickmode: 'array', tickvals: yIdx, ticktext: labels, tickfont: { size: 9 },
    },
    legend: { title: { text: 'Teaching team' }, orientation: 'h', y: -0.12, font: { size: 10 } },
    margin: { l: 120, r: 20, t: 50, b: 90 }, plot_bgcolor: 'white', paper_bgcolor: 'white',
  };
  Plotly.newPlot('cohort-retention-chart', [heatmap, ...teamLegend], layout, PLOT_CONFIG);
}

// Community survival curve for 2026: dancers attending at least each share of the calendar, with
// and without the 30th anniversary. Clicking a point downloads that group's DNC ids.
function renderCommunity2026(data) {
  const totalDates = data.total_dates || 0;
  const series = [
    { key: 'incl_30th', label: 'incl. 30th anniversary', colour: '#1f77b4', scope: 'incl' },
    { key: 'excl_30th', label: 'excl. 30th anniversary', colour: '#d62728', scope: 'excl' },
  ];
  const traces = series.map((s) => {
    const pts = data[s.key] || [];
    return {
      type: 'scatter', mode: 'lines+markers', name: s.label,
      x: pts.map((p) => p.pct), y: pts.map((p) => p.dancers),
      customdata: pts.map((p) => [s.scope, p.min_dates]),
      marker: { size: 7, color: s.colour }, line: { color: s.colour, width: 2 },
      hovertemplate:
        `${s.label}<br>at least %{customdata[1]} dates (%{x:.0f}% of calendar)<br>` +
        '%{y} dancers<br><i>click to download their ids</i><extra></extra>',
    };
  });

  const layout = {
    title: 'The 2026 community: how big is it, and how committed?',
    xaxis: {
      title: `share of the 2026 calendar attended (%) — ${totalDates} dates in all`,
      range: [0, 100], tickmode: 'array', tickvals: Array.from({ length: 11 }, (_, i) => i * 10), ...GRID,
    },
    yaxis: { title: 'dancers attending at least this share', rangemode: 'tozero', ...GRID },
    hovermode: 'closest', legend: { x: 0.98, y: 0.98, xanchor: 'right', yanchor: 'top' },
    margin: { l: 60, r: 30, t: 50, b: 60 }, plot_bgcolor: 'white', paper_bgcolor: 'white',
  };

  Plotly.newPlot('community-2026-chart', traces, layout, PLOT_CONFIG).then((gd) => {
    gd.on('plotly_click', (ev) => {
      const cd = ev.points[0].customdata;
      if (!cd) return;
      const [scope, minDates] = cd;
      communitySelection = { scope, minDates, dancers: ev.points[0].y };
      renderCommunityPanel();
    });
  });
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

// Termly active community since 2026 (Plot 8): distinct dancers per term with >= 1 activity
// (active) and >= 2 (regulars), each counted including and excluding the 30th anniversary. Clicking
// a point downloads that term/threshold/scope's dancers, as on the survival curve above.
function renderTermlyActive(points) {
  const labels = points.map((p) => p.label);
  const line = (key, colour, dash, hollow, name, scope, minAct) => ({
    type: 'scatter', mode: 'lines+markers', name,
    x: labels, y: points.map((p) => p[key]),
    customdata: points.map((p) => [scope, minAct, p.term_start, p.label]),
    line: { color: colour, width: 2, dash },
    marker: { size: 9, symbol: 'circle', color: hollow ? 'white' : colour, line: { color: colour, width: 2 } },
    hovertemplate: `${name}<br>%{x}: %{y} dancers<br><i>click to download their ids</i><extra></extra>`,
  });
  // incl drawn first so the coincident excl marker sits on top where the two lines meet.
  const traces = [
    line('active_incl', '#1f77b4', 'solid', false, 'active, incl. 30th (>= 1)', 'incl', 1),
    line('active_excl', '#d62728', 'solid', false, 'active, excl. 30th (>= 1)', 'excl', 1),
    line('regular_incl', '#1f77b4', 'dash', true, 'regulars, incl. 30th (>= 2)', 'incl', 2),
    line('regular_excl', '#d62728', 'dash', true, 'regulars, excl. 30th (>= 2)', 'excl', 2),
  ];
  const layout = {
    title: 'Termly active community since 2026 (all event types)',
    xaxis: { title: 'teaching term', type: 'category', ...GRID },
    yaxis: { title: 'distinct dancers', rangemode: 'tozero', ...GRID },
    hovermode: 'closest', legend: { x: 0.98, y: 0.98, xanchor: 'right', yanchor: 'top' },
    margin: { l: 60, r: 30, t: 50, b: 60 }, plot_bgcolor: 'white', paper_bgcolor: 'white',
  };
  Plotly.newPlot('termly-active-chart', traces, layout, PLOT_CONFIG).then((gd) => {
    gd.on('plotly_click', (ev) => {
      const cd = ev.points[0].customdata;
      if (!cd) return;
      const [scope, minActivities, termStart, label] = cd;
      termlySelection = { scope, minActivities, termStart, label, dancers: ev.points[0].y };
      renderTermlyPanel();
    });
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

  // The shared colour ramp spans every year on either per-year chart. The server sends the span;
  // fall back to the years actually present if an older payload omits it.
  const beginner = payload.beginner_intake || [];
  const level2 = payload.level2_socials || [];
  const allYears = [...beginner, ...level2].map((y) => y.acad_year).filter((v) => v != null);
  const minYear = payload.year_min ?? Math.min(...allYears);
  const maxYear = payload.year_max ?? Math.max(...allYears);
  renderBeginnerIntake(beginner, minYear, maxYear);
  renderLevel2Socials(level2, minYear, maxYear);
  await renderCohortRetention(payload.cohort_retention || { terms: [], matrix: [], teams: [], teacher_enc: {} });
  renderCommunity2026(payload.community_2026 || { total_dates: 0, incl_30th: [], excl_30th: [] });
  renderTermlyActive(payload.termly_active || []);

  // When names are unlocked or re-locked, retranslate the retention legend and refresh the
  // community download panel (the scatter download handles itself in attendance.js).
  AttendanceCrypto.onChange(() => {
    if (retentionData) renderCohortRetention(retentionData);
    renderCommunityPanel();
    renderTermlyPanel();
  });
}

renderSummaries();
