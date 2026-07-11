"""ipywidgets control panel for de-duplicating and correcting dancer records.

Offline notebook tooling (``pseudonymise.ipynb``) only. It imports ``ipywidgets``, a *dev*
dependency that is not part of the deployed app, so nothing in the running server imports this
module. It wraps the read/write primitives in ``pseudonyms_db`` and ``dancer_matching`` in a
friendlier interface than the old ``input()``-driven loop:

* **Review duplicates** — scan for fuzzy candidates, then work through them one at a time with
  both records shown side by side and Merge / Swap / Skip buttons (calls ``substitute_dancer_id``).
* **Edit a dancer** — search for or type a dancer id, load their fields into a form, correct any
  of them and save (calls ``update_dancer``).

Call :func:`launch` from a notebook cell. The database is opened once and its connection is held
for the life of the panel; close it with the panel's *Close database* button when finished.
"""

import html
from pathlib import Path

import ipywidgets as widgets
from IPython.display import display

from esds_apps.attendance import attendance_db, dancer_matching, pseudonyms_db

_NAME_ROWS = (
    ('first_name', 'First name'),
    ('alt_first_name', 'Alt first name'),
    ('last_name', 'Last name'),
    ('alt_last_name', 'Alt last name'),
)
_EMAIL_ROWS = (
    ('email', 'Email'),
    ('alt_email', 'Alt email'),
)

# A self-contained CSS spinner (no FontAwesome/CDN dependency, so it renders anywhere the
# HTML widget does) shown while the O(n²) duplicate scan runs.
_SPINNER = (
    '<span style="display:inline-block;width:14px;height:14px;border:2px solid #ccc;'
    'border-top-color:#333;border-radius:50%;animation:esds-spin 0.7s linear infinite;'
    'vertical-align:middle;margin-right:6px"></span>'
    '<style>@keyframes esds-spin{to{transform:rotate(360deg)}}</style>'
)

# (blob, field, human label) for the fields a merge can keep the discarded value of as an alt.
# One checkbox is offered per field that actually differs between the two records.
_ALT_FIELDS = (
    ('name', 'first_name', 'first name'),
    ('name', 'last_name', 'last name'),
    ('email', 'email', 'email'),
)


def _e(value) -> str:
    return html.escape(str(value)) if value else ''


def _one_line(dancer: dict) -> str:
    """A compact 'First Last <email>' summary for search-result and queue labels."""
    n = dancer.get('name') or {}
    e = dancer.get('email') or {}
    name = ' '.join(filter(None, [n.get('first_name'), n.get('last_name')])) or '(no name)'
    alt = ' / '.join(filter(None, [n.get('alt_first_name'), n.get('alt_last_name')]))
    if alt:
        name += f' (alt {alt})'
    email = e.get('email') or '(no email)'
    return f'{name}  ·  {email}'


def _card_html(label: str, dancer: dict) -> str:
    """A titled table of one dancer's fields, for the side-by-side merge view."""
    n = dancer.get('name') or {}
    e = dancer.get('email') or {}
    rows = ''
    for key, human in _NAME_ROWS:
        if n.get(key):
            rows += f'<tr><td style="color:#888;padding-right:8px">{human}</td><td><b>{_e(n[key])}</b></td></tr>'
    for key, human in _EMAIL_ROWS:
        if e.get(key):
            rows += f'<tr><td style="color:#888;padding-right:8px">{human}</td><td><b>{_e(e[key])}</b></td></tr>'
    if not rows:
        rows = '<tr><td colspan="2"><i>(no name or email)</i></td></tr>'
    return (
        f'<div style="border:1px solid #ccc;border-radius:6px;padding:10px 12px;min-width:280px">'
        f'<div style="font-weight:600;margin-bottom:6px">{_e(label)} — '
        f'<code>{_e(dancer["dancer_id"])}</code></div>'
        f'<table style="border-collapse:collapse">{rows}</table></div>'
    )


