# Security Policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Contact the
maintainer privately through the security contact configured on the GitHub
repository and include reproduction steps, affected versions, and impact.

Please do not include API keys, credentials, private source code, or personal
data in a report.

## Scope notes

AgenVantage reads local repositories and can launch configured external agent
commands. Review generated handoffs and command arguments before enabling
execution on untrusted repositories. Provider validation can send selected
content to an external provider only when explicitly configured by the user.
