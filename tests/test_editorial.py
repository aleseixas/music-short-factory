from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from engine.editorial import (
    EditorialCatalogs,
    load_editorial_catalogs,
    validate_editorial_direction,
)
from engine.episode import load_episode
from engine.models import (
    AssetSpec,
    BackgroundMusicSpec,
    Episode,
    HighlightSpec,
    OverlayCue,
    RelativeTextFxCue,
    ResolvedVisualFxCue,
    ScriptSegment,
    SfxCue,
    ShotSpec,
    Story,
    TextFxCue,
    TimelinePlan,
    TimelineScene,
    VisualFxCue,
    WordTiming,
)
from engine.timeline import build_timeline


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOGS = EditorialCatalogs(
    music_profiles=frozenset({"latin_pop_uplifting"}),
    sfx_types=frozenset({"impact", "pop", "riser", "whoosh"}),
)


def _episode(shot_count: int = 1, **changes: object) -> Episode:
    segments = tuple(
        ScriptSegment(f"segment_{index}", f"word{index}")
        for index in range(shot_count)
    )
    story = Story("Demo", "demo", segments, target_duration_seconds=75)
    photo = AssetSpec("photo", "photo.jpg", None, "", "", 0.5, 0.5)
    assets = {photo.id: photo}
    for index in range(1, 12):
        asset = AssetSpec(
            f"overlay_{index}",
            f"overlay_{index}.png",
            None,
            "",
            "",
            0.5,
            0.5,
        )
        assets[asset.id] = asset
    shots = tuple(
        ShotSpec(
            f"shot_{index}",
            segment.id,
            "photo",
            "hold",
            "cut",
        )
        for index, segment in enumerate(segments)
    )
    values = {
        "name": "demo",
        "directory": Path("episodes/demo"),
        "story": story,
        "assets": assets,
        "shots": shots,
    }
    values.update(changes)
    return Episode(**values)


def _plan(episode: Episode, duration: float = 75.0) -> TimelinePlan:
    count = len(episode.story.segments)
    step = duration / count
    words = tuple(
        WordTiming(segment.text, index * step, (index + 1) * step - 0.01)
        for index, segment in enumerate(episode.story.segments)
    )
    return build_timeline(
        story=episode.story,
        shots=episode.shots,
        assets=episode.assets,
        words=words,
        audio_duration=duration,
        fps=10,
        crossfade_seconds=0,
        visual_fx_cues=episode.visual_fx_cues,
    )


def _codes(episode: Episode, plan: TimelinePlan | None = None) -> frozenset[str]:
    resolved_plan = plan or _plan(episode)
    return validate_editorial_direction(
        episode,
        resolved_plan,
        CATALOGS,
        highlight_default_duration=1.65,
    ).warning_codes


