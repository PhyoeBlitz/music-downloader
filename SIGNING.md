# Code signing

Release builds are made by GitHub Actions (`.github/workflows/release.yml`).
The workflow is ready to sign the exes with [SignPath](https://signpath.io), which offers free
code signing for open-source projects through the [SignPath Foundation](https://signpath.org).
Signing switches on automatically once the secret below exists; until then releases are unsigned.

## 1. Apply

Apply at <https://signpath.org/apply> with this repository. SignPath reviews the project
(open-source license, public source, builds from CI) and sets up an organization and project for you.

## 2. Configure SignPath

In the SignPath web app:

1. Connect the GitHub repository as a trusted build system (GitHub.com).
2. Create an artifact configuration for the uploaded GitHub artifact (a zip with both exes):

   ```xml
   <?xml version="1.0" encoding="utf-8"?>
   <artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
     <parameters>
       <parameter name="version" required="true" />
     </parameters>
     <zip-file>
       <pe-file path="MusicDownloader.exe" product-name="Music Downloader" product-version="${version}">
         <authenticode-sign />
       </pe-file>
       <pe-file path="MusicDownloaderGUI.exe" product-name="Music Downloader" product-version="${version}">
         <authenticode-sign />
       </pe-file>
     </zip-file>
   </artifact-configuration>
   ```

   The workflow passes `version` (from the tag, e.g. `1.0.2`) and every exe carries matching
   Windows version metadata (`packaging/make_version_info.py`), so SignPath only signs files
   that really are this product at this version.
3. Create an API token for a CI user that can submit signing requests.

## 3. Add the values to GitHub

Repository **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
|---|---|---|
| Secret | `SIGNPATH_API_TOKEN` | the API token |
| Variable | `SIGNPATH_ORGANIZATION_ID` | your organization ID |
| Variable | `SIGNPATH_PROJECT_SLUG` | project slug |
| Variable | `SIGNPATH_SIGNING_POLICY_SLUG` | e.g. `release-signing` |
| Variable | `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | the artifact configuration slug |

## 4. Release

Push a new tag:

```powershell
git tag -a v1.0.2 -m "v1.0.2"
git push origin v1.0.2
```

The workflow builds, sends the exes to SignPath and waits up to **1 hour** for the signing
request to be approved in SignPath (the Foundation requires a manual approval for every request),
then publishes the signed files in the release.

Check: right-click an exe from the release → **Properties → Digital Signatures** should show
SignPath Foundation, and the release notes say "Signed via SignPath."
