# Niche agent: the rules

The niche agent (`/niche-agent`) reads this file at the start of every run. ChannelRecipe's server serves the newest
copy (`GET /api/studio/niche-agent/teaching`), so a change here reaches every creator without a Studio release; Studio
keeps the copy it shipped with for when the server can't be reached.

How to change it: edit a rule or a number below, keep its date and source, and add new lessons at the end of their
section. A note the owner repeats becomes a rule. The examples the owner sends go in `examples.jsonl` (one line each).

Sources quoted below:
- [K1] "Untapped Claude AI Niche Made $16k in 28 Days" (Kevis, 2026-10-28, UXJ0xnqRbi4).
- [K2] "AI YouTube Automation Just Died… Do THIS Instead" (Kevis, 2026-10-28, K_cKt_E9QXE).
- [K3] "Claude Code + 1 Digital Product = $14,500/Mo" (Kevis, 2026-10-29, c7-cem6Zypo).
- [T1] "The 17 BEST Faceless YouTube Niches To Finish 2026" (Tim Danilov, 2026-10-28, _hORKt_5CRo).
- [D] The owner's checklist doc "More Checks Is Better".
- [N] The owner's verdicts on our own niche research, 2026-10-27 and 2026-10-28.

## The numbers (the tools read this block; change a number here and every run uses it)

Long-form is confirmed. The Shorts numbers are a proposal the owner hasn't confirmed yet (2026-10-29): runs that use
them must say so.

```json
{
  "long": {
    "banger_views": 50000,
    "flop_views": 5000,
    "flop_share_of_median": 0.10,
    "min_banger_flop_ratio": 7,
    "great_banger_flop_ratio": 10,
    "max_first_upload_days": 60,
    "great_first_upload_days": 30,
    "min_uploads_30d": 3,
    "min_median_views": 30000,
    "preferred_max_subs": 15000,
    "max_subs": 50000,
    "min_views_per_sub": 20,
    "hero_views_per_sub": 100,
    "one_hit_top_share": 0.60,
    "min_bangers": 2,
    "judge_after_days": 7,
    "window_days": 60,
    "search_min_views": 30000,
    "min_minutes": 4
  },
  "shorts": {
    "confirmed": false,
    "banger_views": 500000,
    "flop_views": 20000,
    "flop_share_of_median": 0.10,
    "min_banger_flop_ratio": 7,
    "great_banger_flop_ratio": 10,
    "max_first_upload_days": 60,
    "great_first_upload_days": 30,
    "min_uploads_30d": 6,
    "min_median_views": 200000,
    "preferred_max_subs": 50000,
    "max_subs": 50000,
    "min_views_per_sub": 20,
    "hero_views_per_sub": 100,
    "one_hit_top_share": 0.60,
    "min_bangers": 2,
    "judge_after_days": 3,
    "window_days": 60,
    "search_min_views": 300000,
    "min_minutes": 0
  }
}
```

## 0. The owner's checklist: run it on every niche and show every tick

The owner's doc [D] is called "More Checks Is Better". Every niche you show carries this checklist, one line per item,
with ✅ or ❌ and the number or channel behind it. A niche passes only with ✅ on items 1-6, 8 and 9 (7 is a bonus).
Never write "PASSED THE CHECKLIST" without the ticks under it.

1. **Videos get consistent views on average, not random outliers** [D]: the proof channel's median and recent average
   views, and its share of views in its top video (`one_hit_top_share`).
2. **Recent videos are consistent: about 7:1 banger to flop** [D] (10:1 ideal, on fewer than 30 videos posted): the
   banger:flop over the last uploads, and how many videos the channel has posted.
3. **Has competitors** [D]: name them.
4. **At least 2 competitors doing well** [D]: the proof channels, each with its numbers.
5. **No failing competitor with similar quality and recipe (CRUCIAL)** [D]: name the look-alikes you checked. A failing
   one is a ❌ unless you can show the recipe difference that explains it (pacing, look, length, subjects, cadence,
   packaging) [K1].
6. **The emergence stage** [D]: Pioneers → **Emergence (early first movers: where we want to be)** → Mass adoption →
   Saturated. Say which, with the count of winners and how new they are.
7. **Somewhat of an entry barrier** [D] (not required): what keeps others out, ideally something Claude removes [K1].
8. **The recipe and format are clear: we know why it works and who watches** [D]: one line on why it works (the
   format, the hook, the packaging) and one on the audience (who, adult or not, which countries).