class EditorialDirectionTests(unittest.TestCase):
    def test_catalog_discovery_reads_only_real_music_and_sfx_names(self):
        catalogs = load_editorial_catalogs(PROJECT_ROOT)
        music_catalog = json.loads(
            (PROJECT_ROOT / "assets" / "audio" / "music" / "catalog.json").read_text(
                encoding="utf-8"
            )
        )
        sfx_catalog = json.loads(
            (PROJECT_ROOT / "assets" / "audio" / "sfx" / "catalog.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(catalogs.music_profiles, set(music_catalog["profiles"]))
        self.assertEqual(catalogs.sfx_types, set(sfx_catalog["types"]))
        self.assertNotIn("schema_version", catalogs.music_profiles)
        self.assertNotIn("schema_version", catalogs.sfx_types)

    def test_balanced_episode_has_no_editorial_warnings(self):
        visual_types = (
            "slow_zoom_in",
            "pan_right",
            "slow_zoom_out",
            "pan_left",
            "pan_up",
            "pan_down",
            "punch_zoom",
        )
        episode = _episode(
            shot_count=10,
            background_music=BackgroundMusicSpec("latin_pop_uplifting", 0.12),
            sfx_cues=tuple(
                SfxCue(time, effect, 0.25)
                for time, effect in zip(
                    (0.3, 9, 18, 27, 36, 45, 54, 66),
                    ("impact", "whoosh", "pop", "riser") * 2,
                )
            ),
            visual_fx_cues=tuple(
                VisualFxCue(index * 7.5 + 0.4, index * 7.5 + 1.4, effect, 0.45)
                for index, effect in enumerate(visual_types)
            ),
            text_fx_cues=tuple(
                TextFxCue(
                    index * 10 + 2,
                    index * 10 + 3.2,
                    f"IDEIA {index + 1}",
                    ("pop_in", "slide_up", "fade_pop")[index % 3],
                    accent_text=str(index + 1),
                )
                for index in range(6)
            ),
            overlay_cues=tuple(
                OverlayCue(index * 20 + 4, index * 20 + 5.5, f"overlay_{index + 1}", "fade_in", "center")
                for index in range(3)
            ),
        )

        self.assertEqual(_codes(episode), frozenset())

    def test_synchronized_editorial_beat_is_not_treated_as_excess(self):
        episode = _episode(
            background_music=BackgroundMusicSpec("latin_pop_uplifting", 0.12),
            sfx_cues=(SfxCue(10.0, "impact", 0.4),),
            visual_fx_cues=(VisualFxCue(9.8, 10.3, "punch_zoom", 0.6),),
            text_fx_cues=(
                TextFxCue(9.8, 11.2, "41 SEMANAS\nNO TOPO", "scale_bounce", accent_text="41"),
            ),
            overlay_cues=(OverlayCue(9.9, 11.1, "overlay_1", "pop_in", "center"),),
        )

        self.assertEqual(_codes(episode), frozenset())

    def test_budget_warns_about_all_count_excesses(self):
        cases = {
            "sfx_count_high": _episode(
                sfx_cues=tuple(
                    SfxCue(index * 5, ("impact", "whoosh", "pop", "riser")[index % 4], 0.2)
                    for index in range(13)
                ),
            ),
            "visual_fx_count_high": _episode(
                shot_count=11,
                visual_fx_cues=tuple(
                    VisualFxCue(
                        index * (75 / 11) + 0.2,
                        index * (75 / 11) + 1.0,
                        (
                            "slow_zoom_in",
                            "slow_zoom_out",
                            "pan_left",
                            "pan_right",
                            "pan_up",
                            "pan_down",
                            "punch_zoom",
                        )[index % 7],
                    )
                    for index in range(11)
                ),
            ),
            "punch_zoom_count_high": _episode(
                shot_count=5,
                visual_fx_cues=tuple(
                    VisualFxCue(index * 15 + 0.3, index * 15 + 0.8, "punch_zoom")
                    for index in range(5)
                ),
            ),
            "text_fx_count_high": _episode(
                text_fx_cues=tuple(
                    TextFxCue(index * 2, index * 2 + 1, f"FATO {index}", "pop_in")
                    for index in range(11)
                ),
            ),
            "overlay_count_high": _episode(
                overlay_cues=tuple(
                    OverlayCue(index * 2, index * 2 + 1, f"overlay_{index + 1}", "fade_in", "center")
                    for index in range(7)
                ),
            ),
        }

        for expected, episode in cases.items():
            with self.subTest(expected=expected):
                self.assertIn(expected, _codes(episode))

    def test_sfx_cluster_and_repetition_have_separate_warnings(self):
        clustered = _episode(
            sfx_cues=(
                SfxCue(1.0, "impact", 0.2),
                SfxCue(1.4, "whoosh", 0.2),
                SfxCue(1.9, "pop", 0.2),
            )
        )
        repeated = _episode(
            sfx_cues=tuple(SfxCue(time, "impact", 0.2) for time in (5, 15, 25, 35))
        )

        self.assertIn("sfx_clustered", _codes(clustered))
        self.assertNotIn("sfx_repetition", _codes(clustered))
        self.assertIn("sfx_repetition", _codes(repeated))
        self.assertNotIn("sfx_clustered", _codes(repeated))

    def test_long_kinetic_text_and_scale_bounce_repetition_warn(self):
        episode = _episode(
            text_fx_cues=tuple(
                TextFxCue(
                    index * 2,
                    index * 2 + 1,
                    "ESTA FRASE TEM PALAVRAS DEMAIS PARA TEXTO CINETICO" if index == 0 else f"FATO {index}",
                    "scale_bounce",
                )
                for index in range(4)
            )
        )

        codes = _codes(episode)
        self.assertIn("kinetic_text_long", codes)
        self.assertIn("scale_bounce_repetition", codes)

    def test_unknown_music_profile_and_sfx_type_are_errors(self):
        cases = (
            (
                _episode(background_music=BackgroundMusicSpec("invented_music", 0.2)),
                "profile de background music inexistente",
            ),
            (
                _episode(sfx_cues=(SfxCue(1, "cinematic_super_boom", 0.2),)),
                "type inexistente no catalogo",
            ),
        )

        for episode, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(RuntimeError, message):
                    _codes(episode)

    def test_invalid_sfx_source_trim_is_an_editorial_error(self):
        cases = (
            (
                _episode(sfx_cues=(SfxCue(1, "impact", 0.2, -0.1, None),)),
                "source_start_seconds",
            ),
            (
                _episode(sfx_cues=(SfxCue(1, "impact", 0.2, 0, 0),)),
                "duration_seconds",
            ),
        )
        for episode, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(RuntimeError, message):
                    _codes(episode)

    def test_missing_or_incompatible_overlay_asset_is_an_error(self):
        cases = (
            (
                _episode(overlay_cues=(OverlayCue(1, 2, "missing", "fade_in", "center"),)),
                "asset inexistente",
            ),
            (
                _episode(
                    overlay_cues=(OverlayCue(1, 2, "photo", "fade_in", "center"),),
                    assets={
                        "photo": AssetSpec("photo", "photo.mp4", None, "", "", 0.5, 0.5)
                    },
                ),
                "formato de overlay suportado",
            ),
        )

        for episode, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(RuntimeError, message):
                    _codes(episode)

    def test_invalid_overlaps_and_out_of_duration_cues_are_errors(self):
        overlapping = (
            _episode(
                visual_fx_cues=(
                    VisualFxCue(1, 3, "pan_left"),
                    VisualFxCue(2, 4, "pan_right"),
                )
            ),
            _episode(
                text_fx_cues=(
                    TextFxCue(1, 3, "UM", "pop_in"),
                    TextFxCue(2, 4, "DOIS", "fade_pop"),
                )
            ),
            _episode(
                overlay_cues=(
                    OverlayCue(1, 3, "overlay_1", "fade_in", "center"),
                    OverlayCue(2, 4, "overlay_2", "fade_in", "center"),
                )
            ),
        )
        plain_plan = _plan(_episode())
        for episode in overlapping:
            with self.subTest(kind=type(next(iter(episode.visual_fx_cues or episode.text_fx_cues or episode.overlay_cues))).__name__):
                with self.assertRaisesRegex(RuntimeError, "sobrepostas"):
                    validate_editorial_direction(episode, plain_plan, CATALOGS)

        outside = (
            _episode(sfx_cues=(SfxCue(75, "impact", 0.2),)),
            _episode(visual_fx_cues=(VisualFxCue(74.5, 75.5, "pan_left"),)),
            _episode(text_fx_cues=(TextFxCue(74.5, 75.5, "FIM", "fade_pop"),)),
            _episode(overlay_cues=(OverlayCue(74.5, 75.5, "overlay_1", "fade_in", "center"),)),
        )
        for episode in outside:
            with self.subTest(cues=episode):
                with self.assertRaisesRegex(RuntimeError, "dentro do video"):
                    validate_editorial_direction(episode, plain_plan, CATALOGS)

    def test_invalid_accent_and_multiple_visual_fx_in_one_shot_are_errors(self):
        invalid_accent = _episode(
            text_fx_cues=(
                TextFxCue(1, 2, "FATO IMPORTANTE", "pop_in", accent_text="AUSENTE"),
            )
        )
        with self.assertRaisesRegex(RuntimeError, "accent_text"):
            _codes(invalid_accent)

        episode = _episode()
        plan = _plan(episode)
        resolved = (
            ResolvedVisualFxCue(1, 1, 5, 1, 5, "pan_left", 0.5),
            ResolvedVisualFxCue(2, 6, 10, 6, 10, "pan_right", 0.5),
        )
        scene = replace(plan.scenes[0], visual_fx_cues=resolved)
        invalid_plan = replace(plan, scenes=(scene,))
        with self.assertRaisesRegex(RuntimeError, "mais de uma visual_fx_cue"):
            validate_editorial_direction(episode, invalid_plan, CATALOGS)

    def test_duplicate_highlight_text_and_consecutive_punches_warn(self):
        first = ShotSpec(
            "shot_0",
            "segment_0",
            "photo",
            "hold",
            "cut",
            HighlightSpec("41 SEMANAS NO TOPO", 0.2, None),
        )
        base = _episode(shot_count=2)
        shots = (first, base.shots[1])
        episode = replace(
            base,
            shots=shots,
            text_fx_cues=(
                TextFxCue(0.2, 1.5, "41 SEMANAS\nNO TOPO", "pop_in", accent_text="41"),
            ),
            visual_fx_cues=(
                VisualFxCue(0.1, 0.5, "punch_zoom"),
                VisualFxCue(37.6, 38.0, "punch_zoom"),
            ),
        )
        plan = _plan(episode)

        codes = _codes(episode, plan)
        self.assertIn("duplicate_editorial_text", codes)
        self.assertIn("consecutive_punch_zoom", codes)

    def test_editorial_duplicate_check_uses_resolved_relative_timing(self):
        base = _episode(shot_count=2)
        second = replace(
            base.shots[1],
            highlight=HighlightSpec("No. 1 NO BRASIL", 0.2, 1.5),
        )
        episode = replace(
            base,
            shots=(base.shots[0], second),
            text_fx_cues=(
                RelativeTextFxCue(
                    "segment_1",
                    0.2,
                    1.5,
                    "No. 1\nNO BRASIL",
                    "scale_bounce",
                    accent_text="No. 1",
                ),
            ),
        )

        self.assertIn("duplicate_editorial_text", _codes(episode))

    def test_no_effects_and_legacy_timeline_remain_valid(self):
        empty = _episode()
        empty_report = validate_editorial_direction(
            empty,
            _plan(empty),
            EditorialCatalogs(),
        )
        self.assertEqual(empty_report.warnings, ())

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            episode_dir = root / "episodes" / "legacy"
            (episode_dir / "assets").mkdir(parents=True)
            files = {
                "story.json": {
                    "schema_version": 1,
                    "title": "Legacy",
                    "slug": "legacy",
                    "target_duration_seconds": 75,
                    "segments": [{"id": "hook", "text": "Legacy narration"}],
                },
                "assets.json": {
                    "schema_version": 1,
                    "assets": [
                        {
                            "id": "photo",
                            "file": "photo.jpg",
                            "url": "",
                            "credit": "",
                            "license": "",
                            "focus": {"x": 0.5, "y": 0.5},
                        }
                    ],
                },
                "timeline.json": {
                    "schema_version": 1,
                    "shots": [
                        {
                            "id": "shot_hook",
                            "segment": "hook",
                            "asset": "photo",
                            "motion": "hold",
                            "transition_out": "cut",
                        }
                    ],
                },
            }
            for name, data in files.items():
                (episode_dir / name).write_text(json.dumps(data), encoding="utf-8")
            (episode_dir / "sources.txt").write_text("Legacy source\n", encoding="utf-8")
            legacy = load_episode(root, "episodes", "legacy")

            report = validate_editorial_direction(
                legacy,
                _plan(legacy),
                EditorialCatalogs(),
            )

        self.assertEqual(report.warnings, ())


if __name__ == "__main__":
    unittest.main()
