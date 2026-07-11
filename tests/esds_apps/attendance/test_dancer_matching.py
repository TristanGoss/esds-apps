from esds_apps.attendance import dancer_matching, pseudonyms_db

# ============================================================================
# Fuzzy duplicate detection
# ============================================================================


def test_find_duplicate_candidates_finds_similar_names(ctx):
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smyth'}, None)
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Bob', 'last_name': 'Jones'}, None)
    candidates = dancer_matching.find_duplicate_candidates(ctx, threshold=0.7)
    last_names = {(c[0]['name']['last_name'], c[1]['name']['last_name']) for c in candidates}
    assert ('Smith', 'Smyth') in last_names or ('Smyth', 'Smith') in last_names


def test_find_duplicate_candidates_sorted_by_score(ctx):
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smyth'}, None)
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Ali', 'last_name': 'Smit'}, None)
    candidates = dancer_matching.find_duplicate_candidates(ctx, threshold=0.5)
    scores = [c[2] for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_find_duplicate_candidates_finds_similar_emails(ctx):
    pseudonyms_db.get_or_create_dancer_id(ctx, None, {'email': 'alice.smith@example.com'})
    pseudonyms_db.get_or_create_dancer_id(ctx, None, {'email': 'alice.smyth@example.com'})
    candidates = dancer_matching.find_duplicate_candidates(ctx, threshold=0.8)
    assert len(candidates) == 1


def test_find_duplicate_candidates_no_false_positives(ctx):
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Bob', 'last_name': 'Jones'}, None)
    candidates = dancer_matching.find_duplicate_candidates(ctx, threshold=0.9)
    assert len(candidates) == 0


def test_find_duplicate_candidates_name_vs_email_local(ctx):
    # One record has only a name; the other has only an email whose local part encodes the same name.
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Chris', 'last_name': 'Leeson'}, None)
    pseudonyms_db.get_or_create_dancer_id(ctx, None, {'email': 'chris.leeson@example.com'})
    candidates = dancer_matching.find_duplicate_candidates(ctx, threshold=0.8)
    assert len(candidates) == 1


def test_find_duplicate_candidates_considers_alt_email(ctx):
    """A match on alt_email should surface as a duplicate candidate."""
    id1 = pseudonyms_db.get_or_create_dancer_id(
        ctx, {'first_name': 'Dave', 'last_name': 'Brown'}, {'email': 'dave@work.com'}
    )
    # Manually store alt_email via a second encounter.
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Dave', 'last_name': 'Brown'}, {'email': 'dave@home.com'})
    # A second record whose primary email matches the alt_email of id1.
    pseudonyms_db.get_or_create_dancer_id(
        ctx, {'first_name': 'David', 'last_name': 'Browne'}, {'email': 'dave@home.com'}
    )
    # id1 now has alt_email=dave@home.com; the third record has email=dave@home.com — should score high.
    candidates = dancer_matching.find_duplicate_candidates(ctx, threshold=0.8)
    ids_in_candidates = {c[0]['dancer_id'] for c in candidates} | {c[1]['dancer_id'] for c in candidates}
    assert id1 in ids_in_candidates


# ============================================================================
# Dancer search
# ============================================================================


def test_search_dancer_finds_by_name(ctx):
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Bob', 'last_name': 'Jones'}, None)
    results = dancer_matching.search_dancer(ctx, 'alice smith', threshold=0.9)
    assert len(results) == 1
    assert results[0][0]['name']['first_name'] == 'Alice'


def test_search_dancer_partial_query_scores_high(ctx):
    """A prefix query should score ~1.0 via partial ratio, not be penalised for length."""
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    results = dancer_matching.search_dancer(ctx, 'alice', threshold=0.9)
    assert len(results) == 1
    assert results[0][1] >= 0.9


def test_search_dancer_finds_by_email_local(ctx):
    pseudonyms_db.get_or_create_dancer_id(ctx, None, {'email': 'alice.smith@example.com'})
    pseudonyms_db.get_or_create_dancer_id(ctx, None, {'email': 'bob.jones@example.com'})
    results = dancer_matching.search_dancer(ctx, 'alice smith', threshold=0.9)
    assert len(results) == 1
    assert results[0][0]['email']['email'] == 'alice.smith@example.com'


def test_search_dancer_sorted_by_score(ctx):
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Ali', 'last_name': 'Smit'}, None)
    results = dancer_matching.search_dancer(ctx, 'alice smith', threshold=0.5)
    assert len(results) == 2
    assert results[0][1] >= results[1][1]


def test_search_dancer_max_results(ctx):
    for i in range(5):
        pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': f'Alice{i}', 'last_name': 'Smith'}, None)
    results = dancer_matching.search_dancer(ctx, 'alice smith', threshold=0.5, max_results=3)
    assert len(results) <= 3


def test_search_dancer_no_results(ctx):
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Bob', 'last_name': 'Jones'}, None)
    results = dancer_matching.search_dancer(ctx, 'zzz', threshold=0.9)
    assert results == []


def test_search_dancer_finds_by_alt_email(ctx):
    did = pseudonyms_db.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@work.com'}
    )
    # Trigger alt_email storage.
    pseudonyms_db.get_or_create_dancer_id(
        ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, {'email': 'alice@home.com'}
    )
    results = dancer_matching.search_dancer(ctx, 'alice@home.com', threshold=0.9)
    assert len(results) == 1
    assert results[0][0]['dancer_id'] == did


