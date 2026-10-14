from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from test_reader import call
from test_reader import reader as reader

from podcast_bot.models import UserError
from podcast_bot.reader.npo import NpoSourceAdapter, NpoSubtitleNormalizer, valid_url
from podcast_bot.storage import Storage

URL = "https://npo.nl/start/afspelen/een-dunne-lijn"
VTT = (Path(__file__).parent / "fixtures/npo/minimal.vtt").read_text()


def client(page="VPWON_1361725", status=200, vtt=VTT, content_type="text/vtt"):
    def handle(request):
        if request.url.host == "npo.nl":
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})
        return httpx.Response(status, text=vtt, headers={"content-type": content_type})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.mark.parametrize(
    "url,expected",
    [
        (URL, True),
        (URL.replace("npo.nl", "www.npo.nl"), True),
        ("http://npo.nl/start/afspelen/a", False),
        (URL + "?x=1", False),
        (URL.replace("npo.nl", "npo.nl.evil.org"), False),
        ("https://user@npo.nl/start/afspelen/a", False),
        ("https://npo.nl/other", False),
        ("https://127.0.0.1/start/afspelen/a", False),
    ],
)
def test_recognition(url, expected):
    assert valid_url(url) is expected


@pytest.mark.parametrize(
    "page,error",
    [("none", "product id not found"), ("VPWON_1361725 VPWON_1361722", "ambiguous product id")],
)
async def test_resolution_errors(page, error):
    async with client(page=page) as c:
        with pytest.raises(UserError, match=error):
            await NpoSourceAdapter(c).import_document(42, URL)


async def test_document_persistence_api_and_duplicate(reader, tmp_path):
    async with client(
        page='<meta property="og:title" content="Een dunne lijn">VPWON_1361725 VPWON_1361725'
    ) as c:
        adapter = NpoSourceAdapter(c)
        assert (
            adapter.discover_subtitle("VPWON_1361725")
            == "https://cdn.npoplayer.nl/subtitles/nl/VPWON_1361725.vtt"
        )
        with patch("podcast_bot.reader.api.NpoSourceAdapter", return_value=adapter):
            a = await call(
                reader, "POST", "/api/reader/documents", {"source_type": "npo", "url": URL}
            )
            b = await call(
                reader, "POST", "/api/reader/documents", {"source_type": "npo", "url": URL}
            )
    assert a == b
    d = reader.store.reader_document(a[1]["id"])
    assert d.language == "nl" and d.source_reference == "VPWON_1361725"
    assert d.title == "Een dunne lijn"
    assert d.metadata()["raw_vtt"] == VTT
    sentence = d.sentences()[0]
    assert (
        sentence.text
        == "Met de bevrijding van Nederland komt er een einde aan vijf jaar overheersing door de nazi's."
    )
    assert (sentence.source_start, sentence.source_end) == (9.4, 16.04)
    with patch("podcast_bot.reader.documents.segment", side_effect=AssertionError("Chinese")):
        status, data = await call(reader, "GET", f"/api/reader/{d.id}")
    assert status == 200 and data["language"] == "nl"
    assert data["sentences"][0]["source_end"] == 16.04
    assert data["glossary"] == {}
    store = Storage(tmp_path / "npo.sqlite")
    store.save_reader_document(d)
    store.db.close()
    store = Storage(tmp_path / "npo.sqlite")
    assert store.reader_document(d.id) == d
    store.db.close()


def test_raw_cues():
    cues = NpoSubtitleNormalizer().parse(VTT)
    assert cues[0].classification == "tt888"
    assert cues[2].text == "komt er een einde aan vijf jaar overheersing door de nazi's."
    assert cues[2].classes == ["yellow"]
    assert cues[2].settings == "align:center"
    assert "\n" in cues[2].raw_text
    assert cues[3].classification == "non_speech"
    text, _ = NpoSubtitleNormalizer().reconstruct(cues)
    assert "888" not in text and "(explosie)" not in text
    assert "Jaren van terreur..." in text


def track(items):
    return "WEBVTT\n\n" + "\n\n".join(
        f"00:00:{start:02d}.000 --> 00:00:{end:02d}.000\n{text}" for start, end, text in items
    )


