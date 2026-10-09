# Homepage showreel — where and how

Status: on the homepage, between “See it on a demo lake” and the credit plate. The published file is the master, copied, not re-encoded.

Source film: [`videos/cryo-showreel-30s/renders/16x9.mp4`](../videos/cryo-showreel-30s/renders/16x9.mp4).
The brief already names the docs homepage as an audience
([`videos/cryo-showreel-30s/brief.md`](../videos/cryo-showreel-30s/brief.md)). The page it has to
fit is [`site/_src/pages/index.html`](../site/_src/pages/index.html).

## What the film is

30 seconds, 1920×1080, 60 fps, H.264 + AAC, about 15 MB at 4.3 Mbps. No voiceover. On-screen type
carries the story, so it is readable muted. Beats, in order:

1. Iceberg maintenance.
2. A receipt for every decision.
3. Read the catalog.
4. Plan without SQL.
5. Admit when engines have headroom.
6. Small files in, right-sized files out.
7. Wordmark and `github.com/tech-pw/cryo`.

That is the same story the homepage already tells in type, then in the maintenance machine, then
in the architecture window. The film is a trailer for those sections, not a second product.

## Where it goes

The docs home is also the landing page, so the film belongs on it. It does not belong in the hero.
The first viewport stays the promise, the tally, and the quickstart
([`site/_src/pages/index.html`](../site/_src/pages/index.html)). A 16:9 player there pushes the
primary action below the fold. Landing pages that use film well (Linear, Vercel, Raycast) still
let the headline win the first screen, and they close with the picture rather than open with it.

**Put it at the end of the story, before the credit plate.** Not after the credits — a film under
“Built & run by Physics Wallah” reads as an appendix. Not instead of “See it on a demo lake” —
that block is the action. The film is the recap a visitor earns after the page has already
explained the product.

```text
See it on a demo lake          clone, seed, quickstart          (unchanged)
Watch the pass                 30-second film, click to play    (new)
Built & run by Physics Wallah credit plate                     (unchanged)
```

Why this slot:

- By then the reader has seen the promise, the machine, the map, the operation order, and the receipt. The film repeats that pass in motion instead of competing with it.
- Someone who bounces from the hero never downloads it. Someone who scrolls the page is the person the closer is for.
- The credit plate stays the last human note. The film does not bury the people who run it.
- The maintenance machine and the architecture window stay the no-download explanations. The film does not replace either.

Rejected placements:

| Placement | Why not |
|---|---|
| Hero, beside or under the H1 | Pushes the quickstart below the fold. The landing page still needs a sentence before a trailer. |
| After the credit plate | Feels bolted on. Credits should close the page. |
| Replace the maintenance machine | The SVG works with no download, in both themes, and at 390 px. A video cannot. |
| Replace the architecture window | Different job. That figure is a map, not a trailer. |
| Above the hero | A film before the promise is a marketing homepage, and this page is both docs and landing. The promise still goes first. |
| Quickstart page | The page is a procedure. A film there interrupts the steps. |

## How comparable sites do it

Docs homes that stay useful do not autoplay a trailer in the hero.

- **Stripe, Supabase, Temporal, Dagster, Databricks, Snowflake, Iceberg** open with a one-line pitch and a task index or a quickstart. No intro video on the docs home.
- **GitHub Docs** puts video on a tutorial page, under the title, with a visible player. It does not replace the steps.
- **Cloudflare docs** leads with one sentence and one action, then a live primitive. Motion is a demo of the thing, not a brand film.
- **Linear, Vercel, and Raycast** put film on the marketing home: muted, often looping, always with a poster, and the headline still wins the first viewport. That pattern belongs on a product site, not on `tech-pw.github.io/cryo/`.
- **MDN and GOV.UK** treat video as optional media: poster first, controls visible, captions if there is speech, and a text alternative that stands alone.

The useful rule from the marketing sites is the player chrome, not the placement: poster, no sound until the visitor asks, no layout shift, and a still that reads if the file never loads.

## Player behaviour

Click to play. Nothing else.

