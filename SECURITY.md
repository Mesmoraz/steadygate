# Security policy

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's private reporting instead: on the repository,
go to **Security → Report a vulnerability**. You'll get a reply within a week.

## Scope

- The CLI reads local files and writes a local SQLite file. It makes no network calls.
- The companion (`steadygate serve`) listens on `127.0.0.1` only and rejects requests from
  other web origins and other `Host` headers unless you allow them. Reports about bypassing
  either check are especially welcome.
- The GitHub Action runs the CLI and posts one PR comment with the workflow's token.

Only the latest release gets fixes.
