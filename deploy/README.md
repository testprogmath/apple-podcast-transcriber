# Production deployment

The `CI` workflow deploys **pushes to main**, and any ref dispatched with the **Deploy this ref
to production** box ticked, after `Python quality and tests` succeeds. PRs never receive the
deployment secret, and neither `pull_request_target` nor `issue_comment` is used. GitHub builds
a SHA-tagged image, then streams it through SSH to a forced command. No registry credentials or
passwordless sudo are needed.

## Two deployment modes

The forced command accepts `deploy <sha>` and `deploy <sha> manual`. Automatic releases send the
first form and the helper refuses any SHA that is not the current tip of `main`, which is what
keeps out-of-order CI runs from moving production backwards. A dispatched release sends the
second form: a person chose that ref in the Actions UI, so the staleness question does not apply
and the check is skipped. Everything else is identical, including draining, the SQLite backup,
the image revision check, readiness and rollback. Both modes serialise on the same file lock, so
a dispatched branch and an automatic main release cannot interleave; the later one simply wins.

Dispatching is gated by write access to the repository, the same permission as merging to main.
Add required reviewers to the `production` environment if that should be a second pair of eyes.

**Update `podcast-receive.py` on the server before using a dispatched release.** Releases never
replace this helper. The new grammar still accepts the old `deploy <sha>` command, so installing
it early is safe; an old helper receiving `deploy <sha> manual` refuses the command and fails the
workflow loudly rather than deploying the wrong thing.

```bash
scp deploy/receive.py you@server:/tmp/podcast-receive.py
ssh you@server 'sudo install -o deploy -g deploy -m 0700 /tmp/podcast-receive.py \
  /home/deploy/bin/podcast-receive.py && rm /tmp/podcast-receive.py'
```

## One-time server setup

Install `receive.py` as `/home/deploy/bin/podcast-receive.py` (mode 0700), outside the application
checkout. This helper is not replaced by incoming releases. Append a dedicated public key to
`/home/deploy/.ssh/authorized_keys` with this prefix:

```
restrict,command="/usr/bin/python3 /home/deploy/bin/podcast-receive.py" ssh-ed25519 …
```

Add the matching private key as the `PODCAST_DEPLOY_SSH_KEY` Actions secret in this repository.
`deploy/known_hosts` pins the existing server host key. Verify a changed host fingerprint through
an independent trusted connection before updating it; never disable host-key checking.

The helper preserves the Compose file set recorded on the running container, including
optional Hanly and БКРС dictionary overrides, `.env`, `data/`, and external auth directories. It creates `docker-compose.release.yml` containing
only an image override. Compose/service configuration changes require a reviewed server update;
automatic releases replace the application image only. Include the release override in manual
Compose commands or use the deployment helper, otherwise Compose can select the older local image.

## Queue draining and rollback

Deployments are serialized on GitHub and by a server file lock. Stale SHAs are skipped when main
has advanced. The helper creates `data/deploy-drain` under the same SQLite write lock as job
claiming, waits up to 30 minutes for running jobs, and leaves queued jobs for the new container.
It backs up SQLite to `backups/before-<sha>.sqlite3`, starts the new image and waits for three
consecutive healthy checks. Readiness requires fresh polling/worker/reader status without API calls.
Failure restores the previous image and reports a failed workflow. Database backups are **not**
automatically restored: migrations must remain backward compatible to avoid losing new writes.

State is in `/home/deploy/.local/state/podcast-deploy/`. `current-sha` records the last successful
release. Only image-import output is saved in `load.log`; application secrets are never printed.
A killed deployment may leave `data/deploy-drain`: after confirming no deployment is active,
remove that marker to resume the queue. To retry a failed deployment, rerun its failed GitHub job;
stale runs will be skipped. Keep the previous image until a release is verified.

On first deployment from a legacy image without a health check, the helper stops the idle
bot while holding SQLite’s writer lock, preventing a new claim during bootstrap. If a job
has started, deployment fails safely and can be retried. Never use this key for an interactive
shell or unrelated server administration.
