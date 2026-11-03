# Security Policy

## Supported Versions

Security fixes are released for the latest HomeCave release only.

| Version | Supported |
| :--- | :---: |
| `0.9.x` (latest) | ✅ |
| `< 0.9.3` | ❌ — please upgrade |
| Legacy pre-HomeCave repository | ❌ — archived, moved to HomeCave |

## Reporting a Vulnerability

Please **do not open a public GitHub issue** for security problems.

Report vulnerabilities privately through GitHub's
[**Report a vulnerability**](https://github.com/Maartenwiese/HomeCave/security/advisories/new)
form (Security → Advisories). Include:

- the affected HomeCave version and Home Assistant version,
- steps to reproduce or a proof of concept,
- the impact you expect (for example: which Home Assistant user role is required).

You can expect an initial response within 7 days. Confirmed issues are fixed in a new
release and credited in the release notes unless you prefer to stay anonymous.

## Security Model (summary)

- HomeCave runs inside Home Assistant and relies on Home Assistant authentication.
- **Admin-only:** backup create/list/restore, import (CSV / ZIP / JSON), export, wine deletion,
  and camera-entity snapshots for users without entity read permission.
- **All logged-in users:** viewing the cellar, scanning bottle photos, adding / adjusting /
  updating wines, AI research, and SommAI pairing.
- Backups are stored privately in `<config>/homecave/backups/` and downloaded only through the
  authenticated, admin-only `/api/homecave/download/backup/` view (short-lived signed links).
- Cover images are only downloaded from `https://images.vivino.com` and only written to disk after
  image signature validation.
- All bottle photos (label scans, uploads, Vivino covers, restored covers) are stored privately in
  `<config>/homecave/covers/` and served only by the authenticated `/api/homecave/cover/` view,
  using short-lived signed URLs (24 h, reused for 12 h). Nothing is published under `/local/`.
- AI provider API keys are stored in the Home Assistant config entry and sent only in request
  headers to the configured provider.
