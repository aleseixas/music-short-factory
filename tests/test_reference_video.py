import hashlib
import unittest
from pathlib import Path

from engine.ffmpeg import probe_duration, probe_video_frame_count


REFERENCE = Path(__file__).parent / "reference" / "through_the_wire_v7.mp4"
REFERENCE_SHA256 = "c46dcc3daf7f2c2e9aa6c582ab45ff9197e471fd0346a31ba4971d34fc1f642d"


@unittest.skipUnless(REFERENCE.exists(), "video de referencia nao incluido")
class ReferenceVideoTests(unittest.TestCase):
    def test_reference_hash_duration_and_frame_count(self):
        digest = hashlib.sha256(REFERENCE.read_bytes()).hexdigest()
        self.assertEqual(digest, REFERENCE_SHA256)
        self.assertEqual(probe_video_frame_count(REFERENCE), 1119)
        self.assertAlmostEqual(probe_duration(REFERENCE), 37.30, places=2)


if __name__ == "__main__":
    unittest.main()
