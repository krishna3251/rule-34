"""Chat tone and persistent preference regressions, without a live model."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from services.miko_chat_engine import MikoChatEngine
from services.miko_decision import MikoDecisionEngine
from services.miko_gate import GateDecision
from services.miko_personality import MikoPersonality
from services.miko_profile import MikoProfile
from services.miko_response import MikoResponseProcessor
from services.miko_social import MikoSocialEngine


class ChatBehaviorTests(unittest.TestCase):
    def test_english_hinglish_and_punctuation_opt_outs(self):
        for text in ["Stop flirting, please!", "Miko, don't flirt with me.",
                     "Keep it platonic please", "flirt mat karo", "flirting band karo",
                     "I don't want you to flirt", "stop calling me darling",
                     "don't stop flirting, but stop teasing"]:
            with self.subTest(text=text):
                self.assertTrue(MikoPersonality.wants_clean_chat(text))
        for text in ["don't stop flirting", "never stop teasing", "hello there", "you are funny"]:
            with self.subTest(text=text):
                self.assertFalse(MikoPersonality.wants_clean_chat(text))

    def test_stop_preference_survives_reload_and_later_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profile.json'
            engine = MikoChatEngine.__new__(MikoChatEngine)
            engine.profile = MikoProfile(path)
            decisions = MikoDecisionEngine()
            decision = decisions.decide(GateDecision(True, "Stop flirting, please!", spice_level=2))
            changed = engine._apply_local_request(SimpleNamespace(author=SimpleNamespace(id=1)), decision)
            self.assertEqual(changed.spice_level, 0)
            self.assertEqual(changed.response_mode, 'support')
            profile = MikoProfile(path).get(1, 10, 20)
            self.assertEqual(engine._profile_level(profile), 0)
            later = decisions.decide(GateDecision(True, 'hello', spice_level=2), profile=profile)
            self.assertEqual(later.spice_level, 0)

    def test_strict_mode_overrides_requested_tone(self):
        prompt = MikoPersonality.system_template(2, 'serious', strict=True)
        self.assertIn('CURRENT CHAT LEVEL: 0', prompt)
        self.assertIn('No flirting.', prompt)
        self.assertNotIn('Use a bolder, naughty', prompt)

    def test_social_direction_matches_serious_and_clean_contexts(self):
        social = MikoSocialEngine()
        for intent in ['serious', 'coding', 'help', 'search', 'action']:
            text = social.prompt_block(key=intent, user_name='User', intent=intent,
                                       mood='serious', turn_count=1, spice_level=2)
            self.assertIn('Teasing permitted this turn: no', text)
            self.assertNotIn('Preferred interaction style for this turn: use a small playful aside', text)
        text = social.prompt_block(key='casual', user_name='User', intent='chat',
                                   mood='playful', turn_count=1, spice_level=1)
        self.assertIn('only when welcomed', text)

    def test_clean_fallback_does_not_add_pet_names_or_catchphrase(self):
        for reason in ['empty', 'prompt_leak', 'wrong_level', 'unknown']:
            text = MikoResponseProcessor.fallback(reason).casefold()
            self.assertNotIn('darling', text)
            self.assertNotIn('ara ara', text)
