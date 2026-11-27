# winget

`manifests/` holds the manifest for the first winget submission (`PhyoeBlitz.MusicDownloader` 1.0.1).
After that, the release workflow submits each new version automatically.

## First submission (once)

```powershell
winget install Microsoft.WingetCreate

# optional local test (on a PC that may run unsigned exes; needs an admin terminal once)
winget settings --enable LocalManifestFiles
winget validate --manifest packaging\winget\manifests\p\PhyoeBlitz\MusicDownloader\1.0.1
winget install  --manifest packaging\winget\manifests\p\PhyoeBlitz\MusicDownloader\1.0.1

# open the pull request at microsoft/winget-pkgs (forks it to your account)
wingetcreate submit --token <TOKEN> packaging\winget\manifests\p\PhyoeBlitz\MusicDownloader\1.0.1
```

`<TOKEN>`: GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic),
scope `public_repo`. Watch the pull request and answer the bot or reviewers if they ask something.

## Automatic updates

After the first pull request is **merged**, add the same token as a repository secret named
`WINGET_TOKEN` (Settings → Secrets and variables → Actions). From then on, every pushed `v*` tag
builds the release and opens the winget update pull request (job `winget` in
`.github/workflows/release.yml`). Without the secret that job is skipped.

Never replace the zip of a published release: winget stores its SHA-256, so a changed file breaks
installs. Publish a new version instead.
