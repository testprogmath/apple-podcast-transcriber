# Development and validation

[Back to the README](../README.md)

```sh
python -m pip install -r requirements.lock -e '.[test]'
python -m pytest -q
ruff check .
ruff format --check .
python -m compileall -q podcast_bot
node tests/frontend/app.test.js
```

The Mini App has no build step and no framework, so its behaviour is covered by a small DOM shim in `tests/frontend/` run with the Node.js already present on the CI runner. It exercises token rendering, the lexical sheet, explicit selection, the pinyin toggle and its persistence, and both upload outcomes.

For local fixes, run `ruff check --fix .` and `ruff format .`. Ruff targets Python 3.12 with E/F (errors), I (imports), UP (modern syntax), B (likely bugs), and SIM (simplifications). Formatting handles layout; E501 is excluded for long literal messages/SQL.

CI runs lint, formatting, and `python -m pytest -q` on pull requests and pushes to `main`, using Python 3.12 and ffmpeg without service credentials. Dependabot checks Python and Actions weekly, groups minor/patch updates, and leaves major updates separate. No updates auto-merge.

The existing pip install command remains unchanged. Exact production pins live in `requirements.txt` so Dependabot's pip ecosystem can discover them; `requirements.lock` includes it as a compatibility entry point, with no duplicate pins. When regenerating pins with the existing tool, run `uv pip compile pyproject.toml -o requirements.txt`. Ruff remains in the existing `test` development extra; no additional package manager is required for installation or CI.

Repository settings: enable Actions and Dependabot alerts/security updates if disabled. Require **Python quality and tests** in a branch ruleset for `main` to enforce CI before merging; committed YAML does not set repository rules.

Tests block unexpected networking and mock OpenAI, Telegram, Mandarin Mosaic, and Firebase/Firestore APIs. Real ffmpeg tests use generated local audio. See `VALIDATION.md` for results and validation boundaries. No paid call is required to run tests.

Modules: `resolver/`, `transcription/`, `reader/` (sentence parsing, lexical segmentation, documents, init-data validation, HTTP API/server, static Mini App), `study/` (typed schemas, source validation/chunking, prompts, API adapter/checkpoints, selection, renderers), plus `pipeline.py`, `storage.py`, `queue.py`, and `bot.py`. Additional derived outputs can be added within `study/` without changing speech recognition.

## Documentation sources

- [Apple public Search API](https://performance-partners.apple.com/search-api)
- [OpenAI file transcription](https://developers.openai.com/api/docs/guides/speech-to-text)
- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [GPT-5.4 mini model and pricing](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
- [OpenAI pricing](https://developers.openai.com/api/docs/pricing)
- [Telegram BotFather](https://core.telegram.org/bots/features#botfather)
- [Telegram Mini Apps and initData validation](https://core.telegram.org/bots/webapps)
- [CC-CEDICT, published by MDBG under CC BY-SA 4.0](https://www.mdbg.net/chinese/dictionary?page=cc-cedict)

- [Firestore PATCH and update masks](https://firebase.google.com/docs/firestore/reference/rest/v1/projects.databases.documents/patch)
- [Firestore update-time preconditions](https://firebase.google.com/docs/firestore/reference/rest/v1/Precondition)
