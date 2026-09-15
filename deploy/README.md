# Production deployment

The `CI` workflow deploys **pushes to main only**, after `Python quality and tests` succeeds.
PRs never receive the deployment secret. GitHub builds a SHA-tagged image, then streams it
through SSH to a forced command. No registry credentials or passwordless sudo are needed.

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
