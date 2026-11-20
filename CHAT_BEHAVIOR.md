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

## Yae Miko-inspired voice

Miko's voice now explicitly uses a composed, feminine, slyly amused direction inspired by Yae Miko from Genshin Impact. The style favors elegant mischief, dry observations and measured comebacks over bubbly anime mannerisms, constant pet names or repeated “Ara ara” openings. Original English/Hinglish examples guide cadence without copying game dialogue. Feminine Hindi verbs remain explicit; possessives follow the noun's gender.

The character voice applies to clean chat too: wit does not require flirting. Opt-in flirting remains non-explicit, while practical help and vulnerable conversations receive direct, considerate replies. No commands, model providers, access restrictions or stored data formats change in this voice update. Existing regression tests check runtime compatibility and controls; they do not establish live-model character fidelity.


## Brain v2

Chat now uses private user/channel/server context, with bounded 45-minute RAM history. Persistent history is off by default and requires `/mikobrain privacy`; approved facts require `/mikobrain remember`. `/mikobrain memories`, `forget`, `style`, `cancel` and `feedback` give private controls. Old shared JSON history is preserved on disk but is not imported into the new context. See [MIKO_BRAIN_V2.md](MIKO_BRAIN_V2.md) for the complete scope and retention policy.

Short non-search chat can use an optional local model. OpenRouter/Groq remain available when permitted; exact calculations and explicit memory retrieval work without a model. Styles preserve Miko's character while serious contexts take precedence. No live-model quality claim is made by the regression tests.

## Direct questions and reactions

Miko uses the actual message when she joins an auto-chat conversation. She listens before giving advice when someone is venting and uses recent private turns to resolve corrections and follow-up questions. Technical, corrective, and supportive turns stay clean even if a more playful channel setting is available. A remark like “I had a rough day today” does not trigger a live lookup. A direct question about current information still uses the existing web lookup when available.

Mention Miko, reply to one of her messages, or use the prefix command for a direct request. If a reply is still in progress after two seconds, the original Discord message gets ⏳. A delivered answer replaces it with ✅. Slash questions use Discord's native waiting indicator and put ✅ on the finished reply after the same threshold. Reactions depend on Discord permissions. Fast replies and sampled auto-chat stay free of progress reactions.

The numbered image catalog is used for fitting casual moments such as a celebration or a goodnight. Coding, search, corrections, and sensitive support are text-only. See [the reaction art installation guide](assets/miko/emotions/README.md).

## Reasoning and feminine voice

Miko checks the user’s constraints and recent conversation when diagnosing a technical issue, comparing options, or making a multi-step plan. Supported GPT-OSS models on Groq and OpenRouter get a higher reasoning budget for those turns. Other configured models can make one brief review call when there were no tool calls or live lookup. The review shares the request deadline and provider quota. If it times out, returns no usable answer, or fails validation, Miko keeps the original draft for normal validation.

In English Miko uses she/her. In Hindi and Hinglish she uses feminine forms such as “main dekh rahi hoon,” “main samajh gayi,” and “मैं बता सकती हूँ.” The reply validator catches common masculine self-references and uses the existing clean retry before sending the reply. Straightforward identity questions work without an AI provider. Coding examples and quotations keep the speaker’s own grammar.
