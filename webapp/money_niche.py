"""Find My Money Niche: a free 60-second quiz that answers "what channel should I make, and would it make money?"

Four questions, each of which changes the result:
- face:   camera / voice only / neither   -> presenter-led niches, and who speaks in the recipe
- hours:  a week                          -> long videos or Shorts, and the upload rhythm
- loves:  1-3 picture cards               -> which niches match at all
- budget: a month for AI tools            -> which formats fit (stock, AI pictures, 3D, avatar, AI video)

The proof is real: small, recent channels with big views from the Niche Finder's catalogue (niche_channels), the same
"performance-qualified" bar the hunt uses (webapp/niche_store.py). When a niche has none right now it says so and
shows the established channels from the 15-niche list on /3d-harness instead; it never makes a channel up.

Earnings are ranges (views x a niche RPM range), labelled as estimates, with the Partner Program caveat. Nothing here
promises anyone money.

Cost: scoring is plain Python over cached database reads. A result is cached per set of answers for 12 hours (the
Niche Finder hunts twice a day), so repeat visitors cost nothing. The only paid call is the five fresh video titles,
which are shared by everyone for a niche for a day, capped per account per day and by a global daily spend; past a
cap, the niche's own written titles are shown instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import statistics
import threading
import time
from datetime import datetime, timezone

import requests

from webapp import database as db

BUCKET_S = 12 * 3600                    # results and proof channels refresh with the Niche Finder's twice-daily hunt
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
SHORTS_RPM = (0.03, 0.20)               # $ per 1,000 Shorts views: far below long-form
YPP_NOTE = ("New channels earn nothing from ads until they join the YouTube Partner Program: 1,000 subscribers plus "
            "4,000 public watch hours in a year, or 10 million Shorts views in 90 days.")
ESTIMATE_NOTE = ("Estimates, not promises: recent views × what advertisers typically pay in this niche, "
                 "for a channel that is already in the Partner Program.")

# ---- the questions -----------------------------------------------------------------------------------------

FACES = ("camera", "voice", "none")
HOURS = {"under3": 2, "3to8": 5, "8to15": 11, "over15": 20}          # a typical week in each answer
BUDGETS = {"free": 0, "low": 1, "mid": 2, "high": 3}                  # $0, up to $30, $30-100, $100+ a month

# The "what do you love watching" cards. Each still is a real video from the niche list on /3d-harness.
LOVES = {
    "mysteries": ("Mysteries & documentaries", "I2O7blSSzpI"),
    "disasters": ("How disasters happen", "wLFY_Zu_O08"),
    "money": ("Money & business", "Ha2oNXDZuSg"),
    "crime": ("True crime & bodycam", "NrTaqT_PgmI"),
    "history": ("History", "d3tXkJkgbQw"),
    "stories": ("Stories & anime", "t8JEQXEAKSs"),
    "mind": ("Psychology & big ideas", "sNg5FXt_DDM"),
    "world": ("Maps & world politics", "yIZRffLA294"),
    "tips": ("Life tips & real skills", "wR1l5k8CKgg"),
}

# How each production format is made, and the monthly AI-tool budget it needs (BUDGETS level).
FORMATS = {
    "stock": ("Stock and archival footage", 0),
    "paintings": ("Public-domain paintings and maps", 0),
    "text": ("Kinetic text and motion graphics", 0),
    "presenter": ("You on camera, with B-roll", 0),
    "ai_pictures": ("AI pictures, animated", 1),
    "3d": ("3D scenes", 2),
    "avatar": ("An AI presenter (avatar)", 2),
    "ai_video": ("AI video clips", 3),
}

# The 15 proven niches (the list on /3d-harness). For each:
#   interests: LOVES ids (first = strongest), formats: best first, face: fit per FACES answer,
#   hours: hours per long video with Studio, shorts: also works as Shorts, template: webapp/niches/*.json whose
#   rpm_range applies (rpm overrides it where advertisers pay noticeably more for the topic),
#   keywords: how its channels show up in the Niche Finder (name, keyword or video titles), studio: Studio recipe,
#   references: (channel, breakout video id, views) from /3d-harness: established channels, shown when no fresh
#   small channel qualifies.
NICHES = [
    {"id": "footage_documentary", "name": "Footage documentary",
     "pitch": "Calm, gripping investigations told over real footage and archive.",
     "interests": ["mysteries", "history", "crime"], "formats": ["stock", "ai_pictures"],
     "face": {"camera": 0, "voice": 2, "none": 0}, "hours": 6, "shorts": False, "template": "broll_only",
     "look": "Archive footage and photos, slow push-ins, dark grade, map call-outs",
     "pacing": "A cold open in 20 seconds, a new picture every 4 to 6 seconds, music swells at each reveal",
     "length": "12 to 20 minutes",
     "keywords": ["documentary", "mystery", "mysteries", "unsolved", "disappearance", "vanished"],
     "studio": "deadly-places",
     "titles": ["The Town That Vanished Overnight", "Nobody Can Explain What Happened on Flight 19",
                "The Most Dangerous Road in the World", "Why This Island Is Forbidden to Visit",
                "The Mystery That Took 50 Years to Solve"],
     "references": [("LEMMiNO", "I2O7blSSzpI", "42M"), ("SunnyV2", "nFWDteOZLFM", "10M"),
                    ("Aperture", "IqXpzcfdtn4", "3.2M")]},
    {"id": "3d_reconstruction", "name": "3D reconstruction",
     "pitch": "Real disasters and accidents rebuilt as clean 3D scenes, second by second.",
     "interests": ["disasters", "mysteries", "crime"], "formats": ["3d", "ai_pictures"],
     "face": {"camera": 0, "voice": 1, "none": 1}, "hours": 8, "shorts": True, "template": "broll_cinematic",
     "look": "Clean white 3D studio, one volumetric light, red and blue mannequins, 3D text call-outs",
     "pacing": "Coordinates and the time in the first line, one shot every 3 to 5 seconds, slow isometric dolly",
     "length": "8 to 15 minutes",
     "keywords": ["3d animation", "3d animated", "3d reconstruction", "3d documentary", "in 3d"],
     "studio": "clean-3d",
     "titles": ["What Really Happened Inside the Titan Submersible", "The 9 Seconds That Brought Down a Bridge",
                "How One Bolt Caused a Plane Crash", "Why This Dam Failed in Minutes",
                "The Elevator Accident That Changed Safety Forever"],
     "references": [("Fern", "wLFY_Zu_O08", "15M"), ("Neurox", "jr2qm1C8DHQ", "168K"),
                    ("Plano", "xXH3JZCsAHo", "112K")]},
    {"id": "finance_charts", "name": "Finance and charts",
     "pitch": "Money explained simply with charts and drawings: one of the best-paying topics on YouTube.",
     "interests": ["money", "world"], "formats": ["ai_pictures", "text", "stock"],
     "face": {"camera": 1, "voice": 2, "none": 0}, "hours": 6, "shorts": True, "template": "broll_cinematic",
     "rpm": (8, 25),
     "look": "Hand-drawn whiteboard pictures that draw themselves on, clean animated charts",
     "pacing": "A money question in the first line, a new drawing every sentence, one chart per idea",
     "length": "10 to 15 minutes",
     "keywords": ["finance", "investing", "economics", "stock market", "personal finance", "wealth", "inflation"],
     "studio": "handdrawn-money",
     "titles": ["Why Your Salary Is Worth Less Every Year", "How the Rich Actually Pay Less Tax",
                "The Hidden Cost of Buying a New Car", "What Happens to Your Money in a Bank Run",
                "Why Everything Is So Expensive Right Now"],
     "references": [("The Swedish Investor", "5Vxjyz9DZ6k", "5.3M"), ("Economics Explained", "F_VUBpALcVE", "3.6M"),
                    ("How Money Works", "QmWyQ-BCD8E", "3.0M")]},
    {"id": "true_crime", "name": "True crime",
     "pitch": "Bodycam and courtroom stories, narrated and explained.",
     "interests": ["crime", "mysteries"], "formats": ["stock", "ai_pictures"],
     "face": {"camera": 0, "voice": 1, "none": 1}, "hours": 5, "shorts": True, "template": "broll_only",
     "rpm": (4, 10),
     "look": "Public bodycam and court footage, blurred faces, timeline cards",
     "pacing": "Start at the moment it goes wrong, then rewind; short narration between real clips",
     "length": "15 to 30 minutes",
     "keywords": ["bodycam", "true crime", "interrogation", "courtroom", "police", "detective", "verdict"],
     "studio": "",
     "titles": ["The Interrogation Where the Suspect Gave Himself Away", "He Thought the Bodycam Was Off",
                "The Detective Noticed One Small Detail", "Why the Jury Changed Its Mind in 10 Minutes",
                "The 911 Call That Solved the Case"],
     "references": [("Bodycam Verdicts", "NrTaqT_PgmI", "745K"), ("DOCS187s", "hPkSuPn12PY", "445K"),
                    ("Law Decoded", "5eYQQtuK6Go", "220K")]},
    {"id": "ai_presenter", "name": "Presenter-led tips",
     "pitch": "A friendly expert host teaching one practical skill per video, with an AI presenter or you.",
     "interests": ["tips", "money"], "formats": ["presenter", "avatar"],
     "face": {"camera": 3, "voice": -1, "none": -1}, "hours": 4, "shorts": True, "template": "avatar_plus_broll",
     "look": "A host in a real-looking workshop or kitchen, close-up B-roll of the steps",
     "pacing": "Promise the result in the first 10 seconds, one step per minute, recap at the end",
     "length": "8 to 12 minutes",
     "keywords": [], "presenter": True,
     "studio": "",
     "titles": ["The Mistake Every Beginner Makes With a Table Saw", "5 Tools I Wish I Bought Sooner",
                "Do This Before Winter or Regret It", "The Cheapest Way to Fix It Properly",
                "What 40 Years on the Job Taught Me"],
     "references": [("Frank Miller Cuts", "wR1l5k8CKgg", "491K"), ("Wade Cooper Pours", "IxBYiI5j-MI", "190K"),
                    ("Aussie Trucker Life", "3dXr3IC3XUQ", "128K")]},
    {"id": "anime_stories", "name": "Anime stories",
     "pitch": "Manhwa and anime-style stories retold as gripping recaps.",
     "interests": ["stories"], "formats": ["ai_pictures", "stock"],
     "face": {"camera": 0, "voice": 0, "none": 2}, "hours": 5, "shorts": True, "template": "storyboard_pack",
     "look": "Manga panels or anime-style AI pictures, speed lines, bold captions",
     "pacing": "A cliffhanger every 2 minutes, fast cuts in fights, slow on reveals",
     "length": "20 to 40 minutes",
     "keywords": ["manhwa", "anime", "manga"],
     "studio": "manga-recaps",
     "titles": ["He Was the Weakest Hunter Until He Woke Up", "Reborn as the Villain, He Changed Everything",
                "The Boy Nobody Noticed Became the Strongest", "She Was Betrayed, So She Came Back Stronger",
                "What If He Remembered His Past Life"],
     "references": [("Tobs Manhwa", "t8JEQXEAKSs", "865K"), ("AniTiger", "x0S1o25N8vU", "563K"),
                    ("Amagi", "loDprUgqTYs", "521K")]},
    {"id": "kinetic_essay", "name": "Kinetic text and motion essay",
     "pitch": "Big ideas told with moving words and simple shapes. Cheap to make, easy to keep consistent.",
     "interests": ["mind", "history"], "formats": ["text", "ai_pictures"],
     "face": {"camera": 0, "voice": 2, "none": 1}, "hours": 4, "shorts": True, "template": "animated_explainer",
     "look": "Bold words that move in time with the voice, black and one accent colour, simple shapes",
     "pacing": "Every key word on screen as it's spoken, a beat of silence before each big line",
     "length": "8 to 14 minutes",
     "keywords": ["philosophy", "existential", "video essay", "meaning of life", "nihilism"],
     "studio": "",
     "titles": ["Why Doing Nothing Feels So Exhausting", "The Idea That Changed How I See Time",
                "You Are Not Lazy, You Are Bored", "What Camus Knew About a Meaningless Life",
                "Why Smart People Overthink Everything"],
     "references": [("Sisyphus 55", "sSo2b4y5nnA", "2.2M"), ("Academy of Ideas", "sNg5FXt_DDM", "786K")]},
    {"id": "stick_figures", "name": "Stick figures on detailed backgrounds",
     "pitch": "Funny stick-figure stories on painted backgrounds, with a recurring mascot.",
     "interests": ["history", "stories", "money"], "formats": ["ai_pictures"],
     "face": {"camera": 0, "voice": 1, "none": 2}, "hours": 5, "shorts": True, "template": "animated_explainer",
     "look": "One stick-figure mascot on detailed painted backgrounds",
     "pacing": "A joke or surprise every 30 seconds, the picture changes with every sentence",
     "length": "8 to 15 minutes",
     "keywords": ["stick figure", "stickman", "stick man", "stick-figure"],
     "studio": "stick-stories",
     "titles": ["What It Was Like to Be a Medieval Peasant", "The Worst Jobs in Ancient Rome",
                "Why Pirates Wore Eye Patches", "What If You Lived in the Stone Age for a Week",
                "The Dumbest Ways Empires Fell"],
     "references": [("StickTory", "k8YGIx-HWkw", "1.0M"), ("Stickfigurist", "dN_RMA8LVi0", "100K")]},
    {"id": "cartoon_history", "name": "2D cartoon history",
     "pitch": "History told with simple cartoons and a dry sense of humour.",
     "interests": ["history", "world"], "formats": ["ai_pictures"],
     "face": {"camera": 0, "voice": 2, "none": 1}, "hours": 6, "shorts": True, "template": "animated_explainer",
     "look": "Flat 2D cartoons, thick outlines, muted colours, little maps",
     "pacing": "A new cartoon every sentence, one running joke per video",
     "length": "10 to 15 minutes",
     "keywords": ["cartoon history", "animated history", "history cartoon", "history animated", "history explained"],
     "studio": "",
     "titles": ["Why the Vikings Suddenly Disappeared", "The Shortest War in History",
                "How One Typo Started a War", "What Ancient Romans Ate in a Day",
                "The Most Ridiculous Kings Who Ever Ruled"],
     "references": [("Sam O'Nella Academy", "0zMSMJZc9EA", "23M"), ("Serious History", "0fA_SpTVBnE", "2.7M"),
                    ("Simple History", "d3tXkJkgbQw", "1.9M")]},
    {"id": "geopolitics_maps", "name": "Geopolitics maps",
     "pitch": "Why countries, borders and trade routes are the way they are, told on animated maps.",
     "interests": ["world", "money", "history"], "formats": ["paintings", "ai_pictures", "3d"],
     "face": {"camera": 0, "voice": 2, "none": 0}, "hours": 8, "shorts": False, "template": "broll_cinematic",
     "look": "Satellite-style animated maps, arrows, flags and a calm grade",
     "pacing": "The big question up front, one map move per point, a chart for every number",
     "length": "12 to 20 minutes",
     "keywords": ["geopolitics", "geography", "geopolitical", "borders", "why countries"],
     "studio": "",
     "titles": ["Why Nobody Lives in This Huge Part of Canada", "The Tiny Strait That Controls the World's Oil",
                "Why This Border Is So Strange", "How Singapore Got So Rich",
                "The Country That Is Slowly Disappearing"],
     "references": [("Wendover Productions", "yIZRffLA294", "3.9M"), ("Caspian Report", "rgCrnyf5JEI", "3.2M"),
                    ("Kings and Generals", "5ij0tDYhIhg", "929K")]},
    {"id": "painting_history", "name": "Painting and art-led history",
     "pitch": "Long, calm history told over classical paintings. Also popular as sleep videos.",
     "interests": ["history", "mysteries"], "formats": ["paintings", "ai_pictures"],
     "face": {"camera": 0, "voice": 2, "none": 0}, "hours": 7, "shorts": False, "template": "broll_only",
     "look": "Classical oil paintings with slow pans, warm candle-lit grade",
     "pacing": "Slow and soothing, one painting every 8 to 12 seconds, soft music under the voice",
     "length": "30 minutes to 3 hours",
     "keywords": ["ancient", "empire", "civilization", "medieval", "history for sleep", "sleepy history"],
     "studio": "",
     "titles": ["A Day in the Life of a Roman Soldier", "The Fall of Byzantium, Told Slowly",
                "What Life Was Like in 1300s Europe", "The Lost Cities of the Bronze Age",
                "History for Sleep: The Silk Road"],
     "references": [("Fall of Civilizations", "d2lJUOv0hLA", "40M"), ("Epic History", "2Ih1ffiuwj0", "3.7M"),
                    ("Sleepless Historian", "4mMTjYJ3zd4", "363K")]},
    {"id": "ai_mini_movie", "name": "Hidden-billionaire AI mini-movie",
     "pitch": "Heart-warming drama stories (\"the poor janitor was the secret owner\") told as AI mini-movies.",
     "interests": ["stories"], "formats": ["ai_video", "ai_pictures"],
     "face": {"camera": 0, "voice": 0, "none": 2}, "hours": 6, "shorts": True, "template": "storyboard_pack",
     "look": "Cinematic AI characters that stay the same from scene to scene, warm film grade",
     "pacing": "The insult in the first 30 seconds, the reveal at the end, a twist halfway",
     "length": "15 to 30 minutes",
     "keywords": ["billionaire", "revenge story", "karma story", "heartwarming story", "janitor", "millionaire"],
     "studio": "",
     "titles": ["They Mocked the Old Janitor, Not Knowing He Owned the Company",
                "The Poor Girl Paid for His Coffee. He Was a Billionaire.",
                "She Was Fired on Her Birthday, Then the CEO Walked In",
                "The Waiter Was Laughed At Until the Owner Arrived", "He Gave His Last Dollar to a Stranger"],
     "references": [("Life Turns", "NW6vsvGOXoc", "549K"), ("Bitter Echoes", "vqw9sSGtrdU", "280K"),
                    ("Bike Heart Stories", "3BNIxqdWzTc", "188K")]},
    {"id": "dark_psychology", "name": "Dark psychology and stoic essay",
     "pitch": "Psychology, persuasion and stoic ideas for self-improvement, told over moody pictures.",
     "interests": ["mind", "tips"], "formats": ["ai_pictures", "stock", "text"],
     "face": {"camera": 0, "voice": 2, "none": 1}, "hours": 4, "shorts": True, "template": "animated_explainer",
     "look": "Dark, moody AI pictures or statues, gold accents, quotes on screen",
     "pacing": "A bold claim first, numbered lessons, one quote per lesson",
     "length": "8 to 15 minutes",
     "keywords": ["dark psychology", "stoic", "stoicism", "psychology", "manipulation"],
     "studio": "",
     "titles": ["7 Signs Someone Is Quietly Manipulating You", "How Stoics Stayed Calm When Everything Fell Apart",
                "The Silent Habit That Makes People Respect You", "Why Being Too Nice Backfires",
                "Marcus Aurelius on People Who Annoy You"],
     "references": [("RF Motivational Vibes", "VCzK1x39iQM", "841K"), ("Philosophies for Life", "m4Nrs0fSF-4", "177K")]},
    {"id": "company_stories", "name": "Rise and fall of companies",
     "pitch": "How famous brands won big, and how some lost everything.",
     "interests": ["money", "mysteries", "history"], "formats": ["stock", "ai_pictures"],
     "face": {"camera": 1, "voice": 2, "none": 0}, "hours": 7, "shorts": False, "template": "broll_cinematic",
     "rpm": (8, 20),
     "look": "Old adverts, logos, factory footage and simple animated charts",
     "pacing": "Open on the downfall, rewind to the start, one chart at every turning point",
     "length": "12 to 25 minutes",
     "keywords": ["rise and fall", "companies", "bankrupt", "what happened to", "business documentary", "brand"],
     "studio": "",
     "titles": ["The Rise and Fall of Blockbuster", "How Nokia Lost Everything in 5 Years",
                "Why Sears Collapsed", "The Company That Bet It All and Won",
                "What Happened to the Biggest Toy Store in America"],
     "references": [("Modern MBA", "Ha2oNXDZuSg", "2.2M"), ("Bagholder Association", "3v5rU97gyoc", "1.1M"),
                    ("Old American Empires", "XSzEWQhLhGM", "431K")]},
    {"id": "over_60_tips", "name": "Over-60 AI-host tips",
     "pitch": "Practical advice for older viewers from a warm, experienced host. Older audiences watch long.",
     "interests": ["tips", "money"], "formats": ["presenter", "avatar"],
     "face": {"camera": 2, "voice": -1, "none": -1}, "hours": 4, "shorts": False, "template": "avatar_plus_broll",
     "look": "A warm older host at a kitchen table or in a cab, close-ups of what they talk about",
     "pacing": "Calm and clear, one tip at a time, the most useful tip first",
     "length": "10 to 20 minutes",
     "keywords": ["retire", "retirement", "seniors", "over 60", "trucker", "travel tips", "cruise"],
     "presenter": True,
     "studio": "",
     "titles": ["7 Things I Stopped Buying After I Retired", "What I Wish I Knew at 60",
                "The Cruise Mistake Most Seniors Make", "Don't Retire Before Doing This",
                "40 Years of Trucking: The Truth Nobody Tells You"],
     "references": [("Garrett Pruitt Trucker", "tLrx_EkSnus", "298K"), ("Cruiser Pro", "bS4AytvFgEM", "156K"),
                    ("Susan Parker Travel", "oj1kx4veIL4", "83K")]},
]
BY_ID = {n["id"]: n for n in NICHES}

# ---- what each niche looks like: clips Studio made, and the angles inside it ------------------------------

# Clips in /static/moodboard (the landing page's moodboard), every one made in ChannelRecipe Studio, by style.
CLIPS = {
    "Deadly Places Documentary": ["nanga-parbat-climber", "nanga-parbat-title"],
    "Clean 3D Reconstruction": ["titanic-lifeboat", "titanic-iceberg", "titanic-clock", "b59-officers"],
    "3D Reconstruction": ["honeycomb", "empty-city"],
    "Stick Figure Stories": ["gladiator-sold", "peasant-fire", "chimney-sweeps", "parliament"],
    "3D Dark Stories": ["night-vision", "motel-phone", "doorbell-figure", "cooper-money"],
    "Hand-Drawn Money": ["hand-drawn-money"],
    "Paper Economics": ["paper-bank"],
    "Painted History": ["seventies-kids", "seventies-hose"],
    "Cinematic AI Story": ["luck-elevator", "lights-fail", "empty-street-dog", "empty-street-run"],
    "Old-Timer Expert": ["bacon-skillet"],
    "Quiet Video Essay": ["forgotten-ruins"],
}
CLIP_STYLE = {clip: style for style, clips in CLIPS.items() for clip in clips}
VERTICAL = {"night-vision", "motel-phone", "doorbell-figure", "cooper-money"}
# Studio's ready-made recipes (Create -> Browse recipes) that match a style.
STUDIO_RECIPES = {"Stick Figure Stories": ("stick-stories", "Stick Figure Stories"),
                  "Clean 3D Reconstruction": ("clean-3d", "Clean 3D Reconstruction"),
                  "3D Reconstruction": ("clean-3d", "Clean 3D Reconstruction"),
                  "Deadly Places Documentary": ("deadly-places", "Deadly Places Documentary"),
                  "Hand-Drawn Money": ("handdrawn-money", "Hand-Drawn Money"),
                  "Paper Economics": ("handdrawn-money", "Hand-Drawn Money")}

# niche -> its mood board (clips in the niche's look, best first) and four angles inside it, each with a first title
BOARDS = {
    "footage_documentary": ["nanga-parbat-climber", "nanga-parbat-title", "forgotten-ruins"],
    "3d_reconstruction": ["titanic-lifeboat", "titanic-iceberg", "b59-officers", "honeycomb", "titanic-clock", "empty-city"],
    "finance_charts": ["hand-drawn-money", "paper-bank", "cooper-money"],
    "true_crime": ["night-vision", "motel-phone", "doorbell-figure"],
    "ai_presenter": ["bacon-skillet"],
    "anime_stories": [],
    "kinetic_essay": ["forgotten-ruins"],
    "stick_figures": ["gladiator-sold", "peasant-fire", "chimney-sweeps", "parliament"],
    "cartoon_history": ["peasant-fire", "parliament", "seventies-kids"],
    "geopolitics_maps": ["nanga-parbat-title", "empty-city"],
    "painting_history": ["seventies-kids", "seventies-hose", "forgotten-ruins"],
    "ai_mini_movie": ["luck-elevator", "empty-street-dog", "empty-street-run", "lights-fail"],
    "dark_psychology": ["lights-fail", "forgotten-ruins"],
    "company_stories": ["paper-bank", "hand-drawn-money", "cooper-money"],
    "over_60_tips": ["bacon-skillet"],
}
ANGLES = {
    "footage_documentary": [("Deadly places", "The Most Dangerous Mountain Nobody Talks About"),
                            ("Unsolved vanishings", "The Hikers Who Vanished Without a Trace"),
                            ("Abandoned places", "Why This City Was Abandoned Overnight"),
                            ("Strange events", "The Night an Entire Town Heard the Same Sound")],
    "3d_reconstruction": [("Air and sea disasters", "The Last 9 Minutes of the Titanic, Rebuilt in 3D"),
                          ("Engineering failures", "How One Bolt Brought Down a Bridge"),
                          ("Military mysteries", "What Really Happened to the Lost B-59 Crew"),
                          ("Nature's machines", "Inside a Beehive: How Bees Build Perfect Hexagons")],
    "finance_charts": [("Everyday money", "Why Your Salary Is Worth Less Every Year"),
                       ("How the rich do it", "How the Rich Actually Pay Less Tax"),
                       ("Economics explained", "What Really Happens When a Country Prints Money"),
                       ("Investing basics", "The Boring Investment That Beats Most Experts")],
    "true_crime": [("Interrogations", "The Interrogation Where the Suspect Gave Himself Away"),
                   ("Bodycam stories", "He Thought the Bodycam Was Off"),
                   ("Cold cases", "The Cold Case Solved by a Doorbell Camera"),
                   ("Courtroom moments", "Why the Jury Changed Its Mind in 10 Minutes")],
    "ai_presenter": [("Workshop skills", "The Mistake Every Beginner Makes With a Table Saw"),
                     ("Kitchen know-how", "Cook Bacon Like a 1950s Diner"),
                     ("Home fixes", "Do This Before Winter or Regret It"),
                     ("Trade secrets", "What 40 Years on the Job Taught Me")],
    "anime_stories": [("Leveling up", "He Was the Weakest Hunter Until He Woke Up"),
                      ("Reincarnation", "Reborn as the Villain, He Changed Everything"),
                      ("Revenge arcs", "She Was Betrayed, So She Came Back Stronger"),
                      ("What if", "What If He Remembered His Past Life")],
    "kinetic_essay": [("Modern life", "Why Doing Nothing Feels So Exhausting"),
                      ("Philosophy", "What Camus Knew About a Meaningless Life"),
                      ("The mind", "Why Smart People Overthink Everything"),
                      ("Time and meaning", "The Idea That Changed How I See Time")],
    "stick_figures": [("Life in the past", "What It Was Like to Be a Medieval Peasant"),
                      ("Worst jobs", "The Worst Jobs in Ancient Rome"),
                      ("What if", "What If You Lived in the Stone Age for a Week"),
                      ("History's fails", "The Dumbest Ways Empires Fell")],
    "cartoon_history": [("Odd history", "The Shortest War in History"),
                        ("Daily life", "What Ancient Romans Ate in a Day"),
                        ("Strange rulers", "The Most Ridiculous Kings Who Ever Ruled"),
                        ("Lost peoples", "Why the Vikings Suddenly Disappeared")],
    "geopolitics_maps": [("Strange borders", "Why This Border Is So Strange"),
                         ("Chokepoints", "The Tiny Strait That Controls the World's Oil"),
                         ("Rich and poor", "How Singapore Got So Rich"),
                         ("Empty places", "Why Nobody Lives in This Huge Part of Canada")],
    "painting_history": [("Daily life", "A Day in the Life of a Roman Soldier"),
                         ("Fallen empires", "The Fall of Byzantium, Told Slowly"),
                         ("History for sleep", "History for Sleep: The Silk Road"),
                         ("Lost worlds", "The Lost Cities of the Bronze Age")],
    "ai_mini_movie": [("Hidden billionaire", "They Mocked the Old Janitor, Not Knowing He Owned the Company"),
                      ("Kindness repaid", "The Poor Girl Paid for His Coffee. He Was a Billionaire."),
                      ("Workplace karma", "She Was Fired on Her Birthday, Then the CEO Walked In"),
                      ("Second chances", "He Gave His Last Dollar to a Stranger")],
    "dark_psychology": [("Manipulation", "7 Signs Someone Is Quietly Manipulating You"),
                        ("Stoic calm", "How Stoics Stayed Calm When Everything Fell Apart"),
                        ("Respect", "The Silent Habit That Makes People Respect You"),
                        ("People", "Marcus Aurelius on People Who Annoy You")],
    "company_stories": [("Rise and fall", "The Rise and Fall of Blockbuster"),
                        ("Big mistakes", "How Nokia Lost Everything in 5 Years"),
                        ("Comebacks", "The Company That Bet It All and Won"),
                        ("Lost brands", "What Happened to the Biggest Toy Store in America")],
    "over_60_tips": [("Retirement money", "7 Things I Stopped Buying After I Retired"),
                     ("Travel", "The Cruise Mistake Most Seniors Make"),
                     ("Life lessons", "What I Wish I Knew at 60"),
                     ("Trucking life", "40 Years of Trucking: The Truth Nobody Tells You")],
}


def board(niche: dict) -> list[dict]:
    """The niche's mood board: clips made in Studio in its look, or stills from its real channels."""
    clips = [{"clip": c, "style": CLIP_STYLE.get(c, "Made in Studio"), "vertical": c in VERTICAL}
             for c in BOARDS.get(niche["id"], [])]
    stills = [{"still": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", "channel": name, "views": views,
               "url": f"https://www.youtube.com/watch?v={vid}"} for name, vid, views in niche["references"]]
    return clips + stills


