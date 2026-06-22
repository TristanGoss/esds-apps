// Renders the all-activities attendance scatter with Plotly, mirroring the visual language of
// the matplotlib chart in working/attendance.ipynb: colour = event type, marker shape =
// difficulty, filled = attended / hollow = registered. Data comes from /attendance/activities.json.

// colour -> event type
const EVENT_COLOURS = {
  course: '#1f77b4',     // blue
  social: '#ff7f0e',     // orange
  workshop: '#2ca02c',   // green
  weekender: '#9467bd',  // purple
};

// marker -> difficulty. Plotly symbol names chosen to match the matplotlib markers.
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

const DANCER_GREY = '#555555';
const FALLBACK_COLOUR = '#000000';
const FALLBACK_SYMBOL = 'circle';

function difficultyOf(row) {
  return row.difficulty || 'other';
}

// One scatter trace for confirmed attendance (filled markers), one for registrations (hollow).
// Per-point colour and symbol arrays carry the event-type / difficulty encoding within each trace.
function dataTraces(rows) {
  const attended = rows.filter((r) => r.total > 0);
  const registered = rows.filter((r) => r.named_registered > 0);

  const customdata = (r) => [
    r.event_name, r.activity_name, r.event_type, difficultyOf(r),
    r.named_total, r.aggregate_total, r.total, r.named_registered, r.named_unknown,
    r.activity_id,
  ];

  const attendedTrace = {
    name: 'attended',
    type: 'scatter',
    mode: 'markers',
    showlegend: false,
    x: attended.map((r) => r.date),
    y: attended.map((r) => r.total),
    customdata: attended.map(customdata),
    marker: {
      size: 9,
      color: attended.map((r) => EVENT_COLOURS[r.event_type] || FALLBACK_COLOUR),
      symbol: attended.map((r) => DIFFICULTY_SYMBOLS[difficultyOf(r)] || FALLBACK_SYMBOL),
      line: { color: 'white', width: 0.6 },
      opacity: 0.85,
    },
    hovertemplate:
      '<b>%{customdata[0]}</b><br>%{customdata[1]}<br>%{x|%Y-%m-%d}<br>' +
      'Attended: %{y} (named %{customdata[4]} + door %{customdata[5]})<extra></extra>',
  };

  const registeredTrace = {
    name: 'registered',
    type: 'scatter',
    mode: 'markers',
    showlegend: false,
    x: registered.map((r) => r.date),
    y: registered.map((r) => r.named_registered),
    customdata: registered.map(customdata),
    marker: {
      size: 9,
      color: registered.map((r) => EVENT_COLOURS[r.event_type] || FALLBACK_COLOUR),
      symbol: registered.map((r) => (DIFFICULTY_SYMBOLS[difficultyOf(r)] || FALLBACK_SYMBOL) + '-open'),
      line: { width: 1.4 },
      opacity: 0.8,
    },
    hovertemplate:
      '<b>%{customdata[0]}</b><br>%{customdata[1]}<br>%{x|%Y-%m-%d}<br>' +
      'Registered: %{y}<br>Turnout unknown: %{customdata[8]}<extra></extra>',
  };

  // Drawn registered-first so the filled attendance marker sits on top when the two coincide.
  return [registeredTrace, attendedTrace];
}

// Plotly has no separate colour/shape legend, so these zero-data traces stand in as legend keys,
// grouped under titles the way the three matplotlib legends were.
function legendTraces(rows) {
  const eventTypes = [...new Set(rows.map((r) => r.event_type))].filter((t) => EVENT_COLOURS[t]);
  const difficulties = Object.keys(DIFFICULTY_SYMBOLS).filter((d) =>
    rows.some((r) => difficultyOf(r) === d)
  );

  const stub = (extra) => Object.assign({
    type: 'scatter', mode: 'markers', x: [null], y: [null], hoverinfo: 'skip', showlegend: true,
  }, extra);

  const traces = [];
  eventTypes.forEach((t, i) =>
    traces.push(stub({
      name: t,
      legendgroup: 'event',
      legendgrouptitle: i === 0 ? { text: 'Event type' } : undefined,
      marker: { size: 9, color: EVENT_COLOURS[t], symbol: 'circle' },
    }))
  );
  difficulties.forEach((d, i) =>
    traces.push(stub({
      name: d,
      legendgroup: 'difficulty',
      legendgrouptitle: i === 0 ? { text: 'Difficulty' } : undefined,
      marker: { size: 9, color: DANCER_GREY, symbol: DIFFICULTY_SYMBOLS[d] },
    }))
  );
  traces.push(stub({
    name: 'attended',
    legendgroup: 'record',
    legendgrouptitle: { text: 'Record type' },
    marker: { size: 9, color: DANCER_GREY, symbol: 'circle' },
  }));
  traces.push(stub({
    name: 'registered',
    legendgroup: 'record',
    marker: { size: 9, color: DANCER_GREY, symbol: 'circle-open', line: { width: 1.4 } },
  }));
  return traces;
}

// The CSV column order for an activity's full record. first_name/last_name are filled in the
// browser from the decrypted enc_name; everything else comes straight off the server row.
const ACTIVITY_CSV_COLUMNS = [
  'event_name', 'event_type', 'venue', 'activity_name', 'activity_type', 'difficulty', 'date',
  'record_type', 'dancer_id', 'first_name', 'last_name', 'status', 'ticket_type', 'head_count',
];

let selectedActivity = null; // the clicked point's customdata, kept so we can re-render on unlock

function showDetails(point) {
  const cd = point.customdata;
  if (!cd) return;
  selectedActivity = { cd, x: point.x };
  renderPointPanel();
}

function renderPointPanel() {
  if (!selectedActivity) return;
  const { cd, x } = selectedActivity;
  const [eventName, activityName, eventType, difficulty, named, agg, total, registered, unknown, activityId] = cd;
  const summary = `
    <p><strong>${eventName}</strong> — ${activityName} (${eventType}, ${difficulty})<br>${x}</p>
    <p>
      Attended: <strong>${total}</strong> (${named} named + ${agg} anonymous door)<br>
      Registered (named): <strong>${registered}</strong><br>
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

  const layout = {
    title: 'ESDS attendance over time (all activities)',
    xaxis: { title: 'date', type: 'date', showgrid: true, gridcolor: '#e7e7e7' },
    yaxis: { title: 'people', rangemode: 'tozero', showgrid: true, gridcolor: '#e7e7e7' },
    hovermode: 'closest',
    dragmode: 'zoom',
    legend: { groupclick: 'toggleitem', x: 1.02, y: 1, font: { size: 11 } },
    margin: { l: 55, r: 160, t: 50, b: 50 },
    plot_bgcolor: 'white',
    paper_bgcolor: 'white',
  };

  const chart = document.getElementById('attendance-chart');
  await Plotly.newPlot(
    chart,
    [...dataTraces(rows), ...legendTraces(rows)],
    layout,
    { responsive: true, scrollZoom: true, displaylogo: false }
  );
  chart.on('plotly_click', (ev) => showDetails(ev.points[0]));

  // Swap the selected activity's download between the locked hint and a working button when names
  // are unlocked or re-locked from the control at the top of the page.
  AttendanceCrypto.onChange(() => renderPointPanel());
}

render();
