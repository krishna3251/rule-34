from __future__ import annotations

import unittest

from services.miko_image.detector import MikoImageIntentDetector


class MikoImageIntentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = MikoImageIntentDetector()

    def test_playful_reaction(self) -> None:
        result = self.detector.analyze("bruh you really did that 😂", "hehe, caught you")
        self.assertTrue(result.needed)
        self.assertEqual(result.image_type, "reaction")
        self.assertIn(result.primary_emotion, {"laughing", "playful"})
        self.assertGreater(result.confidence, 0.6)

    def test_technical_question_can_skip_image(self) -> None:
        result = self.detector.analyze(
            "What is a Python decorator?",
            "A decorator wraps a function to extend its behavior.",
            intent="coding",
        )
        self.assertFalse(result.needed)
        self.assertEqual(result.image_type, "none")

    def test_contextual_support(self) -> None:
        result = self.detector.analyze(
            "I'm exhausted today",
            "Come here, take a break.",
            intent="serious",
        )
        self.assertTrue(result.needed)
        self.assertEqual(result.image_type, "support")
        self.assertEqual(result.primary_emotion, "tired")

    def test_repetition_is_suppressed(self) -> None:
        result = self.detector.analyze(
            "lol that's funny 😂",
            "hehe",
            previous_emotion="laughing",
            previous_image_type="reaction",
        )
        self.assertFalse(result.needed)


if __name__ == "__main__":
    unittest.main()
