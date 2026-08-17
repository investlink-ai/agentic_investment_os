# GitHub automation

- Keep workflows thin: install the locked environment and call the owning `Makefile` target instead
  of reproducing gates in YAML.
- Default `GITHUB_TOKEN` permissions to read-only, disable persisted checkout credentials, and grant a
  narrower write permission only in the job that needs it.
- Keep pull-request jobs keyless: no broker credentials, model credentials, live services, or
  untrusted code on a privileged or self-hosted runner.
- Pin third-party actions to a full commit SHA with the release tag in a comment. Let Dependabot
  propose updates.
- Keep required check names stable once branch protection depends on them.
