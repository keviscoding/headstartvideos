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
    "3d": ("Cinematic Reconstruction", "Animation", "Directed 3D scenes bring places, people and events into the story.", ("3D scenes", "Maps", "Narrated stories")),
    "folio": ("Illustrated Explainer", "Animation", "A moving illustrated story with recurring characters and explanatory diagrams.", ("Drawn animation", "Characters", "Diagrams")),
    "stories": ("Character Stories", "Animation", "An illustrated character carries a story through decisions and time.", ("2D characters", "Storytelling", "Illustrated scenes")),
    "atlas": ("Cartoon Chronicle", "Animation", "Bold cartoon history with playful graphics, eras and illustrated maps.", ("Cartoon imagery", "Maps", "History")),
    "quest": ("Level-Up Stories", "Animation", "Follow a recurring game-like hero through levels, challenges and progress.", ("3D game world", "Characters", "Progress graphics")),
    "nocturne": ("Cinematic Archive", "Documentary", "Atmospheric 3D reconstructions meet the real archive behind a story.", ("3D scenes", "Archive footage", "Documentary")),
    "documentary": ("Editorial Documentary", "Documentary", "Real footage, maps and source material shaped into an editorial story.", ("Real footage", "Maps", "Editorial graphics")),
    "almanac": ("Archive Edition", "Editorial", "Archive film, photographs and collector-card graphics in a printed look.", ("Archive film", "Paper graphics", "History")),
    "courtside": ("Sports Documentary", "Documentary", "Real game footage, interviews, photographs and the numbers behind the story.", ("Sports footage", "Interviews", "Statistics")),
    "deep": ("Technical Investigation", "Documentary", "Explore machines and missions through footage, cutaways and route graphics.", ("Technical graphics", "Archive footage", "Maps")),
    "dossier": ("Casefile Documentary", "Documentary", "Build a mystery around evidence, documents and investigative graphics.", ("Evidence graphics", "Documents", "Real footage")),
    "hacker": ("Cyber Investigation", "Data & graphics", "Networks, reconstructed messages and technical graphics tell the story.", ("Network graphics", "Messages", "Cyber stories")),
    "history": ("Painted History", "Animation", "Historical stories unfold through moving paintings and parchment-style maps.", ("Painted imagery", "Historical maps", "Narration")),
    "horizon": ("Data Documentary", "Data & graphics", "Explain big questions with sourced numbers, charts, documents and footage.", ("Sourced data", "Charts", "Real footage")),
    "hustle": ("Business Playbook", "Editorial", "Energetic business explainers with comparisons, counters and bold captions.", ("Business graphics", "Mixed media", "Bold captions")),
    "jung": ("Reflective Essay", "Editorial", "Calm narrated essays with imagery, footage and thoughtful visual graphics.", ("Mixed media", "Motion graphics", "Calm narration")),
    "kinetic": ("Motion Essay", "Data & graphics", "Animated words, chalk diagrams and source cards carry an expressive essay.", ("Animated type", "Diagrams", "Source cards")),
    "legend": ("First-Person Profile", "Documentary", "First-person profiles combine a source clip, adapted narration and real imagery.", ("Voice-led profile", "Real imagery", "Chapters")),
    "avatar": ("Presenter Essentials", "Presenter", "An AI presenter opens the film; an image sequence carries the narration.", ("AI presenter", "Intro appearance", "Image sequences")),
    "avatar-premium": ("Presenter Showcase", "Presenter", "A returning AI presenter joins visual explanations, motion and comparisons.", ("AI presenter", "Recurring host", "Motion graphics")),
    "mentor": ("Studio Instructor", "Presenter", "Presenter-led lessons with visual explanations and graphics around the host.", ("AI instructor", "Lesson graphics", "Animated examples")),
    "modern-homes": ("Everyday Home", "Presenter", "Practical home advice with a presenter, warm paper titles and visual examples.", ("AI presenter", "Home advice", "Visual examples")),
    "news": ("World Briefing", "Documentary", "News footage, explanatory maps and sourced quotes make events understandable.", ("News footage", "Maps", "Sourced quotes")),
    "pulse": ("Technology Briefing", "Presenter", "A recurring presenter joins launch material, charts and product demonstrations.", ("AI presenter", "Product news", "Benchmarks")),
    "pitmaster": ("Kitchen Instructor", "Presenter", "Cooking lessons with an AI host, illustrated rules and an optional product feature.", ("AI instructor", "Cooking diagrams", "Product feature")),
    "workshop": ("Practical Instructor", "Presenter", "Hands-on advice with an AI presenter, paper illustrations and real footage.", ("AI instructor", "Paper illustrations", "Real footage")),
    "gaming": ("Gaming Briefing", "Documentary", "Gameplay and trailers meet sourced articles and explanatory graphics.", ("Gameplay", "Gaming news", "Editorial graphics")),
    "grooming": ("Personal Care Guide", "Editorial", "Routine footage, practical steps and clear product explanations.", ("How-to steps", "Routine footage", "Products")),
    "automatic": ("Adaptive Documentary", "Documentary", "Discover or reuse a documentary look for your topic through style learning.", ("Style learning", "Documentary", "Reusable graphics")),
}

BUILTIN_IDS = frozenset(RECIPES) - {"automatic", "gaming", "grooming"}
ART_IDS = frozenset(RECIPES) | {"custom"}
_SAFE_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,139}\Z")


def asset(name):
    """Only serve owned SVG artwork. Unknown future IDs use the neutral artwork."""
    if not isinstance(name, str):
        raise ValueError("Invalid recipe artwork name.")
    value = name[:-4] if name.endswith(".svg") else name
    if not _SAFE_NAME.fullmatch(value):
        raise ValueError("Invalid recipe artwork name.")
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


def presentationCatalog(channels, style_info=None):
    """Enrich only existing rows: never expose intentionally hidden variants."""
    return [present(channel, style_info) for channel in channels]


presentation_catalog = presentationCatalog