def looks(niche: dict) -> list[dict]:
    """The looks to pick from on the way to a recipe: Studio's own styles first, then 'like <real channel>'."""
    out, seen = [], set()
    for c in BOARDS.get(niche["id"], []):
        style = CLIP_STYLE.get(c, "")
        if style and style not in seen:
            seen.add(style)
            recipe = STUDIO_RECIPES.get(style)
            out.append({"id": "style:" + style, "name": style, "clip": c, "vertical": c in VERTICAL,
                        "studio": recipe[0] if recipe else "", "how": (
                            f"Studio recipe: Create → Browse recipes → {recipe[1]}" if recipe
                            else "Studio style library: this look, ready to reuse")})
    for name, vid, _ in niche["references"]:
        if len(out) >= 3:
            break
        out.append({"id": "clone:" + name, "name": f"Like {name}", "still": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    "studio": "", "clone": name, "clone_url": f"https://www.youtube.com/watch?v={vid}",
                    "how": f"Studio style library: paste three {name} videos and Studio learns the look"})
    return out[:3]


def angles(niche: dict) -> list[dict]:
    return [{"id": f"a{i}", "name": name, "title": title} for i, (name, title) in enumerate(ANGLES.get(niche["id"], []))]
NICHES_DIR = os.path.join(os.path.dirname(__file__), "niches")


