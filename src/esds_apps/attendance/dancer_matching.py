"""Fuzzy matching over the dancer-ID store.

Read-only analysis tools that decrypt the store (via ``pseudonyms_db.decrypt_all``) and
compare records: ``find_duplicate_candidates`` surfaces likely-same people for manual
de-duplication (paired with ``pseudonyms_db.substitute_dancer_id``), and ``search_dancer``
looks one person up by a name or email query. Both compare names, emails and the local part
of emails, so a record holding only a name can still match one holding only an email.
"""

import re

from rapidfuzz import fuzz

from esds_apps.attendance.pseudonyms_db import DbContext, decrypt_all


def _all_emails(dancer: dict) -> list[str]:
    e = dancer.get('email') or {}
    return [e[k].lower() for k in ('email', 'alt_email') if e.get(k)]


def _email_local(email: str) -> str:
    return re.sub(r'[._\-]', ' ', email.split('@')[0])


def _full_name(dancer: dict) -> str:
    n = dancer.get('name') or {}
    return f'{n.get("first_name", "")} {n.get("last_name", "")}'.strip().lower()


def _full_names(dancer: dict) -> list[str]:
    """Every full-name combination from the primary and alt first/last names, lower-cased.

    A record with only a primary name yields one; an alt first *or* last yields two; both an
    alt first *and* last yield four. This is what lets a record that has absorbed a second person
    (their name kept in the alt fields — e.g. a shared-email booking) match that person's own
    record on the right combination, rather than hiding behind its primary name alone.
    """
    n = dancer.get('name') or {}
    firsts = [v for v in (n.get('first_name'), n.get('alt_first_name')) if v and v.strip()]
    lasts = [v for v in (n.get('last_name'), n.get('alt_last_name')) if v and v.strip()]
    names = {f'{first} {last}'.strip().lower() for first in firsts for last in lasts}
    # A record with only first names or only last names still contributes those single tokens.
    if not lasts:
        names.update(first.strip().lower() for first in firsts)
    if not firsts:
        names.update(last.strip().lower() for last in lasts)
    return sorted(names)


def _pair_score(a: tuple, b: tuple) -> float:
    """Best fuzzy match between two prepared (dancer, full_names, emails, email_locals) tuples.

    full_names is the list of all name combinations, so any combination of one record can match
    any combination of the other — the score is the best over every name/name, email/email and
    name/email-local pairing.
    """
    _, a_names, a_emails, a_locals = a
    _, b_names, b_emails, b_locals = b
    best = 0.0
    for an in a_names:
        for bn in b_names:
            best = max(best, fuzz.ratio(an, bn) / 100)
    for ae in a_emails:
        for be in b_emails:
            best = max(best, fuzz.ratio(ae, be) / 100)
    for names, locals_ in ((a_names, b_locals), (b_names, a_locals)):
        for name in names:
            for loc in locals_:
                best = max(best, fuzz.ratio(name, loc) / 100)
    return best


def find_duplicate_candidates(
    ctx: DbContext,
    threshold: float = 0.8,
) -> list[tuple[dict, dict, float]]:
    """Scan the database for pairs of dancers with similar names or emails.

    Returns list of (dancer_a, dancer_b, score) sorted by score descending,
    where score is the best match across name and email.
    """
    # Precompute each dancer's comparison fields once, rather than rebuilding them
    # for every pair (the comparison is O(n²)). Each record contributes every name combination,
    # so a record holding a second person in its alt fields can match that person's own record.
    prepared = [
        (d, _full_names(d), emails, [_email_local(e) for e in emails])
        for d in decrypt_all(ctx)
        for emails in [_all_emails(d)]
    ]

    candidates = []
    for i, a in enumerate(prepared):
        for b in prepared[i + 1 :]:
            score = _pair_score(a, b)
            if score >= threshold:
                candidates.append((a[0], b[0], score))

    return sorted(candidates, key=lambda x: x[2], reverse=True)


def search_dancer(
    ctx: DbContext,
    query: str,
    threshold: float = 0.6,
    max_results: int = 10,
) -> list[tuple[dict, float]]:
    """Fuzzy-search the database for dancers matching a name or email query.

    Compares query against full name, email, and email local part. Returns
    list of (dancer, score) sorted by score descending, capped at max_results.
    """
    q = query.strip().lower()
    q_local = _email_local(q) if '@' in q else q

    results = []
    for dancer in decrypt_all(ctx):
        full_name = _full_name(dancer)

        best = 0.0
        if full_name:
            best = max(best, fuzz.partial_ratio(q, full_name) / 100)
        for email in _all_emails(dancer):
            best = max(best, fuzz.partial_ratio(q, email) / 100)
            best = max(best, fuzz.partial_ratio(q_local, _email_local(email)) / 100)

        if best >= threshold:
            results.append((dancer, best))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:max_results]


def find_conflated_identities(
    ctx: DbContext,
    first_threshold: float = 60.0,
    last_threshold: float = 60.0,
) -> list[tuple[str, float, float]]:
    """Find dancer records that may have conflated two separate people into one id.

    Looks for records carrying *both* a first-name and a surname alt where each alt is very
    different from its primary (fuzz.ratio below the given threshold, 0-100). Requiring both names
    to differ is the discriminator: a nickname changes the first name but not the surname, and a
    marriage changes the surname but not the first name, so those legitimate cases score high on the
    unchanged name and are excluded — whereas a genuinely different person differs on both.

    Returns ``[(dancer_id, first_ratio, last_ratio), ...]`` sorted most-different first. Note it
    cannot catch conflations that share a surname (e.g. two siblings): those need a different signal.
    """
    flagged = []
    for dancer in decrypt_all(ctx):
        name = dancer.get('name') or {}
        first, alt_first = (name.get('first_name') or '').strip(), (name.get('alt_first_name') or '').strip()
        last, alt_last = (name.get('last_name') or '').strip(), (name.get('alt_last_name') or '').strip()
        if not (first and alt_first and last and alt_last):
            continue
        first_ratio = fuzz.ratio(first.lower(), alt_first.lower())
        last_ratio = fuzz.ratio(last.lower(), alt_last.lower())
        if first_ratio < first_threshold and last_ratio < last_threshold:
            flagged.append((dancer['dancer_id'], round(first_ratio, 1), round(last_ratio, 1)))
    return sorted(flagged, key=lambda r: r[1] + r[2])