@pytest.mark.parametrize(
    "items,count",
    [
        ([(1, 2, "Eerste zin."), (2, 3, "Tweede zin.")], 2),
        ([(1, 2, "Begin..."), (10, 11, "vervolg.")], 2),
        ([(1, 2, "Begin..."), (2, 3, "(explosie)"), (3, 4, "vervolg.")], 2),
        ([(1, 2, "Begin..."), (2, 3, "JAN: vervolg.")], 2),
        ([(1, 2, "Begin..."), (2, 3, "- vervolg.")], 2),
        ([(1, 2, "888"), (2, 3, "(misschien)")], 1),
        ([(3, 4, "888")], 1),
    ],
)
def test_boundaries(items, count):
    n = NpoSubtitleNormalizer()
    assert len(n.reconstruct(n.parse(track(items)))[1]) == count


@pytest.mark.parametrize(
    "raw",
    [
        "html",
        "WEBVTT\n\nbad",
        "WEBVTT\n\n00:00:99.000 --> 00:01:00.000\na",
        "WEBVTT\n\n00:00:02.000 --> 00:00:01.000\na",
    ],
)
def test_malformed(raw):
    with pytest.raises(UserError, match="invalid vtt"):
        NpoSubtitleNormalizer().parse(raw)


@pytest.mark.parametrize(
    "status,vtt,kind,error",
    [
        (404, "", "text/vtt", "subtitle not found"),
        (200, "html", "text/vtt", "invalid vtt"),
        (200, VTT, "text/html", "unexpected subtitle content type"),
        (200, "WEBVTT\n", "text/vtt", "empty subtitle track"),
    ],
)
async def test_subtitle_errors(status, vtt, kind, error):
    async with client(status=status, vtt=vtt, content_type=kind) as c:
        with pytest.raises(UserError, match=error):
            await NpoSourceAdapter(c).import_document(42, URL)


@pytest.mark.parametrize("stage", ["page", "subtitle"])
async def test_network_failure(stage):
    def handle(request):
        if stage == "subtitle" and request.url.host == "npo.nl":
            return httpx.Response(200, text="VPWON_1361725", headers={"content-type": "text/html"})
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
        with pytest.raises(UserError, match="fetch failed"):
            await NpoSourceAdapter(c).import_document(42, URL)


async def test_redirect_ssrf():
    seen = []

    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
        with pytest.raises(UserError, match="redirect rejected"):
            await NpoSourceAdapter(c).import_document(42, URL)
    assert seen == [URL]


async def test_dutch_translation_uses_existing_service(reader):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from podcast_bot.reader.translations import SentenceTranslations, TranslationResult

    async with client() as c:
        document = await NpoSourceAdapter(c).import_document(42, URL)
    mock = SimpleNamespace(
        request=AsyncMock(
            return_value=(
                TranslationResult(
                    translation="С освобождением Нидерландов заканчивается оккупация."
                ),
                {},
            )
        )
    )
    service = SentenceTranslations(reader.store, mock)
    result = await service.resolve(document, document.sentences()[0])
    assert result["source"] == "generated"
    assert mock.request.await_count == 1