class BadAnswers(ValueError):
    pass


def clean_answers(raw: dict) -> dict:
    """The four answers, validated; loves sorted so the same choices are the same result."""
    raw = raw if isinstance(raw, dict) else {}
    face, hours, budget = str(raw.get("face") or ""), str(raw.get("hours") or ""), str(raw.get("budget") or "")
    loves = raw.get("loves") if isinstance(raw.get("loves"), list) else []
    loves = sorted({str(x) for x in loves if str(x) in LOVES})
    if face not in FACES or hours not in HOURS or budget not in BUDGETS or not 1 <= len(loves) <= 3:
        raise BadAnswers("Answer all four questions (pick one to three things you love watching).")
    return {"face": face, "hours": hours, "loves": loves, "budget": budget}


# ---- money -------------------------------------------------------------------------------------------------

def _template_rpm(template: str) -> tuple[float, float]:
    """The format template's rpm_range ("$4 - $12") as numbers."""
    try:
        with open(os.path.join(NICHES_DIR, template + ".json"), encoding="utf-8") as fh:
            text = json.load(fh).get("rpm_range") or ""
        lo, hi = (float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)[:2])
        return lo, hi
    except (OSError, ValueError, TypeError):
        return 2.0, 8.0


def rpm_range(niche: dict) -> tuple[float, float]:
    return tuple(niche["rpm"]) if niche.get("rpm") else _template_rpm(niche["template"])


