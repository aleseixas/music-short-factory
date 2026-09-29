from pathlib import Path
import json
import unittest

from engine.pipeline_runtime import trigger_plan, workflow_events

ROOT = Path(__file__).resolve().parents[1]


class PipelineContractDriftTests(unittest.TestCase):
    def test_all_workflow_events_are_in_the_audited_inventory(self):
        contract = json.loads((ROOT / "config/pipeline-contract.json").read_text())
        actual = {path.name: sorted(workflow_events(ROOT, path.name)[0]) for path in (ROOT / ".github/workflows").glob("*.yml")}
        self.assertEqual(actual, contract["workflow_inventory"])

    def test_duplicate_safety_guard_has_explicit_auth_and_http_dependency(self):
        for path in (ROOT / ".github/workflows").glob("duplicate-preflight*.yml"):
            source = path.read_text(encoding="utf-8")
            self.assertIn("GH_TOKEN: ${{ github.token }}", source)
            self.assertIn("python -m pip install requests", source)

    def test_every_contract_trigger_matches_real_workflow(self):
        contract = json.loads((ROOT / "config/pipeline-contract.json").read_text())
        for name, rule in contract["workflows"].items():
            with self.subTest(workflow=name):
                plan = trigger_plan(ROOT, name)
                self.assertIn(rule["trigger"], plan["events"])
                self.assertEqual(rule["dispatch_supported"], "workflow_dispatch" in plan["events"])

    def test_operational_templates_reference_contract(self):
        for filename in ("docs/pipeline-contract.md", "templates/publishing-completion-rule.md", "templates/short-form-style-rule.md"):
            self.assertIn("pipeline-contract", (ROOT / filename).read_text(encoding="utf-8"), filename)

    def test_publish_paths_share_concurrency_and_final_outcome(self):
        for filename in ("publish-episode.yml", "publish-existing-artifact.yml"):
            source = (ROOT / ".github/workflows" / filename).read_text(encoding="utf-8")
            self.assertIn("group: publish-episode-", source)
            self.assertIn("pipeline_workflow.py publisher-result", source)
            self.assertNotIn("publish_ready_bundle.py restore", source)
            self.assertIn("python publish.py", source)
            self.assertIn("contents: write", source)

    def test_media_request_identity_and_queue_are_persisted(self):
        source = (ROOT / ".github/workflows/episode-media-preflight.yml").read_text(encoding="utf-8")
        self.assertIn('pipeline_workflow.py media-pass', source)
        self.assertIn('pipeline_workflow.py queue', source)
        self.assertNotIn('git push', source)
        adapter = (ROOT / 'scripts/pipeline_workflow.py').read_text(encoding='utf-8')
        self.assertIn('coordinator.set_phase(token, "QUEUED", files=', adapter)
        self.assertIn('PIPELINE_REQUEST_ID:', source)
        self.assertIn('scripts/dispatch_publication.py', source)
        self.assertIn('pipeline_workflow.py media-failed', source)
        publisher = (ROOT / '.github/workflows/publish-episode.yml').read_text(encoding='utf-8')
        self.assertIn("format('publish/{0}/{1}/{2}'", publisher)

    def test_failed_visual_resolution_is_a_hard_gate_before_handoff_and_render(self):
        source = (ROOT / '.github/workflows/episode-media-preflight.yml').read_text(encoding='utf-8')
        resolution = source.split('      - name: Score and resolve final visual candidates', 1)[1]
        resolver, handoff = resolution.split('      - name: Enforce final visual semantics and stage handoff', 1)
        handoff = handoff.split('      - name:', 1)[0]
        self.assertNotIn('continue-on-error:', resolver)
        self.assertNotIn('if: always()', handoff)
        render = source.split('  render-preflight:', 1)[1].split('    runs-on:', 1)[0]
        self.assertIn('- resolve-visuals', render)



if __name__ == "__main__":
    unittest.main()
