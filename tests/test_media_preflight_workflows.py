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
        self.assertIn("max_attempts=10", workflow)

    def test_auth_probe_only_does_not_resolve_or_handoff_another_episode(self):
        workflow = (WORKFLOWS / "test-youtube-auth.yml").read_text(encoding="utf-8")
        for name in ("Resolve Luisa visual candidates without publishing",
                     "Verify resolver output", "Stage visual handoff",
                     "Verify staged handoff locally", "Upload visual handoff smoke artifact"):
            step = workflow.split("- name: " + name, 1)[1].split("      - name:", 1)[0]
            self.assertIn("if: ${{ !inputs.probe_only }}", step)
        self.assertIn("handoff-smoke-test:\n    if: ${{ !inputs.probe_only }}", workflow)

    def test_complete_video_gate_runs_before_publish_queue(self):
        workflow = (WORKFLOWS / "episode-media-preflight.yml").read_text(
            encoding="utf-8"
        )
        ordered_markers = (
            "Episode media preflight — batch diagnostics and auto-repair",
            "Score and resolve final visual candidates",
            "Revalidate final media after visual resolution",
            "Render complete episode",
            "Prepare publishing metadata and cover",
            "Validate every platform locally without publishing",
            "Validate final media and create publish-ready bundle",
            "Upload mandatory publish-ready artifact",
            "Create publish queue only after render preflight passes",
            "Dispatch publication of validated artifact",
        )
        offsets = [workflow.index(marker) for marker in ordered_markers]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn('run: python generate.py "$EPISODE"', workflow)
        self.assertIn(
            'run: python publish.py "$EPISODE" --platform all --dry-run', workflow
        )
        self.assertNotIn("--live", workflow)
        for secret in (
            "YOUTUBE_CLIENT_SECRET",
            "YOUTUBE_REFRESH_TOKEN",
            "INSTAGRAM_ACCESS_TOKEN",
            "CLOUDINARY_API_SECRET",
            "TIKTOK_CLIENT_SECRET",
            "TIKTOK_REFRESH_TOKEN",
        ):
            self.assertNotIn(secret, workflow)
        self.assertIn("source_sha: ${{ steps.source.outputs.source_sha }}", workflow)
        self.assertEqual(
            workflow.count("ref: ${{ needs.media-preflight.outputs.source_sha }}"),
            2,
        )
        self.assertIn(
            "PREFLIGHT_SOURCE_SHA: ${{ needs.media-preflight.outputs.source_sha }}",
            workflow,
        )

    def test_publish_consumes_exact_preflight_bundle_without_rendering(self):
        workflow = (WORKFLOWS / "publish-episode.yml").read_text(encoding="utf-8")
        self.assertIn("source_run_id:", workflow)
        provenance = workflow.split(
            "- name: Verify media preflight artifact provenance", 1
        )[1].split("      - name:", 1)[0]
        self.assertIn('source_name" != "Episode media preflight', provenance)
        self.assertIn('source_event" != "push', provenance)
        self.assertIn('source_branch" != "main', provenance)
        self.assertIn("run-id: ${{ needs.prepare.outputs.source_run_id }}", workflow)
        self.assertIn("name: publish-ready-${{ needs.prepare.outputs.episode }}", workflow)
        self.assertIn("publish_ready_bundle.py restore", workflow)
        self.assertNotIn("generate.py", workflow)
        self.assertNotIn("prepare_post.py", workflow)
        self.assertNotIn("resolve_visual_candidates", workflow)
        self.assertNotIn("GEMINI_API_KEY", workflow)
        self.assertIn('--platform youtube --live', workflow)
        self.assertIn('--platform instagram --live', workflow)
        self.assertIn('--platform tiktok --live', workflow)

    def test_publish_ready_bundle_survives_cleanup_long_enough_for_retries(self):
        workflow = (WORKFLOWS / "cleanup-actions-storage.yml").read_text(
            encoding="utf-8"
        )
        case = workflow.split("publish-ready-*)", 1)[1].split(";;", 1)[0]
        self.assertIn("age >= 259200", case)
        self.assertNotIn('run_conclusion == "success"', case)


if __name__ == "__main__":
    unittest.main()