async def test_safe_redirect_and_subtitle_redirect_rejected():
    def handle(request):
        if request.url.path.endswith("een-dunne-lijn"):
            return httpx.Response(302, headers={"location": "/start/afspelen/new-slug"})
        if request.url.host == "npo.nl":
            return httpx.Response(200, text="VPWON_1361725", headers={"content-type": "text/html"})
        return httpx.Response(302, headers={"location": "https://evil.example/sub.vtt"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
        with pytest.raises(UserError, match="subtitle redirect rejected"):
            await NpoSourceAdapter(c).import_document(42, URL)


def test_migration_existing_database(tmp_path):
    import sqlite3

    path = tmp_path / "bot.sqlite3"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE reader_documents (id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, title TEXT NOT NULL, source_type TEXT NOT NULL, source_reference TEXT NOT NULL, raw_text TEXT NOT NULL, hanly_key TEXT NOT NULL, mosaic_key TEXT NOT NULL, created TEXT NOT NULL)"
    )
    db.execute(
        "INSERT INTO reader_documents VALUES ('old',42,'Old','pasted_text','','中文。','h','m','2026')"
    )
    db.commit()
    db.close()
    store = Storage(tmp_path)
    assert store.reader_document("old").language == "zh"
    assert store.reader_document("old").source_data == "{}"
    store.db.close()


@pytest.mark.parametrize("digits", ["123456", "1361725", "12345678", "1", "1" * 20])
async def test_product_lengths(digits):
    product = "VPWON_" + digits
    async with client(page=f'"{product}" "{product}"') as c:
        adapter = NpoSourceAdapter(c)
        resolved, _, _ = await adapter.resolve_product_id(c, URL)
        assert resolved == product
        assert adapter.discover_subtitle(product).endswith(f"/{product}.vtt")


@pytest.mark.parametrize(
    "product",
    [
        "VPWON_",
        "VPWON_abc",
        "VPWON_123456x",
        "xVPWON_123456",
        "VPWON_123456_",
        "VPWON_１２３４５６",
        "VPWON_" + "1" * 21,
    ],
)
async def test_malformed_products(product):
    async with client(page=product) as c:
        adapter = NpoSourceAdapter(c)
        with pytest.raises(UserError, match="product id not found"):
            await adapter.resolve_product_id(c, URL)
        with pytest.raises(UserError, match="product id not found"):
            adapter.discover_subtitle(product)


async def test_variable_length_ambiguity():
    async with client(page="VPWON_123456 VPWON_12345678") as c:
        with pytest.raises(UserError, match="ambiguous product id"):
            await NpoSourceAdapter(c).resolve_product_id(c, URL)


@pytest.mark.parametrize("description", ["(het Wilhelmus speelt)", "(schoten)", "(taptoe-signaal)"])
async def test_new_non_speech_preserved(description):
    vtt = track([(3, 4, description), (4, 5, "Hallo.")])
    async with client(vtt=vtt) as c:
        d = await NpoSourceAdapter(c).import_document(42, URL)
    assert d.raw_text == "Hallo."
    assert d.metadata()["raw_vtt"] == vtt
    assert d.metadata()["cues"][0]["raw_text"] == description
    assert d.metadata()["cues"][0]["classification"] == "non_speech"


@pytest.mark.parametrize(
    "text",
    [
        "(misschien)",
        "(ik hoor schoten)",
        "(het Wilhelmus speelt in mijn hoofd)",
        "(schoten) Ga weg!",
    ],
)
def test_parenthetical_dialogue_retained(text):
    n = NpoSubtitleNormalizer()
    assert n.reconstruct(n.parse(track([(3, 4, text)])))[0] == text


def test_real_ellipsis_enumeration():
    raw = """WEBVTT

00:00:17.840 --> 00:00:19.640
<c.yellow>Jaren van terreur...</c>

00:00:21.320 --> 00:00:22.800
<c.yellow>repressie...</c>

00:00:24.800 --> 00:00:26.280
<c.yellow>en wanhoop.</c>
"""
    n = NpoSubtitleNormalizer()
    text, sentences = n.reconstruct(n.parse(raw))
    assert text == "Jaren van terreur, repressie en wanhoop."
    assert len(sentences) == 1
    assert (sentences[0].source_start, sentences[0].source_end) == (17.84, 26.28)


@pytest.mark.parametrize(
    "first,next_text",
    [
        ("Begin", "en einde."),
        ("Begin.", "en einde."),
        ("Begin...", "vervolg."),
        ("Begin...", "En einde."),
        ("Begin...", "JAN: en einde."),
        ("Begin...", "- en einde."),
        ("Begin...", "* en einde."),
        ("* Begin...", "en einde."),
        ("<c.yellow>Begin...</c>", "<c.white>en einde.</c>"),
    ],
)
def test_unsafe_larger_gap_refused(first, next_text):
    n = NpoSubtitleNormalizer()
    assert len(n.reconstruct(n.parse(track([(3, 4, first), (6, 7, next_text)])))[1]) == 2


def test_ellipsis_gap_bound_and_non_speech_barrier():
    n = NpoSubtitleNormalizer()
    for items in [
        [(3, 4, "Begin..."), (7, 8, "en einde.")],
        [(3, 4, "Begin..."), (4, 5, "(schoten)"), (6, 7, "en einde.")],
        [(3, 5, "Begin..."), (4, 6, "en einde.")],
    ]:
        assert len(n.reconstruct(n.parse(track(items)))[1]) == 2


@pytest.mark.parametrize(
    "raw,visible",
    [
        ("*Ik ga vrijwillig voor mijn land", "Ik ga vrijwillig voor mijn land"),
        ("*Vaarwel Marie*", "Vaarwel Marie"),
        ("* Hallo wereld *", "Hallo wereld"),
        ("Hallo wereld *", "Hallo wereld"),
        ("2 * 3 = 6", "2 * 3 = 6"),
        ("*A * B", "*A * B"),
        ("*nadruk*", "*nadruk*"),
        ("bestand*.txt", "bestand*.txt"),
        ("**Hallo wereld**", "**Hallo wereld**"),
    ],
)
def test_lyrics_markers_preserve_raw(raw, visible):
    n = NpoSubtitleNormalizer()
    cues = n.parse(track([(3, 4, raw)]))
    assert cues[0].raw_text == raw
    assert cues[0].text == visible
    assert n.reconstruct(cues)[0] == visible


@pytest.mark.parametrize(
    "url", [URL, "https://npo.nl/start/afspelen/het-verhaal-van-nederland-de-tweede-wereldoorlog_2"]
)
async def test_telegram_npo_import_opens_persisted_reader(config, store, url):
    from dataclasses import replace
    from types import SimpleNamespace

    from test_reader import update

    from podcast_bot.bot import BotHandlers

    handlers = BotHandlers(replace(config, reader_url="https://reader.example"), store)
    message = update("  " + url + "\n")
    async with client() as c:
        with patch("podcast_bot.bot.NpoSourceAdapter", return_value=NpoSourceAdapter(c)):
            with patch("podcast_bot.bot.from_text", side_effect=AssertionError("Chinese path")):
                await handlers.handle(message, SimpleNamespace())
                await handlers.handle(message, SimpleNamespace())
    d = store.recent_reader_document(42)
    assert d.source_type == "npo" and d.language == "nl"
    assert d.metadata()["original_url"] == url
    assert d.sentences()[0].source_start == 9.4
    assert len(store.reader_library(42)) == 1
    reply = message.effective_message.reply_text.call_args
    assert (
        reply.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .web_app.url.endswith(f"/reader/?doc={d.id}")
    )
    assert "Chinese" not in reply.args[0] and "Hanly" not in reply.args[0]
    assert store.claim() is None


async def test_telegram_npo_errors_and_authorization(config, store):
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from test_reader import update

    from podcast_bot.bot import BotHandlers

    adapter = NpoSourceAdapter()
    adapter.import_document = AsyncMock(side_effect=UserError("NPO import: subtitle not found."))
    enabled = replace(config, reader_url="https://reader.example")
    with patch("podcast_bot.bot.NpoSourceAdapter", return_value=adapter):
        denied = update(URL, 99)
        await BotHandlers(enabled, store).handle(denied, SimpleNamespace())
        denied.effective_message.reply_text.assert_not_called()
        adapter.import_document.assert_not_called()
        disabled = update(URL)
        await BotHandlers(replace(enabled, reader_enabled=False), store).handle(
            disabled, SimpleNamespace()
        )
        adapter.import_document.assert_not_called()
        missing_url = update(URL)
        await BotHandlers(replace(enabled, reader_url=""), store).handle(
            missing_url, SimpleNamespace()
        )
        adapter.import_document.assert_not_called()
        message = update(URL)
        await BotHandlers(enabled, store).handle(message, SimpleNamespace())
        assert (
            message.effective_message.reply_text.call_args.args[0]
            == "NPO import: subtitle not found."
        )
    assert store.recent_reader_document(42) is None
