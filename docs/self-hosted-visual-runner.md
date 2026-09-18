# Self-hosted visual runner

Use this when YouTube blocks GitHub-hosted runner IPs during web visual resolution.

The publish workflow keeps rendering and publishing on `ubuntu-latest`. Only the `visuals` job moves to the self-hosted machine when the repository variable `USE_SELF_HOSTED_VISUALS` is set to `true`.

## One-time setup

1. In the repository, open **Settings → Actions → Runners → New self-hosted runner**.
2. Choose the operating system of the machine that will use the residential connection and run the commands GitHub shows. Do not share the temporary runner registration token.
3. Keep the runner online. On Windows, `run.cmd` starts it interactively; GitHub also documents how to install it as a service. On Linux/macOS, use the equivalent runner commands shown by GitHub.
4. Install FFmpeg on the self-hosted machine and make sure this succeeds from the same user/session that runs the GitHub runner:

   ```text
   ffmpeg -version
   ```

5. Docker is optional. If Docker is available, `resolve_visual_candidates_web.py` can also start the existing local bgutil PO Token provider. If Docker is not available, the resolver falls back to regular yt-dlp while still benefiting from the residential IP.
6. In **Settings → Secrets and variables → Actions → Variables**, create the repository variable:

   ```text
   USE_SELF_HOSTED_VISUALS=true
   ```

After that, future publish runs execute the visual resolution on the self-hosted runner, upload only the changed resolved visual files as an Actions artifact, and the normal GitHub-hosted publish job downloads that handoff before rendering.

## Disable / rollback

Set `USE_SELF_HOSTED_VISUALS=false` or delete the variable. The workflow immediately returns the visual job to `ubuntu-latest`.

## Important behavior

- If the self-hosted runner is offline while `USE_SELF_HOSTED_VISUALS=true`, the visual job waits for that runner and the publish job cannot continue until it becomes available.
- Publication credentials remain in the GitHub-hosted `publish` job; the self-hosted runner does not receive YouTube upload, Instagram, Facebook, TikTok, or Cloudinary secrets.
- The handoff contains the resolved `assets.json`, `timeline.json`, `visual_resolution_report.json`, and only changed/new media files inside the episode assets directory.