# ---- find_conflated_identities (one id representing two people) ----


def _dancer_with_names(ctx, first, last, alt_first=None, alt_last=None):
    """Create a dancer, then set alt name fields via update_dancer, and return its id."""
    did = pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': first, 'last_name': last}, None)
    fields = {'first_name': first, 'last_name': last}
    if alt_first:
        fields['alt_first_name'] = alt_first
    if alt_last:
        fields['alt_last_name'] = alt_last
    pseudonyms_db.update_dancer(ctx, did, fields, None)
    return did


def test_conflated_flags_two_different_people(ctx):
    did = _dancer_with_names(ctx, 'John', 'Smith', alt_first='Priya', alt_last='Okafor')
    flagged = [f[0] for f in dancer_matching.find_conflated_identities(ctx)]
    assert did in flagged


def test_conflated_ignores_marriage_surname_change(ctx):
    """Same first name, different surname (no alt first name) is not a conflation."""
    did = _dancer_with_names(ctx, 'Alice', 'Smith', alt_last='Jones')
    assert did not in [f[0] for f in dancer_matching.find_conflated_identities(ctx)]


def test_conflated_ignores_similar_variants(ctx):
    """Both names present but each a near-spelling of its primary stays below the difference bar."""
    did = _dancer_with_names(ctx, 'Alice', 'Smith', alt_first='Alyce', alt_last='Smyth')
    assert did not in [f[0] for f in dancer_matching.find_conflated_identities(ctx, 60, 60)]


def test_conflated_requires_both_alts(ctx):
    """A very different first name alone (shared surname) is out of scope for this metric."""
    did = _dancer_with_names(ctx, 'John', 'Smith', alt_first='Priya')  # no alt surname
    assert did not in [f[0] for f in dancer_matching.find_conflated_identities(ctx)]


def test_conflated_sorted_most_different_first(ctx):
    near = _dancer_with_names(ctx, 'Jon', 'Smith', alt_first='Jan', alt_last='Smyth')  # both differ, but mildly
    far = _dancer_with_names(ctx, 'John', 'Smith', alt_first='Priya', alt_last='Okafor')  # very different
    ids = [f[0] for f in dancer_matching.find_conflated_identities(ctx, 90, 90)]
    assert set(ids) >= {near, far}  # both fall below the (generous) 90 bar
    assert ids[0] == far  # lowest combined similarity comes first


# ---- name-combination matching (finds a person hidden in another record's alt fields) ----


def test_find_duplicate_matches_person_hidden_in_alt_fields(ctx):
    """A record blended via a shared email (second person in its alts) matches that person's own record."""
    blended = _dancer_with_names(ctx, 'John', 'Smith', alt_first='Priya', alt_last='Okafor')
    true_record = pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Priya', 'last_name': 'Okafor'}, None)
    pairs = dancer_matching.find_duplicate_candidates(ctx, threshold=0.95)
    matched = {frozenset((a['dancer_id'], b['dancer_id'])) for a, b, _ in pairs}
    assert frozenset((blended, true_record)) in matched


def test_find_duplicate_matches_single_alt_combination(ctx):
    """One alt (surname only) gives two full names; the crossed combination still matches."""
    blended = _dancer_with_names(ctx, 'John', 'Smith', alt_last='Okafor')  # -> 'john smith', 'john okafor'
    other = pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'John', 'last_name': 'Okafor'}, None)
    pairs = dancer_matching.find_duplicate_candidates(ctx, threshold=0.95)
    matched = {frozenset((a['dancer_id'], b['dancer_id'])) for a, b, _ in pairs}
    assert frozenset((blended, other)) in matched


def test_full_names_combinations():
    assert dancer_matching._full_names({'name': {'first_name': 'John', 'last_name': 'Smith'}}) == ['john smith']
    both = dancer_matching._full_names(
        {'name': {'first_name': 'John', 'last_name': 'Smith', 'alt_first_name': 'Priya', 'alt_last_name': 'Okafor'}}
    )
    assert set(both) == {'john smith', 'john okafor', 'priya smith', 'priya okafor'}


def test_full_names_ignores_unrelated_pair_still(ctx):
    """Two genuinely different people with no shared name combination don't match."""
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Alice', 'last_name': 'Smith'}, None)
    pseudonyms_db.get_or_create_dancer_id(ctx, {'first_name': 'Bob', 'last_name': 'Jones'}, None)
    assert dancer_matching.find_duplicate_candidates(ctx, threshold=0.9) == []