9. **Monetizable** [D]: the RPM band, an adult or mixed audience, nothing kids or quiz.

Bonus [D]: evergreen, a long-term opportunity rather than a trend. Say which.

## 1. How outliers work (measure these, never guess them)

1. **Views ÷ subscribers on one video is the headline signal** (2026-10-28, [K2]): "7.1 thousand subscribers and 3
   million views… over 400 views for every subscriber. So that is the signal." Hero cards are 100× or more; under 20× is
   not an outlier.
2. **Small and new, or the data is skewed** (2026-10-29, [K3]): "We don't want to use this big channel… this is skewed
   data." A giant (Hank Green at 3.2M subs) is "bad data… out of the window". The proof channel's first upload is at most
   60 days ago, 30 or fewer is better ([N], [K2]: under 3 months; [T1]: a niche no older than 2 months).
3. **A repeated formula, never a one-hit** (2026-10-29, [K3]): "only one video went viral. We want a repeated formula."
   [D]: "Videos are getting consistent views on average (not random outliers)." [N]: when the median is under about 30K,
   the hits are luck, not a format.
4. **Banger : flop over the recent uploads** ([D], [K1]): 7:1 is the bar, 10:1 is ideal on fewer than 30 videos.
   CityEons was 4:0 and Latos Charts 19:0 [K1]. A high ratio means "a winning format with so much demand and not enough
   supply" [D]. A banger is 50K+ long-form views; a flop is under 5K or under 10% of the channel's median [N]. Videos
   still growing never count as flops: younger than 7 days for long-form, 3 days for Shorts (a default set on
   2026-10-29 so a new upload of a hot channel isn't called a flop; the owner can change it).
5. **Cadence** ([N], [K1]): the proof channel posted 3+ videos in the last 30 days. The winners posted about 3 a week;
   the flopping copycats posted 1–2 a day.
6. **Recent average views beat lifetime totals** (the owner, 2026-10-29). Judge a channel by its newest uploads.
7. **Read the real first upload date from the channel's own tab** ([N], 2026-10-28). The newest-15 window and RSS lie
   about age. Never write "N weeks old" from anything else.

## 2. A good niche vs a bad niche

A niche passes only when all of these hold:
1. **2 or more proof channels doing well, at least one started in the last 60 days** ([K1], [D]): "Most of the winners
   posted their first video in the last 9 weeks." One winner is "probably not enough" (3D body explainers) [K1].
2. **No failing look-alike with the same quality and recipe (CRUCIAL)** [D]. If look-alikes fail, name the recipe
   difference: "about half of the new ones are flopping… pacing, look, the city… it's not that the niche is failing,
   it's the recipe" [K1]. "Superpower stick figures: one winning channel, one failing channel" is out [K1].
3. **The emergence stage** ([D], [K1]): Pioneers (1 channel) → **Emergence** (2–15 winners, most started in the last 90
   days: "where we wanna be") → Mass adoption (dozens, about half flopping) → Saturated (mostly old or big channels,
   recent averages falling). Say the stage, and expect it to crowd: "will be copied by Christmas… the skill is spotting
   the next format early" [K2].
4. **An entry barrier, ideally one Claude removes** [K1]: "until a few weeks ago, you needed a 3D artist… The barrier
   keeps everyone out, everyone except people using Claude." AI-image channels are the crowded bottom of the faceless
   pyramid (about 90% of the market, quick saturation, demonetization risk); 2D/3D/research-heavy channels are the top [T1].
5. **Never AI slop** (2026-10-28, [N]): "Through the Ages is an AI slop channel… it's not a good channel to make with
   Claude." AI image or AI video channels (Midjourney/Flux stills with slow moves, Kling/Veo/Sora clips, the "AI film"
   look, AI faces) are refused even with great numbers. Look at the thumbnails before you count anything.
6. **Monetizable, with an adult or mixed audience** ([K2], [K1], [T1], [N]): education and history about $10 RPM, city
   timelapse about $5–8, finance/economics/WWII high ($6–8 for economics). Kids, quiz, guessing-game and gamey formats
   are out (low RPM, the wrong buyer): "it's low rpm since it looks gamey quiz like for kids" (Map Mayhem, 2026-10-28).
   Non-English channels earn less. Write the RPM band for every niche.
7. **Global, repeatable, ideally evergreen** [K1]: a no-voice format works in every country; "thousands of cities, and
   every one of them is a video." Evergreen is a bonus [D].
