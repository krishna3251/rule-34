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

## Included stickers

The default pack contains all 24 general-audience Yae Miko reactions from the chibi expression sheet. Each tile is stored as a separate WebP under its matching emotion ID.

## Install a larger Yae Miko pack

1. Put `Yae Miko Discord reaction images.zip` beside `main.py` on the bot host. The startup directory, either directory's `data/` folder, and this folder are also searched. Name each static WebP or PNG inside the ZIP using its two-digit ID, such as `02.webp` for Smile or `24.png` for Sleepy.
2. Review the images and put `Yae Miko Discord reaction images.public.json` next to the ZIP. Use the two-digit IDs as JSON keys and the lowercase SHA-256 hashes of the approved image bytes as values. This pair overrides the bundled pack for the IDs listed in its approval file. Other moods can still use their reviewed bundled images. Hash the files inside the ZIP, since the bot checks the bytes of each image.
3. Redeploy and try `/askmiko` with “Hey, Miko!” or “Goodnight, Miko. I'm going to sleep.” in a channel where the bot can attach files. The same image is skipped if it was already the last picture sent to that user in that channel within 45 minutes.

For a loose-file installation, put the reviewed images and their `public.json` in this directory. Older ZIP installations that use this directory's `public.json` also work.

The chat artwork is general-audience in every channel. Source files can be up to 8 MiB and 16 million pixels. The bot sends them as WebP up to 512 pixels on the longest side and preserves transparency. A missing, changed, corrupt or unreviewed file leaves a text reply. Update the hash after reviewing any replacement.
