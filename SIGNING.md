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
     <zip-file>
       <pe-file path="MusicDownloader.exe">
         <authenticode-sign />
       </pe-file>
       <pe-file path="MusicDownloaderGUI.exe">
         <authenticode-sign />
       </pe-file>
     </zip-file>
   </artifact-configuration>
   ```
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

Push a new tag (`git tag v1.0.1 && git push origin v1.0.1`). The workflow builds, sends the exes
to SignPath, waits for the signed files and publishes them in the release.
