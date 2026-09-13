from pathlib import Path
import unittest


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


class MediaPreflightWorkflowTests(unittest.TestCase):
    def test_provider_state_uses_step_context_and_run_attempt_isolation(self):
        workflow = (WORKFLOWS / "episode-media-preflight.yml").read_text(encoding="utf-8")
        job_header = workflow.split("    steps:", 1)[0]
        self.assertNotIn("runner.", job_header)
        batch = workflow.split("- name: Episode media preflight", 1)[1].split("        run:", 1)[0]
        self.assertIn("AUDIO_PROVIDER_CIRCUIT_STATE:", batch)
        self.assertIn("${{ runner.temp }}", batch)
        self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}", batch)
        self.assertIn("max_attempts=6", workflow)

    def test_auth_probe_only_does_not_resolve_or_handoff_another_episode(self):
        workflow = (WORKFLOWS / "test-youtube-auth.yml").read_text(encoding="utf-8")
        for name in ("Resolve Luisa visual candidates without publishing",
                     "Verify resolver output", "Stage visual handoff",
                     "Verify staged handoff locally", "Upload visual handoff smoke artifact"):
            step = workflow.split("- name: " + name, 1)[1].split("      - name:", 1)[0]
            self.assertIn("if: ${{ !inputs.probe_only }}", step)
        self.assertIn("handoff-smoke-test:\n    if: ${{ !inputs.probe_only }}", workflow)

    def test_render_can_use_existing_auth_fallback_without_visual_handoff(self):
        workflow = (WORKFLOWS / "publish-episode.yml").read_text(encoding="utf-8")
        publish = workflow.split("\n  publish:\n", 1)[1]
        self.assertIn("Set up Node.js for render-time yt-dlp EJS", publish)
        render = publish.split("- name: Render episode", 1)[1].split("      - name:", 1)[0]
        self.assertIn("YOUTUBE_COOKIES: ${{ secrets.YOUTUBE_COOKIES }}", render)
        self.assertIn('run: python generate.py "$EPISODE"', render)


if __name__ == "__main__":
    unittest.main()
