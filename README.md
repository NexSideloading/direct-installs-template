# Direct Installs Template

This repository downloads IPA files, fetches signing certificates, signs each IPA, and publishes the signed IPAs and installation manifests. GitHub Actions can run the workflow on a schedule or on demand.

## Important: repository visibility and signing assets

The generated `signed_apps/` files are served from this repository using public raw GitHub URLs by default. Anyone who can access those files can download the signed apps. The workflow does **not** commit downloaded certificate bundles or source IPAs.

Some apps need signing assets embedded in their signed IPA. The default list is in `apps_requiring_signing_asset_injection` in `config.json`; those output IPAs contain certificate files and the certificate password. Do not publish or distribute those builds unless you understand and accept that exposure. Remove apps from that list if they do not need injected assets.

For public over-the-air installation, the generated IPA and icon URLs must be reachable by the device without GitHub authentication. A private repository's raw URLs are not publicly accessible. Never add personal credentials or private signing keys to `config.json`, source code, or a public repository.

## 1. Create your repository

Use this repository as a GitHub template, or fork/copy it into a public repository you control. Keep the repository name and default branch handy; you will enter them in `config.json`.

## 2. Configure `config.json`

Edit `config.json` before running the workflow:

| Setting | Purpose |
| --- | --- |
| `github_repo` | Repository that will host the generated signed apps and icons, in `owner/repository` form. |
| `github_ref` | Branch used in raw file URLs, usually `main`. |
| `ipa_sources` | Map of app names to IPA download URLs. Each app name becomes the output filename, such as `ipas/Ksign.ipa`. |
| `certificate_api_url` | Base URL for the certificate API. The scripts append the required list and download routes. |
| `zsign_download_url` | URL for the Linux x86-64 zsign release archive used by GitHub Actions. |
| `apps_requiring_signing_asset_injection` | Apps that need the signing assets copied into their signed IPA. |
| `cert_filters.include_revoked` | Set to `false` to skip revoked certificates. `true` includes them. Expired certificates are always skipped. |
| `manifest_url_template` | URL pattern for signed IPA files. Supports `{github_repo}`, `{github_ref}`, `{app_name}`, and `{cert_name}`. |
| `icon_url_template` | URL pattern for icons. Supports `{github_repo}`, `{github_ref}`, and `{icon_filename}`. |
| `fallback_icon_url` | Icon URL to use when no extracted or custom icon is available. |

Example custom IPA source:

```json
"ipa_sources": {
  "MyApp": "https://example.com/downloads/my-app.ipa"
}
```

Use simple app names without path separators. The source must be a direct URL to a valid IPA archive containing a `Payload/` directory.

## 3. Add app icons (optional)

App icons are **automatically** extracted from the IPA file, but you can put custom PNG icons in the repository's top-level `icons/` folder. Name each file after its app source name, including capitalization:

```text
icons/
  Ksign.png
  MyApp.png
```

For the app named `MyApp` in `ipa_sources`, `icons/MyApp.png` is used instead of the icon extracted from the IPA. PNG files should be square and accessible from the configured raw GitHub URL. If there is no custom icon, the workflow extracts one when available and publishes it as `icons/MyApp_icon.png`; otherwise it uses `fallback_icon_url`. No manual icon is required.

## 4. Enable GitHub Actions

1. Open the repository's **Settings → Actions → General** page.
2. Under **Workflow permissions**, allow read and write access to repository contents. The workflow needs this permission to publish signed apps and updated state.
3. Confirm Actions are enabled for the repository.
4. Open the **Actions** tab and run **Sign Apps with Certificates** using **Run workflow**.

The workflow is also scheduled to run twice daily at 00:00 and 12:00 UTC. Scheduled workflows run from the repository's default branch and can be delayed by GitHub.

The workflow commits state files, signed apps, and icons. Downloaded IPAs and certificate bundles are temporary and are not committed.

## Installing a signed app

The generated manifest URL is:

```text
https://raw.githubusercontent.com/<owner>/<repository>/<branch>/signed_apps/<app-name>/<certificate-name>/manifest.plist
```

An iOS installation link can use the manifest URL in this form:

```text
itms-services://?action=download-manifest&url=<URL-encoded-manifest-URL>
```

Use the exact app and certificate folder names from the generated `signed_apps/` tree. Both the manifest and its referenced IPA/icon must be reachable from the device.

## Repository contents

- `config.json` — project-specific URLs and settings.
- `scripts/` — download, certificate, signing, and orchestration scripts.
- `.github/workflows/sign-apps.yml` — scheduled and manual GitHub Actions workflow.
- `icons/` — optional custom PNG icons and generated app icons.
- `signed_apps/` — signed IPAs and manifests published by the workflow.
- `*_state.json` — download, certificate, and signing state used to avoid unnecessary work.
