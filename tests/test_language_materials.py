import hashlib
import zipfile
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import test_study
from test_study import enqueue_study

from podcast_bot.bot import send_files, setup_command_menu
from podcast_bot.integrations import StudyUploads
from podcast_bot.models import UserError
from podcast_bot.pipeline import completion
from podcast_bot.study.chunking import split_blocks
from podcast_bot.study.lexical import LexicalChunk, validate_lexical
from podcast_bot.study.models import Selection
from podcast_bot.study.service import StudyService, pack_valid
from podcast_bot.study.settings import StudySettings, study_key


@pytest.fixture
def canonical(store):
    return test_study.canonical.__wrapped__(store)


async def test_command_menu_scoped_to_owner():
    bot = SimpleNamespace(set_my_commands=AsyncMock(), set_chat_menu_button=AsyncMock())
    await setup_command_menu(bot, 42)
    args = bot.set_my_commands.call_args
    assert args.kwargs["scope"].chat_id == 42
    assert {c.command for c in args.args[0]} == {
        "help",
        "status",
        "transcribe",
        "language",
        "level",
        "native",
        "regenerate",
        "zip",
        "reader",
        "hanly",
        "mosaic",
        "retry",
        "force",
        "start",
    }
    assert bot.set_chat_menu_button.call_args.kwargs["menu_button"].type == "commands"


@pytest.mark.parametrize(
    "language,source,term",
    [
        ("en", "We need to figure out what happened.", "figure out"),
        ("de", "Das kommt darauf an.", "kommt darauf an"),
        ("nl", "Dat komt goed uit.", "komt goed uit"),
    ],
)
async def test_non_zh_generates_only_lexical_materials(store, canonical, language, source, term):
    (canonical / "transcript.txt").write_text(source)
    settings = replace(StudySettings(), target_language=language)
    schemas = []

    async def call(**kwargs):
        schemas.append(kwargs["schema"])
        if kwargs["schema"] is Selection:
            result = Selection(selected_ids=[0])
        else:
            assert kwargs["schema"] is LexicalChunk
            result = LexicalChunk(
                vocabulary=[
                    {
                        "term": term,
                        "meaning": "Значение выражения",
                        "register_note": "разговорное",
                        "example": source,
                        "example_translation": "Перевод примера",
                        "usage": "Полезное выражение",
                        "tags": ["expression"],
                    }
                ]
            )
        kwargs["validate"](result)
        return result

    svc = StudyService(store, SimpleNamespace(call=call))
    pack = await svc.generate(
        canonical, settings, enqueue_study(store, canonical, settings), AsyncMock()
    )
    assert schemas == [LexicalChunk, Selection]
    assert pack_valid(pack)
    assert (canonical / "transcript.txt").read_text() == source
    assert (pack / "transcript.txt").read_text() == source
    assert term in (pack / "vocabulary.md").read_text()
    assert not any(
        (pack / name).exists()
        for name in (
            "study.md",
            "reader.md",
            "translation_ru.md",
            "hanly.csv",
            "mandarin_mosaic.csv",
            "transcript_pinyin.md",
        )
    )
    with zipfile.ZipFile(next(pack.glob("*.zip"))) as z:
        assert set(z.namelist()) == {
            "transcript.txt",
            "vocabulary.md",
            "study.json",
            "metadata.json",
        }
    bot = SimpleNamespace(send_media_group=AsyncMock())
    await send_files(bot, 42, pack)
    assert [m.media.filename for m in bot.send_media_group.call_args.kwargs["media"]] == [
        "transcript.txt",
        "vocabulary.md",
    ]
    text = completion(pack)
    assert "Translation" not in text and "Mosaic" not in text and "grammar" not in text
    hanly = SimpleNamespace(upload=AsyncMock())
    mosaic = SimpleNamespace(create_study_pack=AsyncMock())
    assert await StudyUploads(hanly, mosaic).run(pack) == ""
    hanly.upload.assert_not_called()
    mosaic.create_study_pack.assert_not_called()
    # Old non-zh full packs cannot shadow the new vocabulary-only cache.
    old_key = hashlib.sha256(source.encode() + b"\0" + settings.to_json().encode()).hexdigest()
    assert study_key(source.encode(), settings) != old_key


def test_lexical_rejects_fabricated_example():
    item = {
        "term": "figure out",
        "meaning": "понять",
        "register_note": "разговорное",
        "example": "This is invented.",
        "example_translation": "пример",
        "usage": "пример",
        "tags": [],
    }
    with pytest.raises(UserError):
        validate_lexical(
            LexicalChunk(vocabulary=[item]), split_blocks("We need to figure out what happened.")
        )
