# Miko Emotion Assets

Miko uses a fixed numeric reaction table. The ID is canonical and maps directly to the matching image filename.

| ID | Emotion |
|---:|---|
| 01 | Neutral |
| 02 | Smile |
| 03 | Happy |
| 04 | Wink |
| 05 | Laugh |
| 06 | Tease |
| 07 | Thinking |
| 08 | Curious |
| 09 | Confused |
| 10 | Surprised |
| 11 | Shocked |
| 12 | Angry |
| 13 | Embarrassed |
| 14 | Flustered |
| 15 | Sad |
| 16 | Tired |
| 17 | Annoyed |
| 18 | Pout |
| 19 | Nervous |
| 20 | Crying |
| 21 | Blushing Happy |
| 22 | Love |
| 23 | Smug |
| 24 | Sleepy |

Runtime filenames are 01.webp through 24.webp.

The emotion engine selects an ID first, then resolves that ID to the image. Missing assets do not stop chat; Miko falls back to text-only replies.
