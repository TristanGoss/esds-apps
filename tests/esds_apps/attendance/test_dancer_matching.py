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
