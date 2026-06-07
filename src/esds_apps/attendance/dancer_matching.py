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


def _pair_score(a: tuple, b: tuple) -> float:
    """Best fuzzy match between two prepared (dancer, full_name, emails, email_locals) tuples."""
    _, a_full, a_emails, a_locals = a
    _, b_full, b_emails, b_locals = b
    best = 0.0
    if a_full and b_full:
        best = fuzz.ratio(a_full, b_full) / 100
    for ae in a_emails:
        for be in b_emails:
            best = max(best, fuzz.ratio(ae, be) / 100)
    for full, locals_ in ((a_full, b_locals), (b_full, a_locals)):
        if full:
            for loc in locals_:
                best = max(best, fuzz.ratio(full, loc) / 100)
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
    # for every pair (the comparison is O(n²)).
    prepared = [
        (d, _full_name(d), emails, [_email_local(e) for e in emails])
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
