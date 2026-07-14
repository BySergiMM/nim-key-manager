# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities.

Instead, use GitHub's private reporting: go to the repository's **Security** tab →
**Report a vulnerability** (GitHub Security Advisories). This keeps the report
private until a fix is available.

Include, if you can:

- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- affected version/commit and configuration.

You can expect an initial acknowledgement within a few days. Coordinated
disclosure is appreciated: please give maintainers reasonable time to release a
fix before any public disclosure.

## Scope

This project stores **your own** NVIDIA API keys encrypted at rest and is meant
to be **self-hosted**. When you deploy it you are the operator and are
responsible for the security of your instance. Especially relevant:

- Set strong, unique values for `JWT_SECRET`, `ENCRYPTION_MASTER_KEY` and the
  bootstrap admin credentials (on Render these secrets are generated for you).
- Keep `MCP_ALLOWED_IDENTITIES` tight and use a least-privilege OAuth App for the
  Claude connector.
- Enable 2FA on your GitHub and hosting accounts.

Reports that amount to "misconfiguring my own instance" are out of scope, but
reports about insecure defaults are very welcome.

## Supported versions

Fixes are provided for the latest release on the `main` branch.
