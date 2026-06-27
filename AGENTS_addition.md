## Naima's Instructions

At the start of every session, read `naima.md` from the project root.

Check the version field against the last version you worked with, stored in TrueMem as `naima_md_version`.

- If the version matches what's already in TrueMem: skip re-reading the body, proceed normally, no announcement needed.
- If it's newer (or `naima_md_version` isn't set yet): read the full file, then say one line — "naima.md v{version} loaded" — and write `naima_md_version = {version}` to TrueMem ("remember this: naima_md_version = {version}").

`naima.md` carries Naima's architectural decisions and standing instructions. It overrides your own judgment on design questions. AGENTS.md still governs session command mechanics and behavioral rules — if the two ever conflict on a mechanical point, AGENTS.md wins; for design/architecture, `naima.md` wins.
