// Renders the all-activities attendance scatter with ECharts, mirroring the visual language of
// the matplotlib chart in working/attendance.ipynb: colour = event type, marker shape =
// difficulty, filled = attended / hollow = registered + waitlist. Data comes from /attendance/activities.json.

// colour -> event type
const EVENT_COLOURS = {
  course: '#1f77b4',     // blue
  social: '#ff7f0e',     // orange
  workshop: '#2ca02c',   // green
  weekender: '#9467bd',  // purple
};

// marker -> difficulty. ECharts has few built-in symbols, so each shape is a custom SVG path
// (SYMBOL_D below), chosen to match the matplotlib markers. Hollow = same path, transparent fill.
const DIFFICULTY_SYMBOLS = {
  'beginners': 'triangle-down',
  'Level 1': 'triangle-up',
  'Level 2': 'square',
  'Level 3': 'cross',
  'intermediate': 'triangle-left',
  'advanced': 'triangle-right',
  'Improvers / Intermediate': 'diamond',
  'Intermediate / Advanced': 'hexagon',
  'social': 'x',
  'other': 'circle',
};

// SVG path data for each marker shape, drawn in a [-10, 10] box centred on the origin. ECharts
// scales these to symbolSize; the same strings feed the HTML legend swatches. 'circle' is built in.
const SYMBOL_D = {
  'triangle-up': 'M0,-10 L9,7 L-9,7 Z',
  'triangle-down': 'M0,10 L9,-7 L-9,-7 Z',
  'triangle-left': 'M-10,0 L7,-9 L7,9 Z',
  'triangle-right': 'M10,0 L-7,-9 L-7,9 Z',
  'square': 'M-8,-8 H8 V8 H-8 Z',
  'diamond': 'M0,-10 L10,0 L0,10 L-10,0 Z',
  'cross': 'M-3,-10 H3 V-3 H10 V3 H3 V10 H-3 V3 H-10 V-3 H-3 Z',
  'x': 'M-9,-6 L-6,-9 L0,-3 L6,-9 L9,-6 L3,0 L9,6 L6,9 L0,3 L-6,9 L-9,6 L-3,0 Z',
  'hexagon': 'M0,-10 L8.7,-5 L8.7,5 L0,10 L-8.7,5 L-8.7,-5 Z',
};

const DANCER_GREY = '#555555';
const FALLBACK_COLOUR = '#000000';
const MARKER_SIZE = 11;

function difficultyOf(row) {
  return row.difficulty || 'other';
}

// Shape name -> ECharts symbol string (custom path, or the built-in 'circle').
function echartsSymbol(shape) {
  return SYMBOL_D[shape] ? 'path://' + SYMBOL_D[shape] : 'circle';
}

// Shape name -> inline SVG swatch for the HTML legend. Filled or hollow via fill/stroke.
function svgSwatch(shape, { fill, stroke, strokeWidth = 1 }) {
  const f = fill || 'none';
  const s = stroke ? ` stroke="${stroke}" stroke-width="${strokeWidth}"` : '';
  const body = SYMBOL_D[shape]
    ? `<path d="${SYMBOL_D[shape]}" fill="${f}"${s}/>`
    : `<circle cx="0" cy="0" r="9" fill="${f}"${s}/>`;
  return `<svg viewBox="-11 -11 22 22" aria-hidden="true">${body}</svg>`;
}

// A tooltip HTML string built per point, so the chart's formatter is a trivial lookup.
function attendedTip(r) {
  return `<b>${r.event_name}</b><br>${r.activity_name}<br>${(r.date || '').slice(0, 10)}<br>` +
    `Attended: ${r.total} (named ${r.named_total} + door ${r.aggregate_total})`;
}
function registeredTip(r) {
  return `<b>${r.event_name}</b><br>${r.activity_name}<br>${(r.date || '').slice(0, 10)}<br>` +
    `Registered + waitlist: ${r.named_registered + r.waitlisted} ` +
    `(registered ${r.named_registered} + waitlist ${r.waitlisted})<br>Turnout unknown: ${r.named_unknown}`;
}

// customdata carried on each point for the pinned detail panel (order matches renderPointPanel).
function customdata(r) {
  return [
    r.event_name, r.activity_name, r.event_type, difficultyOf(r),
    r.named_total, r.aggregate_total, r.total, r.named_registered, r.named_unknown,
    r.activity_id, r.waitlisted,
  ];
}

function ts(dateStr) {
  return new Date(dateStr).getTime();
}

