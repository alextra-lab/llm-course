# Security Policy

This repository is an educational course. The most important security concern
for users is **handling your API credentials safely** — see below.

## Supported versions

This is a learning resource, not a released product. Only the latest `main` is
maintained; fixes are applied there.

## Reporting a vulnerability

Please report security issues **privately**, not in a public issue:

- Use GitHub's **"Report a vulnerability"** button under the repository's
  **Security → Advisories** tab to open a private advisory.

Include what you found and how to reproduce it. We aim to acknowledge reports
within a few days. Thanks for helping keep learners safe.

## Handling credentials (read this before you run anything)

The examples talk to a hosted endpoint using a token in `OPENAI_API_KEY`.

- Keep your real token in a local **`.env`** file. It is **git-ignored** — never
  commit it, paste it into a lesson file, or share it in an issue/PR.
- If a token is ever exposed, **revoke and rotate it immediately** at your
  provider.
- `.env.example` ships with placeholders only (`sk-replace-me`); copy it to
  `.env` and fill in your own values.

## A note on TLS verification

If TLS verification fails on your machine, prefer pointing `SSL_CERT_FILE` (or
`OPENAI_CA_BUNDLE`) at the correct CA bundle. The `OPENAI_INSECURE=1` opt-out
disables certificate verification entirely and removes protection against
man-in-the-middle attacks — use it only on a network you trust, never in
production. See the README's "Troubleshooting: SSL / certificates" section.
