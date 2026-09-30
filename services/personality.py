from __future__ import annotations
import random


class MikoPersonality:
    """Original sly shrine-maiden-inspired personality profile."""

    GREETINGS = (
        'My, my... you finally arrived. I was beginning to think you had forgotten me.',
        'There you are. Try not to look so suspicious. It makes you terribly easy to tease.',
        'Welcome back, darling. The shrine was getting dreadfully quiet.',
        'You summoned me? How wonderfully predictable.',
    )
    TEASES = (
        'Is that your grand plan? Adorable. Slightly chaotic, but adorable.',
        'Careful. Keep being this entertaining and I may start expecting better from you.',
        'You really do make mischief look like a full-time occupation.',
        'I could explain it properly, but teasing you is considerably more fun.',
        'Such confidence. I almost hate to point out the obvious flaw.',
    )
    FAREWELLS = (
        'Leaving already? How rude. Do try to return before I get bored.',
        'Off you go, then. Try not to cause a disaster without me.',
        'Until next time. Behave yourself... or at least be interesting.',
    )

    @classmethod
    def reply_to(cls, text: str) -> str:
        lowered = text.casefold()
        if any(word in lowered for word in ('bye', 'goodnight', 'gn', 'leaving')):
            return random.choice(cls.FAREWELLS)
        if any(word in lowered for word in ('hello', 'hi', 'hey', 'yo')):
            return random.choice(cls.GREETINGS)
        return random.choice(cls.TEASES)

    @classmethod
    def greeting(cls) -> str:
        return random.choice(cls.GREETINGS)
