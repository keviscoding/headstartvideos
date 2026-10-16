"""ChannelRecipe's public recipe vocabulary and owned conceptual card artwork.

This is a presentation boundary. Internal style identities, creative defaults,
saved kits and user names pass through untouched; no engine state is changed.
"""
from copy import deepcopy
from pathlib import Path
import re


ART_ROOT = Path(__file__).with_name("workspace_art")
ART_BASE = "/cr-workspace/art"

# label, family, short explanation, capability labels. These describe the recipe,
# not a promise that a conceptual thumbnail is a completed generated film.
RECIPES = {
    "3d": ("3D Reconstruction", "Animation", "Directed 3D scenes rebuild what happened: disasters, crashes, battles and mysteries.", ("3D scenes", "Maps", "Narrated stories")),
    "folio": ("Illustrated Explainer", "Animation", "A moving illustrated story with recurring characters and explanatory diagrams.", ("Drawn animation", "Characters", "Diagrams")),
    "stories": ("2D Life Stories", "Animation", "An animated character lives through one decision and where it leads, years later.", ("2D characters", "Storytelling", "Illustrated scenes")),
    "atlas": ("Cartoon History", "Animation", "Bold cartoon history with playful characters, eras and illustrated maps.", ("Cartoon imagery", "Maps", "History")),
    "quest": ("Level-Up Stories", "Animation", "Follow a recurring game-like hero through levels, challenges and progress.", ("3D game world", "Characters", "Progress graphics")),
    "nocturne": ("Dark History 3D", "Documentary", "Night-time 3D reconstructions meet real archive film: war, spies and assassinations.", ("3D scenes", "Archive footage", "Documentary")),
    "documentary": ("Editorial Documentary", "Documentary", "Real footage, maps and source material shaped into an editorial story.", ("Real footage", "Maps", "Editorial graphics")),
    "almanac": ("Retro Archive Doc", "Editorial", "The history of people and things, told with archive film, photos and a printed retro look.", ("Archive film", "Paper graphics", "History")),
    "courtside": ("Sports Rise & Fall", "Documentary", "Real game footage, interviews and the numbers behind an athlete's rise, fall and legacy.", ("Sports footage", "Interviews", "Statistics")),
    "deep": ("Military & Tech Mysteries", "Documentary", "Submarines, spy missions and lost aircraft explained with footage, cutaways and route graphics.", ("Technical graphics", "Archive footage", "Maps")),
    "dossier": ("True Crime Case Files", "Documentary", "True crime and internet mysteries built around evidence, documents and investigative graphics.", ("Evidence graphics", "Documents", "Real footage")),
    "hacker": ("Hacker Stories", "Data & graphics", "Cyber heists and hacker stories told with neon network graphics, messages and screens.", ("Network graphics", "Messages", "Cyber stories")),
    "history": ("Painted History", "Animation", "Historical stories unfold through moving paintings and parchment-style maps.", ("Painted imagery", "Historical maps", "Narration")),
    "horizon": ("Charts & Numbers", "Data & graphics", "Big questions answered with real numbers: animated charts, counters, documents and footage.", ("Sourced data", "Charts", "Real footage")),
    "hustle": ("Side Hustle Explainer", "Editorial", "Money and business explainers with comparisons, counters and big bold captions.", ("Business graphics", "Mixed media", "Bold captions")),
    "jung": ("Dark Psychology", "Editorial", "Calm narrated essays on psychology, philosophy and the mind, with moody imagery and graphics.", ("Mixed media", "Motion graphics", "Calm narration")),
    "kinetic": ("Kinetic Text Essay", "Data & graphics", "Animated words, chalk diagrams and source cards carry an expressive essay.", ("Animated type", "Diagrams", "Source cards")),
    "legend": ("Legend Reveals", "Documentary", "A legend tells their own story in the first person, over real footage and photos.", ("Voice-led profile", "Real imagery", "Chapters")),
    "avatar": ("AI Avatar", "Presenter", "An AI presenter opens the film; an image sequence carries the narration.", ("AI presenter", "Intro appearance", "Image sequences")),
    "avatar-premium": ("AI Avatar Premium", "Presenter", "A returning AI presenter joins visual explanations, motion and comparisons.", ("AI presenter", "Recurring host", "Motion graphics")),
    "mentor": ("AI Mentor", "Presenter", "Presenter-led lessons with visual explanations and graphics around the host.", ("AI instructor", "Lesson graphics", "Animated examples")),
    "modern-homes": ("Home & DIY Tips", "Presenter", "Practical home advice with a presenter, warm paper titles and visual examples.", ("AI presenter", "Home advice", "Visual examples")),
    "news": ("Geopolitics Explained", "Documentary", "Wars, crises and world events explained with relief maps, news footage and sourced quotes.", ("News footage", "Maps", "Sourced quotes")),
    "pulse": ("AI News Briefing", "Presenter", "A recurring presenter joins launch material, charts and product demonstrations.", ("AI presenter", "Product news", "Benchmarks")),
    "pitmaster": ("BBQ & Cooking Tips", "Presenter", "Cooking lessons with an AI host, illustrated rules and an optional product feature.", ("AI instructor", "Cooking diagrams", "Product feature")),
    "workshop": ("Old-Timer Tips", "Presenter", "An old-craftsman AI presenter shares hands-on know-how with engraving-style drawings and real footage.", ("AI instructor", "Paper illustrations", "Real footage")),
    "gaming": ("Gaming Briefing", "Documentary", "Gameplay and trailers meet sourced articles and explanatory graphics.", ("Gameplay", "Gaming news", "Editorial graphics")),
    "grooming": ("Personal Care Guide", "Editorial", "Routine footage, practical steps and clear product explanations.", ("How-to steps", "Routine footage", "Products")),
    "automatic": ("Adaptive Documentary", "Documentary", "Discover or reuse a documentary look for your topic through style learning.", ("Style learning", "Documentary", "Reusable graphics")),
    # Ready-made recipes: styles learned from benchmark channels and tested end to end (data/frontier/recipes)
    "cr-stick-stories": ("Stick Figure Stories", "Animation", "Funny stick-figure stories from history and everyday life, on painted backgrounds with a recurring mascot.", ("Stick figures", "Recurring mascot", "Painted scenes")),
    "cr-handdrawn-finance": ("Hand-Drawn Money", "Animation", "Money explained with whiteboard drawings, a recurring drawn everyman and handwritten notes.", ("Whiteboard drawings", "Recurring character", "Handwritten notes")),
    "cr-paper-finance": ("Paper Economics", "Documentary", "Economics and money over real footage, paper documents and clean graphs, with a paper cut-out host.", ("Real footage", "Paper graphics", "Cut-out host")),
    "cr-business-doc": ("Business Story Doc", "Documentary", "Company rise-and-fall stories over real footage, highlighted articles and logos, with a driving score.", ("Real footage", "Highlighted articles", "Story score")),
    "cr-3d-reconstruction": ("Clean 3D Reconstruction", "Animation", "Clean 3D reconstructions of real events in ivory and signal yellow, with technical overlays and maps.", ("3D scenes", "Technical overlays", "Maps")),
    "cr-true-crime": ("Bodycam True Crime", "Documentary", "Real case footage plays with its own sound between the narration: bodycam, interrogations and maps, under a dark grade.", ("Real case footage", "Original audio", "Maps")),
    "cr-expert-presenter": ("Old-Timer Expert", "Presenter", "An older expert presenter opens the video; raw phone-camera photos of them doing what they say carry the rest.", ("AI presenter", "Phone-camera photos", "Own product")),
    "cr-quiet-essays": ("Quiet Video Essays", "Editorial", "Reflective essays over filtered footage and art, with film grain, the sound of each place and calm music.", ("Filtered footage", "Ambience", "Calm narration")),
    "cr-manga-recaps": ("Manga Recaps", "Animation", "Your own manga, manhwa or webtoon panels, read and retold as a gripping recap.", ("Your own panels", "Recap writing", "No picture costs")),
    "cr-deadly-places": ("Deadly Places Documentary", "Documentary", "Calm investigations of the world's deadliest places and disasters, over aged archival-style pictures, engravings and old maps.", ("Archival-style pictures", "Old maps", "Calm narration")),
}
# Ready-made recipes use the nearest owned artwork until they have their own.
ART_ALIAS = {"cr-stick-stories": "atlas", "cr-handdrawn-finance": "hustle", "cr-paper-finance": "almanac",
             "cr-business-doc": "documentary", "cr-3d-reconstruction": "3d", "cr-true-crime": "dossier",
             "cr-expert-presenter": "workshop", "cr-quiet-essays": "jung", "cr-manga-recaps": "folio",
             "cr-deadly-places": "history"}