8. **An attention cluster is a bonus** [N]: several channels with 1M+ videos in the last 30–60 days, 500K+ common, new
   channels reaching 100K–1M+ within weeks. A small channel doing well in a small, kid-skewed format is not a cluster.

Refuse these:
- old channels, one-hit channels, and "a giant's wake" (one huge channel plus small one-hit copycats) [N];
- AI slop; quiz or kids content;
- formats whose same-quality look-alike fails;
- "20 niches with no proof" [K1]: every niche needs proof videos with numbers;
- the old automation pattern: stock footage, a default AI voice, top-10 facts. The test: "you could swap your video
  with a competitor's and nobody would notice" [K2];
- mass-produced, repetitive "inauthentic content" [K1, K2].

## 3. A good idea vs a bad idea

A good idea:
1. **A proven format with a new subject** [K1]: "New York already has two videos past a million. So don't do New York…
   Manila, Mexico City, Lagos." Or a twist on the lane [K2]: "The Terrain Walk does battles, you could do sieges or famous
   escapes. Same looks, but your own topic." Tim calls this niche bending (guns → battleships, planes, tanks) [T1].
   Search the subject first: if it's already done twice, it's a copy, not an idea.
2. **A big, recognisable subject plus a big span or number** [K1]: "New York for 400 years, Constantinople from 680 BC."
3. **Universal subjects**, like your own body or your own city [K1, K2].
4. **Question titles you can't stop thinking about** [K2]: "Why was this plane built like that?"
5. **Demand in the comments** [K1]: "Please do Melbourne. Can you do LA next?"
6. **Packaged like the winners, not the flops** [K1]: a bright daylight city with big year type beats a dark skyline
   with tiny text.
7. **The length of the winners** (8–9 minutes for the city timelapses) [K1].
8. **Buildable by a ChannelRecipe recipe today** [K1]: the 16K city took about 45 minutes and under $2. Name the recipe
   or style, or say "clone the proof channel's style".
9. **An honest risk line** [N]. Plan the product or call to action before the first video blows up [K3].

A bad idea:
- a copy of a title already done twice;
- no outlier video as proof;
- proof from a one-hit or giant channel;
- a look only AI imagery can make;
- kids or quiz framing;
- needs the creator's face or game capture ChannelRecipe can't make, unless it says "needs your recording" [K3].

## 4. How the agent shows its work

- Every niche and idea carries its evidence: the proof video (link, views), views ÷ subs, the channel's age (its exact
  first upload), recent average views, banger:flop, the twist, the recipe, and one risk line.
- Show what it threw out and why ("1 winning channel, 1 failing"); the owner likes seeing the rejects.
- Never re-propose a niche the examples mark "bad" without saying what changed since.
- Show any good faceless niche, and say whether ChannelRecipe's recipes can make it today (and which), or what it would
  take (a style clone, the creator's own recording). Never AI slop.
- English channels by default; another language only when the creator asks.

## 5. Lessons added later

(New dated lessons go here, newest last, each quoting the owner.)

- **2026-10-29, the owner** (on the doc's own examples): "I sent you a bunch of channels that you should take inspiration
  from for what good niches are, what bad niches are. I even sent you a checklist." The doc's two pictures are the
  model good and bad: Airspeed (aviation "Why…" explainers in one 3D look, about 6:0, "😎") and Ryoga (anime-drawn
  fitness, 2:3, "🤮": a 1.7M hit and a 265K hit beside videos stuck at 10K-64K, so the views are random outliers, not a
  format). Big views on one or two videos never make a niche; the ratio does.
- **2026-10-29, [T1] Tim Danilov's tiers** (his ranking, used as inspiration, the owner's rules win where they differ):
  the harder a format is to replicate, the higher it ranks. Top: high-quality, researched documentaries with their own
  brand (S); a whiteboard + archival military explainer, "every bullet used in the war explained", 10M views (A); a
  2D finance channel on a new angle (A). Middle: simple 2D/whiteboard true crime that stays relevant 6-12 months (B);
  trendy formats that will saturate fast, like zombie-apocalypse stories (B, short-lived). Bottom: plain AI-image
  channels (psychology, insects, ancient humans, AI-image finance) at D-E: cheap, crowded, demonetization risk, often
  not in English. Tim's own bar ("no more than 2 months old, fewer than 3 competitors") is looser than the owner's
  checklist: we still need 2+ competitors doing well and no failing same-quality look-alike.
