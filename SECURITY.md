# Security

Report a suspected vulnerability using this repository's GitHub security reporting feature, if enabled; otherwise open an issue without credentials, cookies, pairing codes or private media attached.

The app binds to loopback. Write operations require an origin check and a per-session token. The separately paired browser extension can access only queued exports, not arbitrary file paths or login cookies. Pairing authorizes publication: keep pairing codes private and generate a new code to revoke prior access.

Do not port-forward the service. Treat local source folders and file names as private. Release archives and issue reports must not include databases, exports, browser profiles, credential stores or logs containing local paths.
