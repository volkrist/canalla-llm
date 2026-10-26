# Auto-update — contract, publishing and rollback

The desktop client asks one public endpoint:

```text
https://gateway.12testers.store/updates/latest?target={{target}}&arch={{arch}}&current_version={{current_version}}
```

Tauri v2 substitutes `windows`, `x86_64` and the running version. The Gateway joins `target-arch`
(`windows-x86_64`, `linux-x86_64`) and answers:

| Situation | Answer |
|---|---|
| no manifest published | `204 No Content` |
| manifest older than, or equal to, the client | `204 No Content` |
| unsupported platform | `204 No Content` |
| newer, bound, signed artifact | `200` with `version`, `notes`, `channel`, `pub_date`, `platforms` |
| manifest exists but is unsafe/invalid | `503` with a typed `updates_manifest_*` reason |

The body is the *static* Tauri shape (`platforms`), which the plugin reads by looking for
`windows-x86_64-nsis` and then `windows-x86_64`; NSIS downloads the installer `.exe` itself, not a
`.nsis.zip`.

## The artifact name is part of the signature

`tauri signer sign` writes the signed file name into the minisign trusted comment
(`file:Canalla LLM_1.2.4_x64-setup.exe`). Both the Gateway and the client refuse a manifest whose
`url` does not serve exactly that name, so:

* **serve the artifact from `/downloads/`** (`/srv/canalla-downloads/`), where the space survives
  as `%20`;
* **never point the manifest at the GitHub release asset**: GitHub rewrites the space to a dot
  (`Canalla.LLM_…`), which breaks the binding (`updates_manifest_entry_unbound` /
  `artifact_name_mismatch`). Uploading the same bytes through `gh release upload` is fine for a
  human download; it is not the updater URL.

`signature` is the base64 **content** of the `.sig` file. It is never a file name, a URL or a
SHA256.

## Publishing a release manifest

```bash
python scripts/updates-manifest.py build \
  --installer "apps/desktop/src-tauri/target/release/bundle/nsis/Canalla LLM_1.2.4_x64-setup.exe" \
  --signature "apps/desktop/src-tauri/target/release/bundle/nsis/Canalla LLM_1.2.4_x64-setup.exe.sig" \
  --version 1.2.4 --previous 1.2.3 \
  --base-url "https://gateway.12testers.store/downloads/" \
  --pub-date "2026-10-26T12:06:27Z" \
  --notes-file artifacts/updater/notes-1.2.4.txt \
  --expect-sha256 <installer sha256> \
  --out artifacts/updater/updates-manifest-1.2.4.json

python scripts/updates-manifest.py check --manifest artifacts/updater/updates-manifest-1.2.4.json
```

`build` refuses an installer whose signed name differs from its file name, and it runs the
Gateway's own loader before writing the file.

Deploy (the service reads the file on every request, so no restart is needed):

```bash
scp artifacts/updater/updates-manifest-1.2.4.json distance:/tmp/
ssh distance "sudo -n bash -c '
  install -o alex-gateway -g alex-gateway -m 640 /tmp/updates-manifest-1.2.4.json \
    /var/lib/alex-gateway/updates/updates-manifest.json.new
  mv -f /var/lib/alex-gateway/updates/updates-manifest.json.new \
    /var/lib/alex-gateway/updates/updates-manifest.json'"
```

The installer is uploaded to `/srv/canalla-downloads/` with its real name
(`Canalla LLM_1.2.4_x64-setup.exe`), not to the GitHub release asset path.

Verify:

```bash
python scripts/updates-manifest.py live --version 1.2.4 \
  --signature "apps/desktop/src-tauri/target/release/bundle/nsis/Canalla LLM_1.2.4_x64-setup.exe.sig" \
  --installer-sha256 <installer sha256> \
  --installer-name "Canalla LLM_1.2.4_x64-setup.exe"
```

The live matrix proves: `1.2.3 → 200`, `1.2.4 → 204`, `1.2.5 → 204`, Linux → `204`, a repeat is
identical, the served signature is the `.sig` content, and the downloaded bytes hash to the
published SHA256.

## Rollback

The previous manifest is kept next to the live one as
`updates-manifest.json.bak-<timestamp>`:

```bash
ssh distance "sudo -n bash -c '
  cp -a /var/lib/alex-gateway/updates/updates-manifest.json.bak-<timestamp> \
    /var/lib/alex-gateway/updates/updates-manifest.json'"
```

Nothing else needs a restart, and no client session is interrupted. Removing the manifest entirely
also rolls back to "no update" (204).

## Cloudflare and the user agent

The public endpoint is behind Cloudflare. Its managed rules answer the default `Python-urllib`
agent with `403` and `error code: 1010`, while the updater's own agent
(`tauri-plugin-updater/2.x`) and curl are allowed. Acceptance tooling must send a normal
user agent; `scripts/updates-manifest.py` does.

## Real installed E2E

`apps/desktop/e2e/updater-live.mjs` drives an installed client over the WebView2 debug port:
it discovers the release, downloads and verifies the package, installs it, relaunches, and checks
the version and the isolated data root. It installs into `%LOCALAPPDATA%\Programs\Canalla LLM`, so
it must only run on a throwaway machine — the `updater-e2e` GitHub workflow installs the published
1.2.3 on a fresh `windows-latest` runner and runs it there, with an isolated data root and device
credentials.
