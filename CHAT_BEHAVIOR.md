# Miko chat behavior

Miko keeps her confident, feminine, mischievous character while responding more naturally to the actual conversation. Casual replies are usually one or two sentences; useful explanations can be longer. She uses specific observations, light compliments and quick comebacks instead of repeating pet names, stage directions or catchphrases. English and Hinglish follow the speaker's language naturally.

Existing chat-level controls remain:

- Level 0: warm, witty, clean conversation without flirting.
- Level 1: occasional light flirting and playful banter when welcomed.
- Level 2: more confident romantic wordplay and teasing, still non-explicit and subject to existing channel, safety and user-level restrictions.

A configured level permits a tone; it does not require flirting every turn. Neutral replies and topic changes should de-escalate it. Serious topics and practical help take priority. Miko must not claim to be human, invent shared memories, become possessive or pressure someone for attention.

Natural-language opt-outs such as “stop flirting, please,” “don't flirt,” “keep it platonic,” “flirt mat karo” and “flirting band karo” save a clean-chat preference. The saved level-zero preference now correctly remains zero on later requests and reloads. Existing commands can explicitly change it again. Strict generation always overrides the requested tone.

Validation covers saved preference behavior, English/Hinglish opt-outs, negated phrases, serious-mode direction, strict-mode precedence and neutral fallbacks. No paid/live model calls were made; prompt direction is tested, while actual conversational quality still requires checking with the configured provider after deployment.

## Personal opt-in controls

- `/flirt on`: enable welcomed, non-explicit flirting for your current server/channel after that server's verification. Administrators must permit chat level 1 or 2.
- `/flirt off`: keep chat clean everywhere and clear all channel opt-ins. Works in DMs and ordinary channels too.
- `/flirt status`: privately show the opt-in, current access, administrator level and provider configuration.
- Prefix equivalents: `miko!flirt on` and `miko!flirt off`. Existing `/allowflirting`, `/stopflirting` and prefix equivalents use the same controls.

No explicit consent means clean chat, including existing installations until users opt in. Every message and direct question rechecks consent, server verification and age-restricted channel access. Threads inherit their parent's administrator level and age restriction but require a separate user opt-in. An administrator's level 0 always wins. Losing access during generation discards the pending flirty response and uses a clean retry. Natural-language stop requests clear every opt-in; enabling another channel later does not revive those old opt-ins.

Restart/redeploy and allow command sync to finish. In the chosen channel, complete `/verify`, have an administrator set `/mikosetlevel` to 1 or 2, then use `/flirt on`. Use `/flirt status` if replies stay clean. AI “configured” indicates a configured provider, not a verified live API connection. Live output quality has not been tested with production credentials.
