# Security Policy

## Supported versions

Only the latest code on `main` receives security fixes.

## Reporting a vulnerability

Please do not open a public issue for security problems.

Report it privately through GitHub instead: open the repository's **Security** tab and choose
**Report a vulnerability** ([direct link](https://github.com/Spuddy10345/rmlabels/security/advisories/new)).

Please include:

- a description of the issue and its impact
- steps to reproduce it
- the affected commit, if known
- any suggested fix

I aim to acknowledge reports within a few days. Once an issue is confirmed, a fix is released
before any public disclosure.

## Scope

rmlabels is designed to run locally or on a private network (for example a tailnet). The web UI
has no authentication of its own, so exposing it to the public internet is outside its intended use.