// One scatter series for confirmed attendance (filled markers), one for registrations (hollow).
// Per-point symbol and itemStyle carry the difficulty / event-type / record encoding.
function dataSeries(rows) {
  const attended = rows.filter((r) => r.total > 0).map((r) => {
    const colour = EVENT_COLOURS[r.event_type] || FALLBACK_COLOUR;
    return {
      value: [ts(r.date), r.total],
      symbol: echartsSymbol(DIFFICULTY_SYMBOLS[difficultyOf(r)] || 'circle'),
      itemStyle: { color: colour, borderColor: '#fff', borderWidth: 0.6, opacity: 0.85 },
      cd: customdata(r), x: (r.date || '').slice(0, 10), tip: attendedTip(r),
    };
  });
  // hollow = registered + waitlist: named on the sheet (turned up or not) plus the event's waitlist.
  const registered = rows.filter((r) => r.named_registered + r.waitlisted > 0).map((r) => {
    const colour = EVENT_COLOURS[r.event_type] || FALLBACK_COLOUR;
    return {
      value: [ts(r.date), r.named_registered + r.waitlisted],
      symbol: echartsSymbol(DIFFICULTY_SYMBOLS[difficultyOf(r)] || 'circle'),
      itemStyle: { color: 'transparent', borderColor: colour, borderWidth: 1.4, opacity: 0.8 },
      cd: customdata(r), x: (r.date || '').slice(0, 10), tip: registeredTip(r),
    };
  });

  // registered listed first so the filled attendance marker draws on top when the two coincide.
  return [
    { name: 'registered + waitlist', type: 'scatter', symbolSize: MARKER_SIZE, data: registered },
    { name: 'attended', type: 'scatter', symbolSize: MARKER_SIZE, data: attended },
  ];
}

// Pre-database term means (from the old ESDS summaries / 2023 AGM report), drawn as ordinary course
// markers -- a filled blue triangle for Level 1, a square for Level 2 -- on each of a term's class
// nights. Styled like the attended points so they sit in context and need no separate legend key.
// They are per-term means, not single sessions (see the FAQ), so they read oddly uniform.
function earlyMeanSeries(means) {
  if (!means || !means.length) return [];
  const byLevel = { L1: [], L2: [] };
  means.forEach((m) => byLevel[m.level] && byLevel[m.level].push(m));
  const series = (rows, difficulty, label) => ({
    name: label + ' (term mean)', type: 'scatter', symbolSize: MARKER_SIZE,
    symbol: echartsSymbol(DIFFICULTY_SYMBOLS[difficulty]),
    data: rows.map((m) => ({
      value: [ts(m.date), m.mean],
      itemStyle: { color: EVENT_COLOURS.course, borderColor: '#fff', borderWidth: 0.6, opacity: 0.85 },
      tip: `<b>Term mean (old records)</b><br>${label}<br>${(m.date || '').slice(0, 4)}<br>` +
        `mean attended: ${m.mean.toFixed(1)}`,
    })),
  });
  return [series(byLevel.L1, 'Level 1', 'Level 1'), series(byLevel.L2, 'Level 2', 'Level 2')];
}

// Build the HTML colour/shape key beneath the chart (ECharts has no built-in encoding legend).
function renderLegend(rows) {
  const eventTypes = [...new Set(rows.map((r) => r.event_type))].filter((t) => EVENT_COLOURS[t]);
  const difficulties = Object.keys(DIFFICULTY_SYMBOLS).filter((d) =>
    rows.some((r) => difficultyOf(r) === d)
  );
  const item = (swatch, label) => `<span class="legend-item">${swatch}${label}</span>`;

  const eventItems = eventTypes.map((t) =>
    item(svgSwatch('circle', { fill: EVENT_COLOURS[t] }), t)
  );
  const diffItems = difficulties.map((d) =>
    item(svgSwatch(DIFFICULTY_SYMBOLS[d], { fill: DANCER_GREY }), d)
  );
  const recordItems = [
    item(svgSwatch('circle', { fill: DANCER_GREY }), 'attended'),
    item(svgSwatch('circle', { fill: 'transparent', stroke: DANCER_GREY, strokeWidth: 1.4 }), 'registered + waitlist'),
  ];

  const group = (title, items) =>
    `<span class="legend-group"><strong>${title}:</strong>${items.join('')}</span>`;
  document.getElementById('attendance-legend').innerHTML =
    group('Event type', eventItems) + group('Difficulty', diffItems) + group('Record type', recordItems);
}

// The CSV column order for an activity's full record. first_name/last_name are filled in the
// browser from the decrypted enc_name; everything else comes straight off the server row.
const ACTIVITY_CSV_COLUMNS = [
  'event_name', 'event_type', 'venue', 'activity_name', 'activity_type', 'difficulty', 'date',
  'record_type', 'dancer_id', 'first_name', 'last_name', 'status', 'ticket_type', 'head_count',
];