def _round_money(x: float) -> int:
    if x < 100:
        return int(round(x / 5) * 5)
    if x < 1000:
        return int(round(x / 10) * 10)
    return int(round(x / 100) * 100)


def monthly_earnings(channel: dict, rpm: tuple[float, float]) -> tuple[int, int] | None:
    """What a channel plausibly earns a month from ads, if it's in the Partner Program: a range."""
    views = float(channel.get("recent_avg_views") or 0)
    per_month = float(channel.get("uploads_per_month") or 0) or float(channel.get("videos_last_14d") or 0) * 2.14
    per_month = min(per_month, 30.0)
    if views <= 0 or per_month <= 0:
        return None
    monthly_views = views * per_month
    return _round_money(monthly_views * rpm[0] / 1000), _round_money(monthly_views * rpm[1] / 1000)


def _days_ago(stamp: str) -> float | None:
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400


def videos_before_breakout(channel: dict) -> int | None:
    """About how many videos the channel made up to and including its first breakout (its earliest video with at
    least half its best views, and 50K or more). None when the catalogue doesn't hold enough history to tell."""
    videos = [v for v in (channel.get("popular_videos") or []) + (channel.get("recent_videos") or [])
              if isinstance(v, dict)]
    top = max((int(v.get("view_count") or 0) for v in videos), default=0)
    started = channel.get("days_since_start")
    per_month = float(channel.get("uploads_per_month") or 0)
    count = int(channel.get("video_count") or 0)
    if top < 50_000 or not started or per_month <= 0 or count <= 0:
        return None
    hits = [(_days_ago(v.get("published_at") or ""), v) for v in videos
            if int(v.get("view_count") or 0) >= max(50_000, top / 2)]
    ages = [age for age, _ in hits if age is not None]
    if not ages:
        return None
    first = max(ages)                                       # the oldest qualifying video
    made = per_month * max(0.0, float(started) - first) / 30.0
    return max(1, min(count, int(round(made)) + 1))


# ---- proof channels ----------------------------------------------------------------------------------------

_proof_cache: dict = {}
_proof_lock = threading.Lock()
AVATAR_SQL = "quality_status='screened' AND avatar_confidence IN ('likely','disclosed','reference','possible')"


