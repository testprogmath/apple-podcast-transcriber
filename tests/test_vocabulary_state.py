import pytest

from podcast_bot.storage import Storage


def test_vocabulary_identity_transitions_and_restart(tmp_path):
    store = Storage(tmp_path)
    assert store.vocabulary_state("获得") is None
    for state in ("known", "learning", "known"):
        store.save_vocabulary_state("获得", state)
        assert store.vocabulary_state("获得") == state
    store.save_vocabulary_state("做研究", "learning")
    store.save_vocabulary_state("獲得", "learning")
    store.close()
    store = Storage(tmp_path)
    assert store.vocabulary_states(["获得", "做研究", "獲得", "不存在"]) == {
        "获得": "known",
        "做研究": "learning",
        "獲得": "learning",
    }
    store.delete_vocabulary_state("获得")
    store.delete_vocabulary_state("获得")
    assert store.vocabulary_state("获得") is None
    with pytest.raises(ValueError):
        store.save_vocabulary_state("获得", "unknown")
    store.close()


def test_vocabulary_batch_deduplicates_and_limits_sql_parameters(store):
    glyphs = [f"词{i}" for i in range(1201)]
    store.save_vocabulary_states(glyphs, "learning")
    queries = []
    store.db.set_trace_callback(queries.append)
    assert store.vocabulary_states([]) == {}
    assert queries == []
    assert store.vocabulary_states(glyphs * 3) == dict.fromkeys(glyphs, "learning")
    assert len(queries) == 3
    store.db.set_trace_callback(None)


def test_additive_vocabulary_migration_keeps_reader_documents(tmp_path):
    from podcast_bot.reader.documents import from_text

    store = Storage(tmp_path)
    document = from_text(42, "他们获得了奖。")
    store.save_reader_document(document)
    store.db.execute("DROP TABLE vocabulary_state")
    store.db.commit()
    before = [tuple(row) for row in store.db.execute("SELECT * FROM reader_documents")]
    store.close()
    store = Storage(tmp_path)
    assert [tuple(row) for row in store.db.execute("SELECT * FROM reader_documents")] == before
    assert store.reader_document(document.id) is not None
    assert store.vocabulary_states(["获得"]) == {}
    store.save_vocabulary_state("获得", "known")
    store.close()