READY_MADE_IDS = frozenset(ART_ALIAS)
BUILTIN_IDS = frozenset(RECIPES) - {"automatic", "gaming", "grooming"} - READY_MADE_IDS
ART_IDS = (frozenset(RECIPES) - READY_MADE_IDS) | {"custom"}
_SAFE_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,139}\Z")


def asset(name):
    """Only serve owned SVG artwork. Unknown future IDs use the neutral artwork."""
    if not isinstance(name, str):
        raise ValueError("Invalid recipe artwork name.")
    value = name[:-4] if name.endswith(".svg") else name
    if not _SAFE_NAME.fullmatch(value):
        raise ValueError("Invalid recipe artwork name.")
    value = ART_ALIAS.get(value, value)
    value = value if value in ART_IDS else "custom"
    path = ART_ROOT / (value + ".svg")
    if not path.is_file() or path.resolve().parent != ART_ROOT.resolve():
        raise ValueError("Recipe artwork is unavailable.")
    return path


def _details(channel, style_info):
    if not isinstance(style_info, dict):
        return channel
    # Callers may supply the individual saved style or the engine's style map.
    name = str(channel.get("name") or "")
    value = style_info.get(name)
    return value if isinstance(value, dict) else style_info


def present(channel, style_info=None, *, automatic=False):
    """Return an enriched copy of a catalogue row, retaining its stable identity."""
    if not isinstance(channel, dict):
        raise TypeError("A recipe catalogue entry must be an object.")
    row = deepcopy(channel)
    name = str(row.get("name") or "")
    info = _details(channel, style_info)
    # An explicitly custom recipe must never lose its author's name, even if it
    # happens to use a legacy built-in ID.
    custom = bool(row.get("custom")) or name.startswith("cr_clone_")
    automatic = automatic or name in {"automatic", "auto", "ai-style", "ai_style"}
    recipe_key = "automatic" if automatic else name
    definition = None if custom else RECIPES.get(recipe_key)
    if definition:
        label, family, description, capabilities = definition
        row.update(label=label, description=description, tagline=description,
                   family=family, capabilities=list(capabilities))
        artwork = recipe_key
    else:
        row.setdefault("label", name or "My visual recipe")
        row.setdefault("tagline", row.get("description") or "A reusable visual recipe for your next video.")
        row.setdefault("description", row.get("tagline"))
        reference = (info.get("cr_style_clone") or {}) if isinstance(info.get("cr_style_clone"), dict) else {}
        features = info.get("features") or row.get("features") or {}
        active = features.get("on", []) if isinstance(features, dict) else []
        reference_only = reference.get("visual_mode") == "reference_only"
        family = "Animation" if reference_only else "Presenter" if "avatar" in active else "Animation" if "threed" in active else "My styles"
        row.update(family=family, capabilities=["Learned animation", "Reference-led", "Animation only"] if reference_only else ["My visual style", "Reusable recipe"])
        artwork = "custom"
    reference = (info.get("cr_style_clone") or {}) if isinstance(info.get("cr_style_clone"), dict) else {}
    reference_only = reference.get("visual_mode") == "reference_only"
    if reference_only:
        row.update(family="Animation", capabilities=["Learned animation", "Reference-led", "Animation only"],
                   description="Your learned animation style carries the whole film, using its saved visual language and pacing.",
                   tagline="Your learned animation style carries the whole film, using its saved visual language and pacing.")
    row.update(art_url=ART_BASE + "/" + artwork + ".svg",
               art_label="Recipe artwork", art_is_conceptual=True,
               visual_policy="reference_only" if reference_only else "recipe_defaults",
               visual_policy_label="Reference animation only · no footage, generated images or 3D scenes" if reference_only else "Uses this recipe’s visual sources")
    return row


_DASH = re.compile(r"\s*\u2014\s*")
# Identifiers and prompts the studio sends back to the engine stay exactly as
# written; every other catalogue value is display copy.
_VERBATIM = frozenset({"name", "id", "style", "character"})


def plain_copy(value, key=None):
    """Catalogue copy for display, without the engine's em dashes."""
    if key in _VERBATIM:
        return value
    if isinstance(value, dict):
        return {k: plain_copy(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [plain_copy(v, key) for v in value]
    if isinstance(value, str) and "\u2014" in value:
        return _DASH.sub(", ", value.strip().strip("\u2014").strip())
    return value


def presentationCatalog(channels, style_info=None):
    """Enrich only existing rows: never expose intentionally hidden variants."""
    return [present(channel, style_info) for channel in channels]


presentation_catalog = presentationCatalog