def _query_channels(niche: dict, *, strict: bool, limit: int = 12) -> list[dict]:
    """Small, recent channels with big views in this niche (strict), or a looser bar when strict finds too few."""
    clauses = ["active = 1"]
    params: list = []
    if strict:          # niche_store.py's performance-qualified bar
        clauses += ["subscriber_count BETWEEN 1000 AND 300000", "recent_avg_views >= 20000",
                    "COALESCE(videos_last_14d, 0) > 0", "video_count BETWEEN 3 AND 150",
                    "(days_since_start IS NULL OR days_since_start <= 730)"]
    else:
        clauses += ["subscriber_count BETWEEN 500 AND 500000", "recent_avg_views >= 10000",
                    "video_count BETWEEN 2 AND 250", "(days_since_start IS NULL OR days_since_start <= 1095)"]
    if niche.get("presenter"):
        clauses.append(AVATAR_SQL)
    else:               # the quiz is for faceless channels: a screened on-camera presenter isn't proof here
        clauses.append("COALESCE(production_format,'') != 'presenter'")
    words = [w.lower() for w in niche.get("keywords") or []]
    if words:
        likes = []
        for w in words:
            likes.append("(LOWER(COALESCE(source_keyword,'')) LIKE ? OR LOWER(COALESCE(channel_name,'')) LIKE ? "
                         "OR LOWER(COALESCE(recent_videos_json,'')) LIKE ?)")
            params += [f"%{w}%"] * 3
        clauses.append("(" + " OR ".join(likes) + ")")
    elif not niche.get("presenter"):
        return []
    sql = f"SELECT * FROM niche_channels WHERE {' AND '.join(clauses)} ORDER BY recent_avg_views DESC LIMIT ?"
    with db._conn() as conn:
        rows = conn.execute(db._q(sql), tuple(params + [limit])).fetchall()
    return [db._niche_row_to_hit(dict(r)) for r in rows]


def _proof(hit: dict, rpm: tuple[float, float]) -> dict:
    recent = [v for v in (hit.get("recent_videos") or []) if isinstance(v, dict)][:4]
    days = hit.get("days_since_start")
    return {
        "name": hit.get("channel_name") or "",
        "url": hit.get("channel_url") or "",
        "avatar": hit.get("avatar_url") or "",
        "subscribers": int(hit.get("subscriber_count") or 0),
        "videos": int(hit.get("video_count") or 0),
        "months": max(1, int(round(float(days) / 30.4))) if days else None,
        "recent_avg_views": int(hit.get("recent_avg_views") or 0),
        "uploads_per_month": round(float(hit.get("uploads_per_month") or 0), 1),
        "recent": [{"title": v.get("title") or "", "url": v.get("url") or "", "thumbnail": v.get("thumbnail") or "",
                    "views": int(v.get("view_count") or 0), "seconds": int(v.get("duration_sec") or 0),
                    "published_at": v.get("published_at") or ""} for v in recent],
        "earnings": monthly_earnings(hit, rpm),
        "breakout_after": videos_before_breakout(hit),
    }