let selectedActivity = null; // the clicked point's data, kept so we can re-render on unlock

// Called with a clicked point's data item ({ cd, x }); pins its breakdown beneath the chart.
function showDetails(item) {
  if (!item || !item.cd) return;
  selectedActivity = { cd: item.cd, x: item.x };
  renderPointPanel();
}

function renderPointPanel() {
  if (!selectedActivity) return;
  const { cd, x } = selectedActivity;
  const [eventName, activityName, eventType, difficulty, named, agg, total, registered, unknown, activityId, waitlisted] = cd;
  const summary = `
    <p><strong>${eventName}</strong> — ${activityName} (${eventType}, ${difficulty})<br>${x}</p>
    <p>
      Attended: <strong>${total}</strong> (${named} named + ${agg} anonymous door)<br>
      Registered (named): <strong>${registered}</strong><br>
      Waitlisted (event): <strong>${waitlisted}</strong><br>
      Turnout unknown: <strong>${unknown}</strong>
    </p>`;

  const body = document.getElementById('point-details-body');
  if (AttendanceCrypto.isUnlocked()) {
    body.innerHTML = summary + '<p><button type="button" id="activity-download-btn">Download full record (CSV)</button></p>';
    document.getElementById('activity-download-btn').addEventListener('click', (ev) =>
      downloadActivity(activityId, ev.currentTarget)
    );
  } else {
    body.innerHTML =
      summary +
      '<p><em>Enter the passphrase at the top of the page to download this activity\'s full record.</em></p>';
  }
  document.getElementById('point-details').hidden = false;
}

async function downloadActivity(activityId, btn) {
  btn.setAttribute('aria-busy', 'true');
  try {
    const resp = await fetch(`/attendance/activity/${activityId}/records.json`, {
      credentials: 'same-origin',
      cache: 'no-store',
    });
    const payload = await resp.json();
    if (!resp.ok) throw new Error(payload.error || 'Could not load the activity record.');
    const rows = [];
    for (const r of payload.rows) {
      const name = await AttendanceCrypto.decryptName(r.enc_name);
      const out = { ...r, first_name: (name && name.first_name) || '', last_name: (name && name.last_name) || '' };
      rows.push(ACTIVITY_CSV_COLUMNS.map((c) => out[c]));
    }
    AttendanceCrypto.downloadCsv(`activity_${activityId}_attendance.csv`, ACTIVITY_CSV_COLUMNS, rows);
  } catch (e) {
    alert('Download failed: ' + e.message);
  } finally {
    btn.removeAttribute('aria-busy');
  }
}

async function render() {
  const status = document.getElementById('chart-status');
  let payload;
  try {
    const resp = await fetch('/attendance/activities.json', { credentials: 'same-origin' });
    payload = await resp.json();
    if (!resp.ok) {
      status.innerHTML = `<p class="error">${payload.error || 'Could not load attendance data.'}</p>`;
      return;
    }
  } catch (e) {
    status.innerHTML = '<p class="error">Could not load attendance data.</p>';
    return;
  }

  const rows = payload.activities || [];
  if (rows.length === 0) {
    status.innerHTML = '<p class="error">The attendance database is empty.</p>';
    return;
  }

  const chart = echarts.init(document.getElementById('attendance-chart'));
  chart.setOption({
    grid: { left: 8, right: 12, top: 15, bottom: 40, containLabel: true },
    tooltip: { trigger: 'item', confine: true, formatter: (p) => p.data.tip },
    xAxis: { type: 'time', splitLine: { show: true, lineStyle: { color: '#e7e7e7' } } },
    yAxis: {
      type: 'value', name: 'people', nameLocation: 'middle', nameGap: 38, scale: true,
      splitLine: { lineStyle: { color: '#e7e7e7' } },
    },
    // Pinch / scroll to zoom and drag to pan the date axis; filterMode 'filter' drops out-of-view
    // points so the y-axis (scale: true) re-fits to the visible data. Double-click resets.
    dataZoom: [{ type: 'inside', xAxisIndex: 0, filterMode: 'filter' }],
    series: [...dataSeries(rows), ...earlyMeanSeries(payload.early_term_means)],
  });

  renderLegend(rows);
  chart.on('click', (p) => showDetails(p.data));
  chart.getZr().on('dblclick', () => chart.dispatchAction({ type: 'dataZoom', start: 0, end: 100 }));
  window.addEventListener('resize', () => chart.resize());

  // Swap the selected activity's download between the locked hint and a working button when names
  // are unlocked or re-locked from the control at the top of the page.
  AttendanceCrypto.onChange(() => renderPointPanel());
}

render();
