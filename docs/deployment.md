# Deployment and releases

[Back to the README](../README.md)

## Automatic deployment

Successful CI runs for `main` build and deploy the exact tested commit through a restricted
SSH command. Pull requests run checks only. Deployment drains the worker, preserves the
SQLite/transcript/authentication volumes, checks application readiness and rolls back an
unhealthy image. See [deploy/README.md](../deploy/README.md) for one-time setup, required
`PODCAST_DEPLOY_SSH_KEY` secret, rollback limitations and manual recovery.

Any branch or tag can also be released deliberately: **Actions → CI → Run workflow**, pick the
ref, tick **Deploy this ref to production**. The checks run first either way, and the box is
unticked by default, so dispatching CI to re-run tests on a branch cannot ship anything by
accident. There is one bot and one database, so this replaces production rather than standing
a preview beside it; `/status` names the running release and the previous image is one command
away (see [deploy/README.md](../deploy/README.md)).

## Releases

`pyproject.toml` holds the version and everything else follows it: the git tag, the
`org.opencontainers.image.version` label on the released image, and the `Version:` line in
`/status`. **Actions → Release → Run workflow** on `main` takes `patch`, `minor` or `major`,
raises that part with `tools/bump_version.py`, commits `chore(release): vX.Y.Z`, pushes an
annotated `vX.Y.Z` tag and opens a GitHub release for it whose notes are generated from the pull
requests merged since the previous one.

Tagging never deploys. A push made with the workflow's own token deliberately does not cascade
into another workflow, so releasing and shipping stay two separate decisions: tag first, then
dispatch CI on the new tag when you want it live. A tag created before this workflow existed
cannot be dispatched, because a dispatched run uses the workflow file from the ref it runs on.