class DedupPanel:
    """Holds the open database context and the widgets for both tabs."""

    def __init__(self, db_path: Path, passphrase: str, output_dir: Path):
        self.ctx = pseudonyms_db.open_db(Path(db_path), passphrase)
        self.output_dir = Path(output_dir)
        self.candidates: list[tuple[dict, dict, float]] = []
        self.idx = 0
        self.deleted: set[str] = set()
        self.alt_checks: dict[str, widgets.Checkbox] = {}
        self.edit_id: str | None = None
        self._build()

    # -- shared -------------------------------------------------------------

    def _log(self, message: str, error: bool = False):
        colour = '#b00' if error else '#060'
        with self.log:
            display(widgets.HTML(f'<span style="color:{colour}">{_e(message)}</span>'))

    # -- review-duplicates tab ---------------------------------------------

    def _build_review_tab(self) -> widgets.Widget:
        self.threshold = widgets.FloatSlider(
            value=0.85,
            min=0.5,
            max=1.0,
            step=0.01,
            description='Threshold:',
            continuous_update=False,
            readout_format='.2f',
            style={'description_width': 'initial'},
        )
        self.scan_btn = widgets.Button(description='Scan for duplicates', icon='search', button_style='primary')
        self.scan_btn.on_click(self._on_scan)

        self.progress = widgets.HTML()
        self.cards = widgets.HBox()
        # Per-field "keep discarded value as alt" checkboxes, rebuilt for each candidate.
        self.alt_box = widgets.VBox()
        merge_ab = widgets.Button(
            description='Keep A, remove B',
            button_style='success',
            icon='check',
            tooltip=(
                "Keep A. In the database, A's dancer entry absorbs any extra details from B, B's "
                "attendance / waitlist / teaching rows move onto A (clashes collapsed), and B's entry "
                'is deleted; across the pseudonymised spreadsheets every B id is rewritten to A so '
                'future re-ingests stay consistent. Takes effect immediately — no re-ingest needed.'
            ),
        )
        merge_ba = widgets.Button(
            description='Keep B, remove A',
            button_style='warning',
            icon='check',
            tooltip=(
                "Keep B. In the database, B's dancer entry absorbs any extra details from A, A's "
                "attendance / waitlist / teaching rows move onto B (clashes collapsed), and A's entry "
                'is deleted; across the pseudonymised spreadsheets every A id is rewritten to B so '
                'future re-ingests stay consistent. Takes effect immediately — no re-ingest needed.'
            ),
        )
        skip = widgets.Button(description='Skip', icon='forward', tooltip='Leave both records unchanged')
        merge_ab.on_click(lambda _b: self._merge(keep='a', drop='b'))
        merge_ba.on_click(lambda _b: self._merge(keep='b', drop='a'))
        skip.on_click(self._on_skip)
        self.action_row = widgets.HBox([merge_ab, merge_ba, skip])
        self.action_row.layout.display = 'none'

        return widgets.VBox(
            [
                widgets.HTML('<b>1.</b> Scan the store for likely-duplicate dancers, then work through them.'),
                widgets.HBox([self.threshold, self.scan_btn]),
                self.progress,
                self.cards,
                self.alt_box,
                self.action_row,
            ]
        )

    def _on_scan(self, _btn):
        # Show the spinner and disable the button before the blocking O(n²) scan. Setting the
        # widget traits flushes to the frontend immediately, so the spinner animates while the
        # kernel is busy; the finally re-enables the button even if the scan raises.
        self.scan_btn.disabled = True
        self.cards.children = ()
        self.action_row.layout.display = 'none'
        self.progress.value = f'{_SPINNER}Scanning the store for duplicates…'
        try:
            self.candidates = dancer_matching.find_duplicate_candidates(self.ctx, threshold=self.threshold.value)
        finally:
            self.scan_btn.disabled = False
        self.idx = 0
        self.deleted = set()
        self._log(f'Found {len(self.candidates)} candidate pair(s) at threshold {self.threshold.value:.2f}.')
        self._render_candidate()

    def _current(self) -> tuple[dict, dict, float] | None:
        """Advance past any pair touching an already-merged id, then return the current one."""
        while self.idx < len(self.candidates):
            a, b, score = self.candidates[self.idx]
            if a['dancer_id'] in self.deleted or b['dancer_id'] in self.deleted:
                self.idx += 1
                continue
            return a, b, score
        return None

    def _render_candidate(self):
        pair = self._current()
        if pair is None:
            self.progress.value = '<b>No more candidates.</b> Re-scan to pick up anything left, or lower the threshold.'
            self.cards.children = ()
            self.alt_box.children = ()
            self.action_row.layout.display = 'none'
            return
        a, b, score = pair
        remaining = sum(
            1
            for c in self.candidates[self.idx :]
            if c[0]['dancer_id'] not in self.deleted and c[1]['dancer_id'] not in self.deleted
        )
        self.progress.value = f'<b>Similarity {score:.2f}</b> — {remaining} candidate(s) remaining'
        self.cards.children = (self._card_box('A', a), self._card_box('B', b))
        self._build_alt_checks(a, b)
        self.action_row.layout.display = 'flex'

    def _build_alt_checks(self, a: dict, b: dict):
        """One pre-ticked checkbox per field that differs, to keep the discarded value as an alt."""
        self.alt_checks: dict[str, widgets.Checkbox] = {}
        boxes = []
        for blob, field, human in _ALT_FIELDS:
            av = (a.get(blob) or {}).get(field, '')
            bv = (b.get(blob) or {}).get(field, '')
            if av and bv and av.strip().lower() != bv.strip().lower():
                desc = f'{human.capitalize()} differs (A: “{av}”, B: “{bv}”) — keep the discarded one as an alt'
                cb = widgets.Checkbox(value=True, indent=False, description=desc)
                self.alt_checks[field] = cb
                boxes.append(cb)
        self.alt_box.children = tuple(boxes)

    def _card_box(self, label: str, dancer: dict) -> widgets.Widget:
        """A candidate card with an Edit button that jumps to the edit tab for that dancer."""
        edit_btn = widgets.Button(description=f'Edit {label}', icon='pencil', layout={'width': 'auto'})
        edit_btn.on_click(lambda _b, did=dancer['dancer_id']: self._edit_dancer(did))
        return widgets.VBox([widgets.HTML(_card_html(label, dancer)), edit_btn])

    def _edit_dancer(self, dancer_id: str):
        """Switch to the Edit tab and load this dancer, e.g. to fix a record instead of merging."""
        self.tabs.selected_index = 1
        self.id_box.value = dancer_id
        self._load(dancer_id)

    def _conflict(self, keep: dict, drop: dict, blob: str, field: str) -> str | None:
        """The discarded differing value for one field, or None if it matches or is blank."""
        kept = (keep.get(blob) or {}).get(field, '')
        dropped = (drop.get(blob) or {}).get(field, '')
        if kept and dropped and kept.strip().lower() != dropped.strip().lower():
            return dropped
        return None

    def _merge(self, keep: str, drop: str):
        pair = self._current()
        if pair is None:
            return
        a, b, _score = pair
        survivor, discarded = (a, b) if keep == 'a' else (b, a)
        # Keep a discarded value as an alt only for the fields whose checkbox is ticked.
        conflicts = {'first_name': None, 'last_name': None, 'email': None}
        for blob, field, _human in _ALT_FIELDS:
            cb = self.alt_checks.get(field)
            if cb is not None and cb.value:
                conflicts[field] = self._conflict(survivor, discarded, blob, field)
        # Rewriting every spreadsheet under output_dir is the slow part, so disable the action
        # buttons and show a spinner while it runs (the trait changes flush to the frontend before
        # the blocking call, as with the scan); the finally restores the buttons on success or error.
        for button in self.action_row.children:
            button.disabled = True
        self.progress.value = f'{_SPINNER}Applying the merge, moving attendance and updating spreadsheets…'
        old_id, new_id = discarded['dancer_id'], survivor['dancer_id']
        try:
            # Move the ingested facts first (uncommitted), then fold the identity + rewrite files.
            # substitute_dancer_id's single commit covers both, so the whole merge is atomic.
            moved = attendance_db.reassign_dancer(self.ctx.conn, old_id, new_id)
            pseudonyms_db.substitute_dancer_id(
                self.ctx,
                old_id,
                new_id,
                output_dir=self.output_dir,
                conflict_first_name=conflicts['first_name'],
                conflict_last_name=conflicts['last_name'],
                conflict_email=conflicts['email'],
            )
            self.deleted.add(old_id)
            self._log(
                f'Merged {old_id} into {new_id} — moved {moved["attendance"]} attendance, '
                f'{moved["waitlist"]} waitlist, {moved["event_teacher"]} teacher row(s).'
            )
        except Exception as exc:  # noqa: BLE001 — surface any failure in the panel, keep the loop alive
            self.ctx.conn.rollback()  # discard the uncommitted attendance move if the merge failed
            self._log(f'Merge failed: {exc}', error=True)
        finally:
            for button in self.action_row.children:
                button.disabled = False
        self.idx += 1
        self._render_candidate()

    def _on_skip(self, _btn):
        self.idx += 1
        self._render_candidate()

    # -- edit tab -----------------------------------------------------------

    def _build_edit_tab(self) -> widgets.Widget:
        # continuous_update=False so observing 'value' fires on Enter / blur, not each keystroke —
        # the modern replacement for the deprecated Text.on_submit.
        self.search_box = widgets.Text(
            placeholder='name or email fragment', description='Find:', continuous_update=False
        )
        search_btn = widgets.Button(description='Search', icon='search')
        search_btn.on_click(self._on_search)
        self.search_box.observe(self._on_search, names='value')
        self.results = widgets.Select(options=[], rows=6, layout={'width': '540px'})
        self.results.observe(self._on_result_selected, names='value')

        self.id_box = widgets.Text(placeholder='DNC-XXXXXXXX', description='Load id:', continuous_update=False)
        load_btn = widgets.Button(description='Load', icon='download')
        load_btn.on_click(self._on_load_id)
        self.id_box.observe(self._on_load_id, names='value')

        self.fields = {key: widgets.Text(description=human) for key, human in (*_NAME_ROWS, *_EMAIL_ROWS)}
        self.current_label = widgets.HTML('<i>No dancer loaded.</i>')
        self.save_btn = widgets.Button(description='Save changes', button_style='primary', icon='save', disabled=True)
        self.save_btn.on_click(self._on_save)

        return widgets.VBox(
            [
                widgets.HTML('<b>Find a dancer</b> by search, or load one directly by id.'),
                widgets.HBox([self.search_box, search_btn]),
                self.results,
                widgets.HBox([self.id_box, load_btn]),
                widgets.HTML('<hr style="margin:6px 0">'),
                self.current_label,
                *self.fields.values(),
                self.save_btn,
            ]
        )

    def _on_search(self, _widget):
        query = self.search_box.value.strip()
        if not query:
            return
        matches = dancer_matching.search_dancer(self.ctx, query, threshold=0.6, max_results=15)
        # Option value is the dancer_id; label is the readable summary.
        self.results.options = [(f'{score:.2f}  {_one_line(d)}', d['dancer_id']) for d, score in matches]
        if not matches:
            self._log(f'No matches for {query!r}.')

    def _on_result_selected(self, change):
        if change['new']:
            self._load(change['new'])

    def _on_load_id(self, _widget):
        did = self.id_box.value.strip()
        if did:
            self._load(did)

    def _load(self, dancer_id: str):
        dancer = pseudonyms_db.decrypt_dancer(self.ctx, dancer_id)
        if dancer is None:
            self._log(f'{dancer_id} not found.', error=True)
            return
        self.edit_id = dancer_id
        name = dancer.get('name') or {}
        email = dancer.get('email') or {}
        for key, _human in _NAME_ROWS:
            self.fields[key].value = name.get(key, '')
        for key, _human in _EMAIL_ROWS:
            self.fields[key].value = email.get(key, '')
        self.current_label.value = f'Editing <code>{_e(dancer_id)}</code>'
        self.save_btn.disabled = False

    def _on_save(self, _btn):
        if self.edit_id is None:
            return
        name_fields = {key: self.fields[key].value for key, _h in _NAME_ROWS}
        email_fields = {key: self.fields[key].value for key, _h in _EMAIL_ROWS}
        try:
            updated = pseudonyms_db.update_dancer(self.ctx, self.edit_id, name_fields, email_fields)
            self._log(f'Saved {self.edit_id}: {_one_line(updated)}')
            self._load(self.edit_id)  # reflect the cleaned/normalised stored values
        except Exception as exc:  # noqa: BLE001 — surface the validation error in the panel
            self._log(f'Save failed: {exc}', error=True)

    # -- assembly -----------------------------------------------------------

    def _build(self):
        # overflow + bottom margin so a growing log scrolls inside its own box instead of
        # spilling over the Close button below it.
        self.log = widgets.Output(
            layout={
                'border': '1px solid #ddd',
                'padding': '4px 8px',
                'max_height': '160px',
                'overflow': 'auto',
                'margin': '0 0 10px 0',
            }
        )
        self.tabs = widgets.Tab(children=[self._build_review_tab(), self._build_edit_tab()])
        self.tabs.set_title(0, 'Review duplicates')
        self.tabs.set_title(1, 'Edit a dancer')

        close_btn = widgets.Button(description='Close database', icon='power-off', layout={'width': 'auto'})
        close_btn.on_click(self._on_close)

        self.widget = widgets.VBox([self.tabs, widgets.HTML('<b>Log</b>'), self.log, close_btn])

    def _on_close(self, _btn):
        self.ctx.conn.close()
        self._log('Database connection closed. Re-run the launch cell to reopen.')


def launch(db_path, passphrase: str, output_dir) -> DedupPanel:
    """Open the store and display the de-duplication / edit panel; returns the DedupPanel.

    ``output_dir`` is the root of the pseudonymised xlsx tree: when a merge collapses two ids,
    every workbook under it is rewritten so the discarded id is replaced by the surviving one.
    """
    panel = DedupPanel(Path(db_path), passphrase, Path(output_dir))
    display(panel.widget)
    return panel