- `preload="none"`. The page must not fetch 15 MB to paint.
- `poster` is a designed 1280×720 title card, not a mid-animation frame. Native controls cover the bottom fifth, so the line sits in the upper half. `width` and `height` reserve the 16:9 box before the file arrives.
- `controls`. No `autoplay`. No `loop` on a docs page — a looping brand film fights the section below it.
- Sound on by default. No `muted`. The visitor can still mute from the controls. The film stays readable if they do.
- `playsinline`, so iOS does not force a fullscreen takeover.
- Honour `prefers-reduced-motion`: show the poster and the control, do not start playback from scroll or hover. The site already collapses motion in [`site/assets/css/site.css`](../site/assets/css/site.css).
- One orange marker only, matching the rest of the page. No glow, no glass, no fake browser chrome. A quiet frame is enough: ink border, 12 px radius, the same wrap as the maintenance machine.
- Caption under the player, one sentence: “Thirty seconds. The same pass the diagram above shows, with sound optional.” Link “See the maintenance flow” stays where it is.
- Accessible name on the player: “Cryo in 30 seconds: read, plan, admit, receipt.”
- No voiceover, so no caption track is required for WCAG 1.2.2. Do not add a decorative transcript that invents narration. The on-screen lines are already in the homepage prose. If a voiceover is added later, ship a WebVTT file with it.
- Pause when the player leaves the viewport. A film that keeps running under the architecture diagram is noise.
- Do not use YouTube or a third-party player. The site is static, self-hosted, and has no chat or analytics embed. Keep it that way.

## File

The page plays the master. A second encode is what made the type soft: even CRF 18 at 30 fps is a different picture from the 60 fps, 4 Mbps render.

| | Published |
|---|---|
| Resolution | 1920×1080 |
| Frame rate | 60 |
| Bitrate | ~4.3 Mbps, faststart already on the master |
| Size | 15 MB |
| Audio | AAC, sound on |
| Poster | designed title card, `homepage-intro-video-poster.jpg` |
| Path | `site/assets/video/homepage-intro-video.mp4`, a copy of `videos/.../renders/16x9.mp4` |

`preload="none"` still holds. The page paints the poster and does not fetch the file until play. Copy the master when the film changes. Do not re-encode it.

Also cut the end card’s `localhost:4173` if that frame is still in the master. The public page cannot send people to a local preview. The brief’s CTA is `github.com/tech-pw/cryo`.

The 9:16 render stays off this page. It is for a feed, not a document. Do not art-direct a vertical film into a 16:9 docs column.

## Component

Follow the `<screenshot>` pattern in [`site/scripts/build.mjs`](../site/scripts/build.mjs), so authors do not paste a raw `<video>` with a broken relative path.

```html
<film name="homepage-intro-video" caption="Thirty seconds. The same pass the diagram above shows." />
```

The generator emits a `<figure>` with a native `<video>`, the poster, and the caption. No new runtime dependency. A few lines in [`site/assets/js/site.js`](../site/assets/js/site.js) pause the video on `visibilitychange` and when it scrolls out of view.

Markup sketch, not the final renderer:

```html
<figure class="film">
  <video
    controls
    playsinline
    preload="none"
    muted
    width="1920"
    height="1080"
    poster="../assets/video/homepage-intro-video-poster.jpg"
    aria-label="Cryo in 30 seconds: read, plan, admit, receipt">
    <source src="../assets/video/homepage-intro-video.mp4" type="video/mp4" />
  </video>
  <figcaption>Thirty seconds. The same pass the diagram above shows.</figcaption>
</figure>
```

Place that figure in a new `<section class="sect film-close">` between `.try-line` and `.credit` in [`site/_src/pages/index.html`](../site/_src/pages/index.html). Heading: “Watch the pass”. One sentence under it, then the player, then a text link back to the quickstart so the closer still has an action. The credit plate remains the last section.

## Check before calling it done

- 1440 px: hero still owns the first viewport. The film is the last section before the credit plate, after “See it on a demo lake”.
- 390 px: poster is full width of the wrap, 16:9, no horizontal scroll, controls reachable.
- Light and dark: the frame uses the page border, not a hardcoded cinema black that clashes in light mode.
- Network: first load transfers the poster only. The mp4 starts on play.
- Reduced motion: poster only until an explicit play.
- Keyboard: the control is a real `<video controls>` button, focusable, with a visible focus ring.
- `node site/scripts/build.mjs --check` still passes, and the homepage `index.md` mentions the film in one sentence so the markdown alternate is not silent about it.
- No invented metrics in the poster or the caption. The film’s own on-screen lines are the copy; do not add a stat row beside the player.