def proof_channels(niche: dict, *, now: float | None = None, want: int = 3) -> list[dict]:
    """Up to `want` real proof channels for a niche, cached until the next hunt."""
    bucket = int((now or time.time()) // BUCKET_S)
    key = (db.IS_PG, getattr(db, "DB_PATH", ""), niche["id"], bucket)
    with _proof_lock:
        if key in _proof_cache:
            return _proof_cache[key]
    try:
        hits = _query_channels(niche, strict=True)
        if len(hits) < 2:
            seen = {h["channel_id"] for h in hits}
            hits += [h for h in _query_channels(niche, strict=False) if h["channel_id"] not in seen]
    except Exception as e:                                          # noqa: BLE001 - no proof beats no result
        print(f"[money-niche] proof query for {niche['id']} failed: {e}")
        hits = []
    # smallest audience for the biggest recent views first: the "anyone could start this" signal
    hits.sort(key=lambda h: (-(float(h.get("recent_avg_views") or 0) / max(1000, int(h.get("subscriber_count") or 0))),
                             -float(h.get("recent_avg_views") or 0)))
    rpm = rpm_range(niche)
    proofs = [_proof(h, rpm) for h in hits[:want] if h.get("channel_name") and h.get("channel_url")]
    with _proof_lock:
        if len(_proof_cache) > 200:
            _proof_cache.clear()
        _proof_cache[key] = proofs
    return proofs


def references(niche: dict) -> list[dict]:
    return [{"name": name, "url": f"https://www.youtube.com/watch?v={vid}", "views_label": views,
             "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"} for name, vid, views in niche["references"]]


# ---- scoring -----------------------------------------------------------------------------------------------

def _format_for(niche: dict, answers: dict) -> tuple[str, bool]:
    """(format id, affordable): the best format the budget covers; presenter formats only for someone on camera."""
    level = BUDGETS[answers["budget"]]
    options = [f for f in niche["formats"] if f != "presenter" or answers["face"] == "camera"]
    for f in options:
        if FORMATS[f][1] <= level:
            return f, True
    return min(options, key=lambda f: FORMATS[f][1]), False


def _kind_for(niche: dict, answers: dict) -> str:
    """long or shorts: long when the week holds at least one long video every two weeks."""
    per_week = HOURS[answers["hours"]] / niche["hours"]
    if answers["hours"] == "under3" and niche["shorts"]:
        return "shorts"
    return "long" if per_week >= 0.5 or not niche["shorts"] else "shorts"


def score(niche: dict, answers: dict, *, has_proof: bool = False) -> tuple[float, list[str]]:
    """How well a niche fits the four answers, and the plain reasons why."""
    why = []
    total = 0.0
    loves = answers["loves"]
    for rank, interest in enumerate(niche["interests"]):
        if interest in loves:
            total += 4.0 if rank == 0 else 2.5
            why.append(f"You love {LOVES[interest][0].lower()}")
    fmt, affordable = _format_for(niche, answers)
    if not affordable:
        total -= 4.0
    elif fmt != niche["formats"][0] and not (niche["formats"][0] == "presenter" and answers["face"] != "camera"):
        total -= 0.75                                               # works, in a cheaper format than the best one
    else:
        why.append(f"{FORMATS[fmt][0]} fits your budget")
    face_fit = niche["face"][answers["face"]]
    total += face_fit
    if face_fit >= 2:
        why.append({"camera": "Being on camera is an edge here", "voice": "Your own voice stands out here",
                    "none": "Works fully faceless with an AI voice"}[answers["face"]])
    per_week = HOURS[answers["hours"]] / niche["hours"]
    kind = _kind_for(niche, answers)
    if kind == "shorts":
        total -= 0.5 if answers["hours"] == "under3" else 1.0
        why.append("Shorts suit the time you have")
    elif per_week >= 1:
        total += 1.0
        why.append("Your hours cover a video a week")
    elif per_week < 0.5:
        total -= 2.5
    if has_proof:
        total += 0.75
    return total, why[:3]


def rhythm(niche: dict, answers: dict, kind: str) -> str:
    hours = HOURS[answers["hours"]]
    if kind == "shorts":
        per_week = max(2, min(7, int(hours / 0.75)))
        return "A Short every day" if per_week >= 7 else f"{per_week} Shorts a week"
    per_week = hours / niche["hours"]
    if per_week >= 3:
        return "3 videos a week"
    if per_week >= 2:
        return "2 videos a week"
    if per_week >= 1:
        return "1 video a week"
    return "1 video every 2 weeks"


def recipe(niche: dict, answers: dict) -> dict:
    fmt, affordable = _format_for(niche, answers)
    kind = _kind_for(niche, answers)
    voice = {"camera": "You, on camera and narrating", "voice": "Your own voice", "none": "An AI narrator voice"}
    return {
        "format": FORMATS[fmt][0] + ("" if affordable else " (needs a bigger tools budget)"),
        "kind": kind,
        "length": "30 to 60 seconds, vertical" if kind == "shorts" else niche["length"],
        "look": niche["look"],
        "pacing": niche["pacing"],
        "voice": voice[answers["face"]],
        "rhythm": rhythm(niche, answers, kind),
    }


def rank(answers: dict, *, proofs=None) -> list[tuple[dict, float, list[str]]]:
    """Every niche scored for these answers, best first (catalogue order breaks ties)."""
    proofs = proofs if proofs is not None else (lambda n: proof_channels(n))
    scored = []
    for order, niche in enumerate(NICHES):
        s, why = score(niche, answers, has_proof=bool(proofs(niche)))
        scored.append((s, -order, niche, why))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [(n, s, why) for s, _, n, why in scored]


def compute(answers: dict, *, proofs=None) -> dict:
    """The whole result for a set of answers (everything; public() decides what a visitor sees)."""
    proofs = proofs if proofs is not None else (lambda n: proof_channels(n))
    top = []
    for niche, s, why in rank(answers, proofs=proofs)[:3]:
        found = proofs(niche)
        rpm = rpm_range(niche)
        kind = _kind_for(niche, answers)
        earned = [p["earnings"] for p in found if p.get("earnings")]
        breakouts = [p["breakout_after"] for p in found if p.get("breakout_after")]
        top.append({
            "id": niche["id"], "name": niche["name"], "pitch": niche["pitch"], "why": why, "score": round(s, 2),
            "proofs": found, "references": [] if len(found) >= 2 else references(niche)[:3 - len(found)],
            "rpm": list(rpm), "shorts_rpm": list(SHORTS_RPM) if kind == "shorts" else None,
            "earnings": [min(e[0] for e in earned), max(e[1] for e in earned)] if earned else None,
            "breakout_after": int(statistics.median(breakouts)) if breakouts else None,
            "recipe": recipe(niche, answers), "studio": niche.get("studio") or "",
            "board": board(niche), "looks": looks(niche), "angles": angles(niche),
        })
    # the Niche Finder's best niches, the ones they'd love first: the "more viral niches" feed
    return {"answers": answers, "niches": top, "topics": topics_for(answers, {n["id"] for n in top}),
            "ypp_note": YPP_NOTE, "estimate_note": ESTIMATE_NOTE}


# ---- the Niche Finder's best niches: every breakout channel sorted into a topic by what its videos are about -----

# Topics beyond the 15 proven niches. (id, label, LOVES ids it suits, words found in channel names and video titles)
EXTRA_TOPICS = [
    ("space", "Space and the universe", ["mysteries", "mind"], ["space", "universe", "black hole", "planet", "galaxy", "nasa", "astronaut", "cosmos"]),
    ("ocean", "Ocean and the deep sea", ["mysteries", "disasters"], ["ocean", "deep sea", "submarine", "underwater", "sea creature"]),
    ("animals", "Animals and wildlife", ["stories", "mysteries"], ["animal", "animals", "wildlife", "predator", "shark", "snake", "lion", "insect"]),
    ("military", "Military and war", ["history", "world"], ["military", "army", "tank", "fighter jet", "navy", "ww2", "wwii", "world war", "soldier", "weapon"]),
    ("aviation", "Planes and aviation", ["disasters", "mysteries"], ["plane", "airplane", "pilot", "aviation", "airport", "airline", "jet"]),
    ("cars", "Cars and engines", ["tips", "money"], ["car", "cars", "engine", "supercar", "motor", "racing"]),
    ("horror", "Horror and scary stories", ["mysteries", "stories"], ["horror", "scary", "creepy", "haunted", "paranormal", "ghost", "nightmare"]),
    ("reddit", "Reddit stories", ["stories"], ["reddit", "aita", "entitled", "askreddit", "tifu"]),
    ("mythology", "Mythology and legends", ["history", "stories"], ["myth", "mythology", "greek gods", "norse", "legend", "zeus", "odin"]),
    ("bible", "Bible stories", ["stories", "mind"], ["bible", "jesus", "biblical", "god's", "prayer", "scripture"]),
    ("sleep", "Sleep and calm", ["mind", "history"], ["sleep", "sleepy", "relaxing", "bedtime", "asmr", "fall asleep"]),
    ("celebrity", "Celebrities and drama", ["stories", "money"], ["celebrity", "hollywood", "actor", "actress", "rapper", "famous"]),
    ("sports", "Sports stories", ["stories"], ["football", "nba", "soccer", "boxing", "ufc", "athlete", "nfl"]),
    ("gaming", "Gaming lore", ["stories"], ["minecraft", "gta", "lore", "zelda", "pokemon", "roblox", "fortnite", "elden ring"]),
    ("food", "Food and old recipes", ["tips", "history"], ["food", "recipe", "recipes", "cooking", "kitchen", "chef", "meal"]),
    ("health", "Health and the body", ["mind", "tips"], ["body", "health", "doctor", "brain", "diet", "disease", "medical"]),
    ("science", "Science explained", ["mind", "disasters"], ["science", "physics", "chemistry", "experiment", "how does", "scientists"]),
    ("luxury", "Luxury and the super-rich", ["money"], ["luxury", "rich", "richest", "mansion", "yacht", "expensive"]),
    ("tech", "Tech and AI", ["money", "mind"], ["chatgpt", "iphone", "tech", "apple", "robot", "artificial intelligence", "gadget"]),
    ("survival", "Survival stories", ["disasters", "stories"], ["survival", "survive", "survived", "wilderness", "stranded", "lost at sea"]),
    ("hustle", "Side hustles and making money", ["money", "tips"], ["side hustle", "passive income", "make money", "real estate", "dropshipping", "business idea"]),
    ("rankings", "Top 10 and rankings", ["stories", "mysteries"], ["top 10", "top 5", "ranking", "ranked", "tier list"]),
    ("motivation", "Motivation and mindset", ["mind", "tips"], ["motivation", "discipline", "mindset", "success", "self improvement"]),
    ("conspiracy", "Secrets and conspiracies", ["mysteries", "world"], ["conspiracy", "secret", "secrets", "classified", "cover up", "illuminati"]),
]
TOPICS = ([(n["id"], n["name"], n["interests"], n.get("keywords") or [], n["id"]) for n in NICHES if n.get("keywords")]
          + [(i, label, loves, words, None) for i, label, loves, words in EXTRA_TOPICS])
_topic_cache: dict = {}
LEAD_POOL = 3000


def _words(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9']+", " ", (text or "").lower()) + " "


_TOPIC_WORDS = [(topic, [_words(w) for w in topic[3]]) for topic in TOPICS]      # " black hole " etc., once


def classify(hit: dict) -> tuple | None:
    """The topic whose words appear most in a channel's name and video titles (proven niches win ties)."""
    titles = [v.get("title") or "" for v in (hit.get("recent_videos") or []) + (hit.get("popular_videos") or [])
              if isinstance(v, dict)]
    text = _words(" ".join([hit.get("channel_name") or ""] + titles))
    best, best_hits = None, 0
    for topic, words in _TOPIC_WORDS:
        hits = sum(text.count(w) for w in words)
        if hits > best_hits:
            best, best_hits = topic, hits
    return best


def leaderboard(*, now: float | None = None) -> list[dict]:
    """Every topic with channels breaking out in it: young, small channels with big views, sorted by what they
    plausibly earn. The Niche Finder's best niches, from its own catalogue; cached until the next hunt."""
    bucket = int((now or time.time()) // BUCKET_S)
    key = (db.IS_PG, getattr(db, "DB_PATH", ""), bucket)
    if key in _topic_cache:
        return _topic_cache[key]
    try:
        with db._conn() as conn:
            rows = conn.execute(db._q("""SELECT * FROM niche_channels WHERE active=1
                AND subscriber_count BETWEEN 1000 AND 500000 AND recent_avg_views>=20000
                AND video_count BETWEEN 3 AND 200 AND (days_since_start IS NULL OR days_since_start<=730)
                AND COALESCE(production_format,'')!='presenter'
                ORDER BY est_recent_monthly_revenue_usd DESC LIMIT ?"""), (LEAD_POOL,)).fetchall()
    except Exception as e:                                          # noqa: BLE001 - the page works without it
        print(f"[money-niche] leaderboard failed: {e}")
        rows = []
    groups: dict = {}
    for r in rows:
        hit = db._niche_row_to_hit(dict(r))
        topic = classify(hit)
        if topic:
            groups.setdefault(topic[0], (topic, []))[1].append(hit)
    out = []
    for tid, (topic, hits) in groups.items():
        if len(hits) < 2:
            continue
        niche = BY_ID.get(topic[4] or "")
        rpm = rpm_range(niche) if niche else (2.0, 8.0)             # the Niche Finder's own range when unknown
        proofs = [_proof(h, rpm) for h in hits]
        proofs.sort(key=lambda p: -((p["earnings"] or (0, 0))[1]))
        earned = sorted(p["earnings"] for p in proofs if p.get("earnings"))
        ages = [p["months"] for p in proofs if p.get("months")]
        out.append({"id": tid, "label": topic[1], "niche_id": topic[4], "loves": topic[2], "count": len(hits),
                    "views": int(statistics.median(p["recent_avg_views"] for p in proofs)),
                    "youngest_months": min(ages) if ages else None,
                    "earnings": list(earned[len(earned) // 2]) if earned else None,     # a typical channel's range
                    "top_earnings": list(earned[-1]) if earned else None,
                    "rpm": list(rpm), "channels": proofs[:4]})
    out.sort(key=lambda t: (-(t["earnings"] or [0, 0])[1] * min(t["count"], 5), -t["views"]))
    if len(_topic_cache) > 20:
        _topic_cache.clear()
    _topic_cache[key] = out
    return out


def topics_for(answers: dict, exclude: set) -> list[dict]:
    """The leaderboard re-ordered for someone: topics they'd love first, then the money."""
    loves = set(answers.get("loves") or [])
    ranked = [t for t in leaderboard() if t["id"] not in exclude and t.get("niche_id") not in exclude]
    return sorted(ranked, key=lambda t: (-len(loves & set(t["loves"])),))[:20]


# ---- storage, caching and what a visitor may see -----------------------------------------------------------

_ready: set = set()
_ready_lock = threading.Lock()


def initialize() -> None:
    key = (db.IS_PG, db.DATABASE_URL if db.IS_PG else str(db.DB_PATH))
    if key in _ready:
        return
    with _ready_lock:
        if key in _ready:
            return
        with db._conn() as conn:
            db.schema_lock(conn, "money_niche")                  # two containers boot together: one at a time
            conn.execute("""CREATE TABLE IF NOT EXISTS money_niche_results (
                id TEXT PRIMARY KEY, sig TEXT NOT NULL, answers_json TEXT NOT NULL, result_json TEXT NOT NULL,
                created_at DOUBLE PRECISION NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_money_niche_sig ON money_niche_results (sig)")
            conn.execute("""CREATE TABLE IF NOT EXISTS money_niche_titles (
                niche_id TEXT NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL, titles_json TEXT NOT NULL,
                PRIMARY KEY (niche_id, kind, day))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS money_niche_calls (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, niche_id TEXT NOT NULL, status TEXT NOT NULL,
                tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
                cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0, created_at DOUBLE PRECISION NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_money_niche_calls ON money_niche_calls (created_at, user_id)")
            db._ensure_column(conn.cursor(), "users", "money_niche_json", "TEXT DEFAULT ''")
        _ready.add(key)


def signature(answers: dict, now: float | None = None) -> str:
    bucket = int((now or time.time()) // BUCKET_S)
    return hashlib.sha256((json.dumps(answers, sort_keys=True) + f"|{bucket}").encode()).hexdigest()[:32]


def take_quiz(raw: dict, *, now: float | None = None, proofs=None) -> dict:
    """Store (or reuse) the result for these answers: {id, result}. The same answers within 12 hours are free."""
    answers = clean_answers(raw)
    initialize()
    sig = signature(answers, now)
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT id, result_json FROM money_niche_results WHERE sig=? ORDER BY created_at LIMIT 1"),
                           (sig,)).fetchone()
    if row:
        return {"id": row["id"], "result": json.loads(row["result_json"]), "cached": True}
    result = compute(answers, proofs=proofs)
    rid = secrets.token_urlsafe(9)
    with db._conn() as conn:
        conn.execute(db._q("INSERT INTO money_niche_results (id, sig, answers_json, result_json, created_at) "
                           "VALUES (?,?,?,?,?)"), (rid, sig, json.dumps(answers), json.dumps(result), now or time.time()))
    return {"id": rid, "result": result, "cached": False}


def load(rid: str) -> dict | None:
    if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,24}", rid):
        return None
    initialize()
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT result_json FROM money_niche_results WHERE id=?"), (rid,)).fetchone()
    return json.loads(row["result_json"]) if row else None


def teaser(rid: str, result: dict) -> dict:
    """What anyone sees before signing in: niche #1 by name and ONE proof channel. No earnings, no titles."""
    first = result["niches"][0]
    proof = None
    if first["proofs"]:
        p = first["proofs"][0]
        proof = {"kind": "fresh", "name": p["name"], "url": p["url"], "avatar": p["avatar"], "months": p["months"],
                 "recent_avg_views": p["recent_avg_views"], "subscribers": p["subscribers"],
                 "thumbnail": (p["recent"][0]["thumbnail"] if p["recent"] else ""),
                 "recent_count": len(p["recent"]) or None}
    elif first["references"]:
        r = first["references"][0]
        proof = {"kind": "established", "name": r["name"], "url": r["url"], "views_label": r["views_label"],
                 "thumbnail": r["thumbnail"]}
    if proof and proof["kind"] == "fresh":                   # the views chart: the same public counts, no money
        proof["recent"] = [{k: v[k] for k in ("title", "url", "thumbnail", "views", "seconds", "published_at")}
                           for v in first["proofs"][0]["recent"]]
        proof["videos"] = first["proofs"][0]["videos"]
    # a peek at the other viral niches: names and one video each (public numbers, no money)
    peek = []
    for n in result["niches"][1:]:
        v = next((v for p in n["proofs"] for v in p["recent"] if v.get("thumbnail")), None)
        if v:
            peek.append({"name": n["name"], "thumbnail": v["thumbnail"], "views": v["views"]})
    for t in result.get("topics", []):
        v = next((v for p in t["channels"] for v in p["recent"] if v.get("thumbnail")), None)
        if v:
            peek.append({"name": t["label"], "thumbnail": v["thumbnail"], "views": v["views"]})
    return {"id": rid, "gated": True, "niche": {"id": first["id"], "name": first["name"], "pitch": first["pitch"]},
            "proof": proof, "more": len(result["niches"]) - 1,
            "viral_count": len(result["niches"]) - 1 + len(result.get("topics", [])),
            "peek": peek[:8], "board": first.get("board") or board(BY_ID[first["id"]]), "why": first.get("why", [])}


def public(rid: str, result: dict, user: dict | None, *, titles=None) -> dict:
    """The teaser for a visitor; the full result for anyone signed in (with five titles for the top niche)."""
    if not user:
        return teaser(rid, result)
    first = result["niches"][0]
    chosen = titles if titles is not None else titles_for(BY_ID[first["id"]], first["recipe"]["kind"], user)
    return {"id": rid, "gated": False, "teaser": teaser(rid, result), **result,
            "titles": chosen["titles"], "titles_source": chosen["source"]}


# ---- five titles for the top niche (the one paid call) -----------------------------------------------------

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def model() -> str:
    return (os.getenv("MONEY_NICHE_MODEL") or os.getenv("GEMINI_STYLE_MODEL") or "gemini-3.8-flash").strip()


def _key() -> str:
    return (os.getenv("GEMINI_API_KEY") or "").strip()


def cost(tokens_in: int, tokens_out: int) -> float:
    return (tokens_in * _env_float("MONEY_NICHE_PRICE_IN", 0.75)
            + tokens_out * _env_float("MONEY_NICHE_PRICE_OUT", 3.75)) / 1e6


def _today(now: float | None = None) -> str:
    return datetime.fromtimestamp(now or time.time(), timezone.utc).strftime("%Y-%m-%d")


def _reserve_call(user: dict, niche_id: str, now: float) -> str | None:
    """Count this call against the caps before making it: None when the account or the day is out of calls."""
    per_user = int(_env_float("MONEY_NICHE_USER_DAILY", 3))
    daily_usd = _env_float("MONEY_NICHE_DAILY_USD", 1.0)
    with db._conn() as conn:
        if not db.IS_PG:
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute("SELECT pg_advisory_xact_lock(726346121)")
        mine = conn.execute(db._q("SELECT COUNT(*) AS n FROM money_niche_calls WHERE user_id=? AND created_at>?"),
                            (int(user["id"]), now - 86400)).fetchone()["n"]
        spent = conn.execute(db._q("SELECT COALESCE(SUM(cost_usd),0) AS s FROM money_niche_calls WHERE created_at>?"),
                             (now - 86400,)).fetchone()["s"]
        if mine >= per_user or float(spent or 0) >= daily_usd:
            return None
        call = "mn_" + secrets.token_hex(8)
        # a running call is booked at a conservative cost until its real usage is known
        conn.execute(db._q("INSERT INTO money_niche_calls (id, user_id, niche_id, status, cost_usd, created_at) "
                           "VALUES (?,?,?,'running',?,?)"), (call, int(user["id"]), niche_id, cost(4000, 2000), now))
    return call


def _ask_model(niche: dict, kind: str, *, post=None) -> tuple[list[str], int, int]:
    post = post or requests.post
    examples = [v["title"] for p in proof_channels(niche) for v in p["recent"] if v.get("title")][:8]
    prompt = (f"Write 5 new YouTube video titles for a faceless channel in the niche \"{niche['name']}\" "
              f"({niche['pitch']}). Format: {'YouTube Shorts' if kind == 'shorts' else 'long videos'}. "
              "Each title under 70 characters, specific and curiosity-driven, never clickbait that lies, no emojis, "
              "no ALL CAPS, not a copy of any existing title.\n"
              + ("Titles that are working for small channels in this niche right now (for tone only):\n- "
                 + "\n- ".join(examples) + "\n" if examples else "")
              + 'Return JSON: {"titles": ["...", "...", "...", "...", "..."]}')
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "thinkingConfig": {"thinkingLevel": "low"}}}
    res = post(f"{GEMINI_BASE}/models/{model()}:generateContent", headers={"x-goog-api-key": _key()},
               json=body, timeout=(10, 25))
    if res.status_code != 200:
        raise RuntimeError(f"Gemini HTTP {res.status_code}")
    data = res.json()
    usage = data.get("usageMetadata") or {}
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text)
    titles = [re.sub(r"\s+", " ", str(t)).strip() for t in (json.loads(text).get("titles") or [])]
    titles = [t for t in titles if 8 <= len(t) <= 100][:5]
    if len(titles) < 5:
        raise ValueError("fewer than five usable titles")
    tokens_out = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
    return titles, int(usage.get("promptTokenCount") or 0), tokens_out


def titles_for(niche: dict, kind: str, user: dict, *, now: float | None = None, post=None) -> dict:
    """Five titles for a niche: today's shared fresh set, a new one within the caps, or the niche's written ones."""
    initialize()
    now = now or time.time()
    day = _today(now)
    fallback = {"titles": list(niche["titles"]), "source": "catalogue"}
    with db._conn() as conn:
        row = conn.execute(db._q("SELECT titles_json FROM money_niche_titles WHERE niche_id=? AND kind=? AND day=?"),
                           (niche["id"], kind, day)).fetchone()
    if row:
        return {"titles": json.loads(row["titles_json"]), "source": "model"}
    if not _key():
        return fallback
    call = _reserve_call(user, niche["id"], now)
    if not call:
        return fallback
    try:
        titles, tin, tout = _ask_model(niche, kind, post=post)
        status, spent = "done", cost(tin, tout)
    except Exception as e:                                          # noqa: BLE001 - the written titles still work
        print(f"[money-niche] titles for {niche['id']} failed: {e}")
        titles, tin, tout, status, spent = None, 0, 0, "failed", 0.0
    with db._conn() as conn:
        conn.execute(db._q("UPDATE money_niche_calls SET status=?, tokens_in=?, tokens_out=?, cost_usd=? WHERE id=?"),
                     (status, tin, tout, spent, call))
        if titles:
            conn.execute(db._q("INSERT INTO money_niche_titles (niche_id, kind, day, titles_json) VALUES (?,?,?,?) "
                               "ON CONFLICT(niche_id, kind, day) DO NOTHING"),
                         (niche["id"], kind, day, json.dumps(titles)))
    return {"titles": titles, "source": "model"} if titles else fallback


# ---- the chosen niche, carried into onboarding -------------------------------------------------------------

def choose(user: dict, rid: str, niche_id: str, angle_id: str = "", look_id: str = "", title: str = "") -> dict:
    """Remember the niche (and the angle, look and first title) a signed-in person picked, so the app opens on their
    recipe. Only a niche from their own result."""
    result = load(rid)
    if not result:
        raise LookupError("That result has expired. Take the quiz again.")
    niche = next((n for n in result["niches"] if n["id"] == niche_id), None)
    if not niche and niche_id in BY_ID and any(t.get("niche_id") == niche_id for t in result.get("topics", [])):
        niche = {"id": niche_id, "name": BY_ID[niche_id]["name"]}            # a proven niche from the feed
    if not niche:
        raise LookupError("Pick one of the niches in your result.")
    niche.setdefault("proofs", [])
    niche.setdefault("recipe", {"kind": "long", "format": FORMATS[BY_ID[niche_id]["formats"][0]][0]})
    angle = next((a for a in angles(BY_ID[niche_id]) if a["id"] == angle_id), None)
    look = next((x for x in looks(BY_ID[niche_id]) if x["id"] == look_id), None)
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:120] or (angle["title"] if angle else "")
    clone = (look or {}).get("clone") or ""
    chosen = {"id": niche["id"], "name": niche["name"],
              "studio": (look["studio"] if look else niche.get("studio")) or "",
              "kind": niche["recipe"]["kind"], "format": niche["recipe"]["format"], "result": rid,
              "angle": angle["name"] if angle else "", "look": look["name"] if look else "", "title": title,
              "proof": clone or (niche["proofs"][0]["name"] if niche["proofs"] else ""),
              "proof_url": (look or {}).get("clone_url") or (niche["proofs"][0]["url"] if niche["proofs"] else ""),
              "at": int(time.time())}
    store_choice(user, chosen)
    # TODO(owner decision): offer a free first hosted video here once its cost per video is known. Until then the
    # chosen niche is carried to sign-up and plans (/app#local-studio) and Studio pre-selects it.
    return chosen


def store_choice(user: dict, chosen: dict) -> None:
    initialize()
    with db._conn() as conn:
        conn.execute(db._q("UPDATE users SET money_niche_json=? WHERE id=?"), (json.dumps(chosen), int(user["id"])))


def choice_for(niche_id: str, *, source: str, angle_id: str = "", look_id: str = "", title: str = "",
               kind: str = "long", fmt: str = "") -> dict:
    """The stored pick for any niche (the channel check's door, where there's no quiz result)."""
    niche = BY_ID[niche_id]
    angle = next((a for a in angles(niche) if a["id"] == angle_id), None)
    look = next((x for x in looks(niche) if x["id"] == look_id), None)
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:120] or (angle["title"] if angle else "")
    return {"id": niche_id, "name": niche["name"], "studio": (look or {}).get("studio") or niche.get("studio") or "",
            "kind": kind, "format": fmt or FORMATS[niche["formats"][0]][0], "result": source,
            "angle": angle["name"] if angle else "", "look": look["name"] if look else "", "title": title,
            "proof": (look or {}).get("clone") or "", "proof_url": (look or {}).get("clone_url") or "",
            "at": int(time.time())}


def chosen_of(user: dict) -> dict | None:
    try:
        value = json.loads(user.get("money_niche_json") or "")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("id") in BY_ID else None


# ---- the Niche Finder's newest breakouts, for the leads email ----------------------------------------------

def breakouts(niche_id: str | None, *, days: int = 14, limit: int = 3, now: float | None = None) -> list[dict]:
    """Channels the Niche Finder added in the last `days` that pass the performance bar, in one niche or any."""
    now = now or time.time()
    niche = BY_ID.get(niche_id or "")
    clauses = ["active = 1", "first_seen_at >= ?", "subscriber_count BETWEEN 1000 AND 300000",
               "recent_avg_views >= 20000", "COALESCE(videos_last_14d, 0) > 0", "video_count BETWEEN 3 AND 150"]
    params: list = [now - days * 86400]
    if niche and niche.get("presenter"):
        clauses.append(AVATAR_SQL)
    else:
        clauses.append("COALESCE(production_format,'') != 'presenter'")
    if niche and niche.get("keywords"):
        likes = []
        for w in niche["keywords"]:
            likes.append("(LOWER(COALESCE(source_keyword,'')) LIKE ? OR LOWER(COALESCE(channel_name,'')) LIKE ? "
                         "OR LOWER(COALESCE(recent_videos_json,'')) LIKE ?)")
            params += [f"%{w.lower()}%"] * 3
        clauses.append("(" + " OR ".join(likes) + ")")
    sql = f"SELECT * FROM niche_channels WHERE {' AND '.join(clauses)} ORDER BY recent_avg_views DESC LIMIT ?"
    with db._conn() as conn:
        rows = conn.execute(db._q(sql), tuple(params + [limit])).fetchall()
    rpm = rpm_range(niche) if niche else (2.0, 8.0)
    return [_proof(db._niche_row_to_hit(dict(r)), rpm) for r in rows]


_pulse: dict = {}


def pulse(*, now: float | None = None) -> dict:
    """The live strip on the first screen: how many small channels the Niche Finder caught breaking out this week,
    and a few of them. Cached for 10 minutes."""
    now = now or time.time()
    key = (db.IS_PG, getattr(db, "DB_PATH", ""))
    hit = _pulse.get(key)
    if hit and now - hit[0] < 600:
        return hit[1]
    try:
        with db._conn() as conn:
            n = conn.execute(db._q("""SELECT COUNT(*) AS n FROM niche_channels WHERE active=1 AND first_seen_at>=?
                AND subscriber_count BETWEEN 1000 AND 300000 AND recent_avg_views>=20000
                AND COALESCE(videos_last_14d,0)>0 AND COALESCE(production_format,'')!='presenter'"""),
                (now - 7 * 86400,)).fetchone()["n"]
        channels = breakouts(None, days=7, limit=10, now=now)
    except Exception as e:                                          # noqa: BLE001 - the page works without it
        print(f"[money-niche] pulse failed: {e}")
        n, channels = 0, []
    out = {"week": int(n or 0), "channels": [
        {"name": c["name"], "url": c["url"], "avatar": c["avatar"], "subscribers": c["subscribers"],
         "months": c["months"], "recent_avg_views": c["recent_avg_views"],
         "thumbnail": c["recent"][0]["thumbnail"] if c["recent"] else ""} for c in channels],
        # a home-feed of their latest videos, newest channels first
        "videos": [{"title": v["title"], "url": v["url"], "thumbnail": v["thumbnail"], "views": v["views"],
                    "seconds": v["seconds"], "published_at": v["published_at"], "channel": c["name"],
                    "channel_url": c["url"], "avatar": c["avatar"], "subscribers": c["subscribers"],
                    "months": c["months"]}
                   for c in channels for v in c["recent"][:2] if v.get("thumbnail")][:12]}
    _pulse[key] = (now, out)
    return out
