import pytest
from conftest import URL

from podcast_bot.models import Transcript
from podcast_bot.storage import Storage, request_key, safe_name


def enqueue(store, url=URL, force=False):
    return store.enqueue(url, "zh", "gpt-transcribe", "", force, 42, 1)


@pytest.mark.parametrize(
    "value", ["../../bad/file", "..", "CON", "a\x00b", "大鹏" * 100, "a:b*c?", ""]
)
def test_filename(value):
    result = safe_name(value)
    assert result and result not in {".", ".."}
    assert not any(c in result for c in "/\\:*?\x00")
    assert len(result.encode()) <= 120


def test_key_normalization():
    a = request_key(URL, "gpt-transcribe", "zh", "")
    assert a == request_key(
        "https://podcasts.apple.com/us/podcast/other/id1490732024?i=1000789324203",
        "gpt-transcribe",
        "zh",
        "",
    )
    for model, language, hints in [
        ("whisper-1", "zh", ""),
        ("gpt-transcribe", "nl", ""),
        ("gpt-transcribe", "zh", "hint"),
    ]:
        assert a != request_key(URL, model, language, hints)


def test_fifo_duplicates_restarts(store):
    one, position, duplicate = enqueue(store)
    assert (position, duplicate) == (1, False)
    assert enqueue(store)[2] is True
    two, position, _ = enqueue(store, URL.replace("1000789324203", "1000789324204"))
    assert position == 2
    assert store.claim().id == one
    assert store.claim() is None
    store.close()
    reopened = Storage(store.root)
    reopened.recover()
    assert reopened.claim().id == one
    reopened.finish(one, "completed")
    assert reopened.claim().id == two
    reopened.close()
    # Fixture owns the original connection; close() is safe repeatedly.


def test_retry_reuses_job_id(store):
    one, *_ = enqueue(store)
    store.claim()
    store.finish(one, "failed", "test")
    assert store.retry(999, 2) is None
    assert store.retry(42, 2) == one
    assert store.claim().message_id == 2
    assert store.retry(42, 3) is None


def test_cache_files_checked(store, tmp_path):
    enqueue(store)
    job = store.claim()
    path = tmp_path / "out"
    path.mkdir()
    (path / "transcript.txt").write_text("你好。")
    (path / "metadata.json").write_text("{}")
    store.save_output(job, path)
    assert store.cached(job.cache_key) == path
    (path / "transcript.txt").unlink()
    assert store.cached(job.cache_key) is None


def test_checkpoint_and_uncertain_request(store):
    enqueue(store)
    job = store.claim()
    usage = store.start_usage(job, "episode", 60, 0.0045)
    store.save_chunk(job.id, "sha", Transcript("你好。", language="zh"), usage)
    assert store.chunk(job.id, "sha").text == "你好。"
    store.start_usage(job, "episode", 60, None)
    store.recover()
    assert store.has_uncertain_usage(job.id)
    store.finish(job.id, "failed")
    store.retry(42, 4)
    assert not store.has_uncertain_usage(job.id)
    assert (
        store.db.execute("SELECT status FROM usage WHERE id=?", (usage,)).fetchone()[0] == "success"
    )
