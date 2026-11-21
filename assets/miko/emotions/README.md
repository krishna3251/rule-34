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

The bundled `01.webp` through `24.webp` files are ready to use with the included `public.json`.

## Replace reaction art

1. Add static general-audience WebP or PNG files using the two-digit IDs above, for example `02.webp` for Smile. A ZIP named `Yae Miko Discord reaction images.zip` can also be used from the repository root, `data/`, or this directory. Remove the matching bundled file to use the ZIP member, since loose files take precedence.
2. Review each image and confirm its expression matches the ID. In this folder, update `public.json` as a JSON object whose keys are two-digit IDs and whose values are the lowercase SHA-256 hashes of the approved source files. For a ZIP, hash the original member bytes. On Linux, `sha256sum assets/miko/emotions/02.webp` prints the hash for a loose file. On Windows PowerShell, use `(Get-FileHash assets/miko/emotions/02.webp -Algorithm SHA256).Hash.ToLower()`.
3. Check a greeting, a celebration and a goodnight in Discord. The same image is skipped if it was already the last picture sent to that user in that channel within 45 minutes.

The chat artwork is general-audience in every channel. Source files can be up to 8 MiB and 16 million pixels. The bot sends them as WebP up to 512 pixels on the longest side and preserves transparency. A missing, changed, corrupt or unreviewed file leaves a text reply. Update the hash after reviewing any replacement.
