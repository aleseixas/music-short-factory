import unittest

from engine.config import MotionStyle
from engine.motion import build_motion_filter, cosine_ease


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.style = MotionStyle(
            zoom_amount=0.035,
            pan_zoom=1.055,
            contrast=1.02,
            saturation=1.03,
            brightness=-0.005,
        )

    def test_cosine_ease_is_monotonic_and_clamped(self):
        values = [cosine_ease(index / 20) for index in range(21)]
        self.assertEqual(cosine_ease(-1), 0.0)
        self.assertEqual(cosine_ease(2), 1.0)
        self.assertAlmostEqual(values[0], 0.0)
        self.assertAlmostEqual(values[-1], 1.0)
        self.assertTrue(all(left <= right for left, right in zip(values, values[1:])))

    def test_filter_uses_easing_oversampling_and_square_pixels(self):
        result = build_motion_filter(
            motion="push_in",
            frames=120,
            fps=30,
            output_width=720,
            output_height=1280,
            working_scale=2,
            focus_x=0.6,
            focus_y=0.4,
            style=self.style,
        )
        self.assertIn("cos(PI", result)
        self.assertIn("perspective=", result)
        self.assertIn("interpolation=cubic", result)
        self.assertIn("scale=1440:2560", result)
        self.assertIn("scale=720:1280", result)
        self.assertIn("setsar=1", result)
        self.assertIn("out_range=tv", result)

    def test_unknown_motion_fails(self):
        with self.assertRaises(RuntimeError):
            build_motion_filter("shake", 60, 30, 720, 1280, 2, 0.5, 0.5, self.style)


if __name__ == "__main__":
    unittest.main()
