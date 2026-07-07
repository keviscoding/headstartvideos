"""
Video Factory - Gradio UI
Tabs: Build Video, Voiceover Studio, Thumbnails, Script Studio, Niche Screener, Settings
Run: python app.py
"""

import os
import sys
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass

_persistent_env = Path(__file__).parent / "presets" / ".env"
if _persistent_env.exists():
    from dotenv import load_dotenv
    load_dotenv(_persistent_env, override=True)

import gradio as gr
from core.pipeline import run_pipeline, run_cinematic_pipeline
from core.avatar_pipeline import run_avatar_pipeline
from core.explainer_pipeline import run_explainer_pipeline
from core.video_analyzer import analyze_video
from core.recipes import list_recipes, validate_keys
from core.voiceover_gen import VOICE_CHOICES, STYLE_PRESETS
from core.captions import list_caption_styles
from core import presets
from config import GEMINI_KEY, PEXELS_KEY, HEYGEN_KEY, ANTHROPIC_KEY, YOUTUBE_API_KEY, DOWNSUB_KEY, PIXABAY_KEY, ATLASCLOUD_KEY


# =========================================================================
# BACKGROUND PRESETS for HeyGen avatars
# =========================================================================
BG_PRESETS = {
    "White": {"type": "color", "value": "#FFFFFF"},
    "Dark Studio": {"type": "color", "value": "#1a1a2e"},
    "News Desk Blue": {"type": "color", "value": "#0d253f"},
    "Warm Office": {"type": "color", "value": "#2c1810"},
    "Green Screen": {"type": "color", "value": "#00b140"},
    "Custom Color": None,
    "Custom Image URL": None,
}


# =========================================================================
# BUILD VIDEO TAB
# =========================================================================
def build_video(
    recipe, voiceover_file, script_text, swap_rate, style,
    avatar_id, voice_id, avatar_ratio, use_ai_images,
    niche_profile_json, bg_choice, bg_custom_color, bg_custom_url,
    preset_name, caption_style, caption_accent, caption_position, caption_size,
    progress=gr.Progress(),
):
    recipe_key = recipe.lower().replace(" ", "_").replace("+", "plus").replace("-", "_")
    recipe_map = {
        "b_roll_only": "broll_only",
        "cinematic_b_roll": "broll_cinematic",
        "avatar___illustrations": "avatar_plus_broll",
        "avatar_plus_illustrations": "avatar_plus_broll",
        "animated_explainer": "animated_explainer",
    }
    recipe_key = recipe_map.get(recipe_key, recipe_key)

    if not script_text.strip():
        return None, "Please paste your script.", ""

    niche_profile = None
    if niche_profile_json and niche_profile_json.strip():
        try:
            niche_profile = json.loads(niche_profile_json)
        except json.JSONDecodeError:
            pass

    background = None
    if bg_choice == "Custom Color" and bg_custom_color:
        background = {"type": "color", "value": bg_custom_color}
    elif bg_choice == "Custom Image URL" and bg_custom_url:
        background = {"type": "image", "url": bg_custom_url}
    elif bg_choice in BG_PRESETS and BG_PRESETS[bg_choice]:
        background = BG_PRESETS[bg_choice]

    # Save to preset if name provided
    if preset_name and preset_name.strip():
        cfg = presets.load_config(preset_name)
        cfg.recipe = recipe_key
        cfg.swap_rate = swap_rate.lower()
        cfg.style = style.lower().replace(" ", "_")
        cfg.avatar_id = avatar_id or ""
        cfg.voice_id = voice_id or ""
        cfg.avatar_ratio = avatar_ratio
        cfg.use_ai_images = use_ai_images
        if background:
            cfg.background = background
        presets.save_config(preset_name, cfg)

    logs = []

    def on_progress(msg):
        logs.append(msg)
        progress(len(logs) / 10, desc=msg)

    try:
        if recipe_key == "avatar_plus_broll":
            ok, missing = validate_keys("avatar_plus_broll")
            if not ok:
                return None, f"Missing API keys: {', '.join(missing)}. Add them in Settings tab.", ""
            if not (avatar_id or "").strip() or not (voice_id or "").strip():
                return None, "Please provide Avatar ID and Voice ID for the avatar recipe.", ""

            result = run_avatar_pipeline(
                script=script_text,
                avatar_id=avatar_id.strip(),
                voice_id=voice_id.strip(),
                voiceover_path=voiceover_file if voiceover_file else None,
                swap_rate=swap_rate.lower(),
                style=style.lower().replace(" ", "_"),
                avatar_ratio=avatar_ratio,
                use_ai_images=use_ai_images,
                niche_profile=niche_profile,
                background=background,
                progress_callback=on_progress,
            )

            timing = result["timing"]
            summary = (
                f"Avatar video built successfully!\n\n"
                f"Output: {result['output_path']}\n"
                f"Slots: {len(result['slots'])} "
                f"({result['avatar_slots']} avatar, {result['illustration_slots']} illustration)\n\n"
                f"Timing breakdown:\n"
                + "\n".join(f"  {k}: {v:.1f}s" for k, v in timing.items())
                + f"\n  Total: {sum(timing.values()):.1f}s"
            )
        elif recipe_key == "animated_explainer":
            if not voiceover_file:
                return None, "Please upload a voiceover audio file.", ""

            result = run_explainer_pipeline(
                script=script_text,
                voiceover_path=voiceover_file,
                style_preset=style.lower().replace(" ", "_"),
                niche_profile=niche_profile,
                caption_style=caption_style or "None",
                caption_accent=caption_accent or "#00BFFF",
                caption_font_size=caption_size or "Medium",
                caption_position=caption_position or "Bottom",
                progress_callback=on_progress,
            )

            timing = result["timing"]
            type_counts = result.get("type_counts", {})
            summary = (
                f"Animated explainer built!\n\n"
                f"Output: {result['output_path']}\n"
                f"Concepts: {len(result['slots'])}\n"
                f"Illustrations: {type_counts.get('illustrations', 0)} generated, "
                f"{type_counts.get('placeholders', 0)} placeholders\n"
                f"Mood distribution: {type_counts.get('moods', {})}\n\n"
                f"Timing breakdown:\n"
                + "\n".join(f"  {k}: {v:.1f}s" for k, v in timing.items())
                + f"\n  Total: {sum(timing.values()):.1f}s"
            )

        elif recipe_key == "broll_cinematic":
            if not voiceover_file:
                return None, "Please upload a voiceover audio file.", ""

            result = run_cinematic_pipeline(
                script=script_text,
                voiceover_path=voiceover_file,
                swap_rate=swap_rate.lower(),
                style=style.lower().replace(" ", "_"),
                niche_profile=niche_profile,
                caption_style=caption_style or "None",
                caption_accent=caption_accent or "#00BFFF",
                caption_font_size=caption_size or "Medium",
                caption_position=caption_position or "Bottom",
                progress_callback=on_progress,
            )

            timing = result["timing"]
            type_counts = result.get("type_counts", {})
            summary = (
                f"Cinematic video built!\n\n"
                f"Output: {result['output_path']}\n"
                f"Scenes: {len(result['slots'])}\n"
                f"Asset breakdown: {type_counts}\n\n"
                f"Timing breakdown:\n"
                + "\n".join(f"  {k}: {v:.1f}s" for k, v in timing.items())
                + f"\n  Total: {sum(timing.values()):.1f}s"
            )

        else:
            if not voiceover_file:
                return None, "Please upload a voiceover audio file.", ""

            result = run_pipeline(
                script=script_text,
                voiceover_path=voiceover_file,
                swap_rate=swap_rate.lower(),
                style=style.lower().replace(" ", "_"),
                niche_profile=niche_profile,
                caption_style=caption_style or "None",
                caption_accent=caption_accent or "#00BFFF",
                caption_font_size=caption_size or "Medium",
                caption_position=caption_position or "Bottom",
                progress_callback=on_progress,
            )

            timing = result["timing"]
            summary = (
                f"Video built successfully!\n\n"
                f"Output: {result['output_path']}\n"
                f"Slots: {len(result['slots'])}\n"
                f"Images found: "
                f"{sum(1 for i in result['images'] if i['source'] != 'fallback')}"
                f"/{len(result['images'])}\n\n"
                f"Timing breakdown:\n"
                + "\n".join(f"  {k}: {v:.1f}s" for k, v in timing.items())
                + f"\n  Total: {sum(timing.values()):.1f}s"
            )

        if preset_name and preset_name.strip():
            presets.add_history_entry(preset_name, "video", {
                "path": result["output_path"],
                "recipe": recipe_key,
                "summary": summary[:300],
            })

        return result["output_path"], summary, "\n".join(logs)

    except Exception as e:
        return None, f"Error: {e}", "\n".join(logs)


def on_recipe_change(recipe):
    is_avatar = "Avatar" in recipe
    is_explainer = "Explainer" in recipe
    return (
        gr.Textbox(visible=is_avatar),
        gr.Textbox(visible=is_avatar),
        gr.Slider(visible=is_avatar),
        gr.Checkbox(visible=is_avatar),
        gr.Audio(visible=not is_avatar),
        gr.Dropdown(visible=is_avatar),
        gr.Textbox(visible=is_avatar),
        gr.Textbox(visible=is_avatar),
    )


def on_bg_change(bg_choice):
    return (
        gr.Textbox(visible=bg_choice == "Custom Color"),
        gr.Textbox(visible=bg_choice == "Custom Image URL"),
    )


def load_preset_into_build(preset_name):
    if not preset_name or not presets.preset_exists(preset_name):
        return [gr.update()] * 8

    cfg = presets.load_config(preset_name)
    np = presets.load_niche_profile(preset_name)
    np_json = json.dumps(np, indent=2) if np else ""

    recipe_labels_map = {
        "avatar_plus_broll": "Avatar + Illustrations",
        "broll_cinematic": "Cinematic B-Roll",
        "animated_explainer": "Animated Explainer",
    }
    recipe_label = recipe_labels_map.get(cfg.recipe, "B-Roll Only")
    swap = cfg.swap_rate.capitalize()
    style = cfg.style.replace("_", " ").title() if cfg.style != "auto" else "Auto"

    return (
        recipe_label,
        cfg.avatar_id,
        cfg.voice_id,
        cfg.avatar_ratio,
        cfg.use_ai_images,
        swap,
        style,
        np_json,
    )


# =========================================================================
# NICHE SCREENER TAB
# =========================================================================
def analyze_niche(youtube_url, minutes, save_preset_name, progress=gr.Progress()):
    if not youtube_url.strip():
        return "", "Please enter a YouTube URL."

    if "youtube.com" not in youtube_url and "youtu.be" not in youtube_url:
        return "", "Please enter a valid YouTube URL."

    progress(0.1, desc="Sending video to Gemini for analysis...")

    try:
        profile = analyze_video(youtube_url.strip(), analyze_minutes=minutes)
        progress(0.9, desc="Analysis complete!")

        verdict_parts = [
            f"Niche: {profile.niche_name}",
            f"Recipe: {profile.recipe}",
            f"B-roll type: {profile.broll_type}",
            f"Swap rate: {profile.default_swap_rate}",
            f"Automatable: {profile.automatable_pct}%",
            "",
            "Visual style:",
            f"  Era: {profile.visual_style.get('era', 'N/A')}",
            f"  Tone: {profile.visual_style.get('tone', 'N/A')}",
            f"  Palette: {profile.visual_style.get('palette', 'N/A')}",
            f"  Grain: {profile.visual_style.get('grain', 'N/A')}",
        ]

        if profile.avatar_config:
            verdict_parts.extend([
                "",
                "Avatar detected:",
                f"  Tool: {profile.avatar_config.get('tool', 'unknown')}",
                f"  Avatar ratio: {profile.avatar_config.get('ratio', 'N/A')}",
                f"  Position: {profile.avatar_config.get('position', 'N/A')}",
            ])

        if profile.notes:
            verdict_parts.extend(["", f"Notes: {profile.notes}"])

        verdict = "\n".join(verdict_parts)
        profile_json = profile.to_json()

        if save_preset_name and save_preset_name.strip():
            profile_dict = json.loads(profile_json)
            presets.create_preset(save_preset_name)
            presets.save_niche_profile(save_preset_name, profile_dict)
            cfg = presets.load_config(save_preset_name)
            cfg.recipe = profile.recipe
            cfg.swap_rate = profile.default_swap_rate
            if profile.avatar_config:
                cfg.avatar_ratio = profile.avatar_config.get("ratio", 0.5)
            presets.save_config(save_preset_name, cfg)
            verdict += f"\n\nSaved to preset: {save_preset_name}"

        return profile_json, verdict

    except Exception as e:
        return "", f"Analysis failed: {e}"


# =========================================================================
# THUMBNAILS TAB
# =========================================================================
def save_thumb_preset(preset_name, ref_images, style_prompt):
    if not preset_name or not preset_name.strip():
        return "Please enter a preset name."

    presets.create_preset(preset_name)

    if ref_images:
        for img_path in ref_images:
            presets.save_thumbnail_ref(preset_name, img_path)

    presets.save_thumbnail_config(
        preset_name,
        style_prompt=style_prompt or "",
        model="nano-banana-pro",
    )

    count = len(presets.get_thumbnail_refs(preset_name))
    return f"Saved! {count} reference image(s) stored in preset '{preset_name}'."


def generate_thumb(preset_name, title, num_options, progress=gr.Progress()):
    if not title or not title.strip():
        return [], "Please enter a video title."

    from core.thumbnail_gen import generate_thumbnails, generate_thumbnail_no_refs

    progress(0.1, desc="Preparing thumbnail generation...")

    ref_images = []
    style_prompt = ""

    if preset_name and presets.preset_exists(preset_name):
        ref_images = presets.get_thumbnail_refs(preset_name)
        thumb_cfg = presets.load_thumbnail_config(preset_name)
        style_prompt = thumb_cfg.get("style_prompt", "")

    out_dir = str(Path(__file__).parent / "output" / "thumbnails" / str(int(time.time())))

    progress(0.3, desc=f"Generating {num_options} thumbnail(s)...")

    if ref_images:
        paths = generate_thumbnails(
            title=title.strip(),
            reference_image_paths=ref_images,
            style_prompt=style_prompt,
            num_images=int(num_options),
            output_dir=out_dir,
        )
    else:
        paths = generate_thumbnail_no_refs(
            title=title.strip(),
            style_description=style_prompt,
            output_dir=out_dir,
        )

    progress(1.0, desc="Done!")

    if not paths:
        return [], "Failed to generate thumbnails. Check your Atlas Cloud API key in Settings."

    if preset_name and presets.preset_exists(preset_name):
        presets.add_history_entry(preset_name, "thumbnail", {
            "title": title.strip(),
            "paths": paths,
        })

    return paths, f"Generated {len(paths)} thumbnail(s) in {out_dir}"


# =========================================================================
# SCRIPT STUDIO TAB
# =========================================================================
def import_channel_data(preset_name, data_file, data_paste):
    if not preset_name or not preset_name.strip():
        return "Please enter a preset name first."

    presets.create_preset(preset_name)

    data = None
    if data_file:
        with open(data_file) as f:
            data = json.load(f)
    elif data_paste and data_paste.strip():
        try:
            data = json.loads(data_paste)
        except json.JSONDecodeError:
            lines = [l.strip() for l in data_paste.strip().split("\n") if l.strip()]
            data = {"videos": [{"title": l, "views": 0} for l in lines]}

    if data:
        presets.save_channel_data(preset_name, data)
        vid_count = len(data.get("videos", []))
        return f"Imported {vid_count} videos into preset '{preset_name}'."

    return "No data provided. Upload a JSON file or paste data."


def fetch_yt_channel(
    preset_name, channel_url, num_videos, yt_key, ds_key,
    fetch_transcripts,
    progress=gr.Progress(),
):
    if not channel_url or not channel_url.strip():
        return "", "Please enter a YouTube channel URL."

    api_key = (yt_key or "").strip() or YOUTUBE_API_KEY
    if not api_key:
        return "", "Please add a YouTube API key in Settings or paste it above."

    downsub = (ds_key or "").strip() or DOWNSUB_KEY

    progress(0.1, desc="Fetching channel data from YouTube...")

    from core.channel_data import fetch_channel_data

    try:
        data = fetch_channel_data(
            channel_url=channel_url.strip(),
            yt_api_key=api_key,
            downsub_key=downsub if fetch_transcripts else "",
            max_videos=int(num_videos),
            fetch_transcripts=fetch_transcripts,
            progress_callback=lambda msg: progress(0.5, desc=msg),
        )

        if preset_name and preset_name.strip():
            presets.create_preset(preset_name)
            presets.save_channel_data(preset_name, data)

        progress(1.0, desc="Done!")

        meta = data.get("metadata", {})
        vids = data.get("videos", [])
        transcripts = data.get("transcripts", [])

        summary_lines = [
            f"Channel: {meta.get('channel_name', 'Unknown')}",
            f"Subscribers: {meta.get('subscribers', 0):,}",
            f"Videos fetched: {len(vids)}",
            f"Transcripts fetched: {len(transcripts)}",
            "",
            "Top videos by views:",
        ]
        sorted_vids = sorted(vids, key=lambda v: v.get("views", 0), reverse=True)
        for v in sorted_vids[:10]:
            summary_lines.append(
                f"  [{v.get('views', 0):>10,} views] {v['title'][:60]}"
            )

        if preset_name:
            summary_lines.append(f"\nSaved to preset: {preset_name}")

        return json.dumps(data, indent=2), "\n".join(summary_lines)

    except Exception as e:
        return "", f"Error fetching channel data: {e}"


def run_channel_analysis(preset_name, api_key, progress=gr.Progress()):
    if not api_key or not api_key.strip():
        return "Please add your Claude API key in the Settings tab or paste it above."

    channel_data = presets.load_channel_data(preset_name) if preset_name else None
    if not channel_data:
        return "No channel data found. Import channel data first."

    progress(0.2, desc="Claude is analyzing the channel...")

    try:
        from core.script_gen import analyze_channel
        analysis = analyze_channel(channel_data, api_key.strip())
    except Exception as e:
        return f"Error analyzing channel: {e}"

    if preset_name:
        presets.save_channel_analysis(preset_name, {"text": analysis})
        presets.save_studio_field(preset_name, "analysis", analysis)
        presets.add_history_entry(preset_name, "analysis", {"text": analysis[:500]})

    progress(1.0, desc="Analysis complete!")
    return analysis


def run_generate_ideas(preset_name, api_key, num_ideas, progress=gr.Progress()):
    if not api_key or not api_key.strip():
        return "Please add your Claude API key."

    channel_data = presets.load_channel_data(preset_name) if preset_name else None
    if not channel_data:
        return "No channel data found. Import channel data first."

    analysis_data = presets.load_channel_analysis(preset_name) if preset_name else None
    analysis_text = analysis_data.get("text", "") if analysis_data else ""

    progress(0.2, desc="Claude is generating ideas...")

    try:
        from core.script_gen import generate_ideas
        ideas = generate_ideas(channel_data, api_key.strip(), int(num_ideas), analysis=analysis_text)
    except Exception as e:
        return f"Error generating ideas: {e}"

    if preset_name:
        presets.save_studio_field(preset_name, "ideas", ideas)
        presets.add_history_entry(preset_name, "ideas", {"text": ideas})

    progress(1.0, desc="Ideas generated!")
    return ideas


def run_generate_titles(preset_name, api_key, video_idea, progress=gr.Progress()):
    if not api_key or not api_key.strip():
        return "Please add your Claude API key."
    if not video_idea or not video_idea.strip():
        return "Please enter or select a video idea."

    channel_data = presets.load_channel_data(preset_name) if preset_name else None
    if not channel_data:
        channel_data = {"videos": []}

    progress(0.2, desc="Claude is generating titles...")

    try:
        from core.script_gen import generate_titles
        titles = generate_titles(video_idea.strip(), channel_data, api_key.strip())
    except Exception as e:
        return f"Error generating titles: {e}"

    if preset_name:
        presets.save_studio_field(preset_name, "titles", titles)
        presets.save_studio_field(preset_name, "title_idea", video_idea.strip())
        presets.add_history_entry(preset_name, "titles", {"idea": video_idea.strip(), "text": titles})

    progress(1.0, desc="Titles generated!")
    return titles


def run_generate_script(preset_name, api_key, title, idea, target_min, progress=gr.Progress()):
    if not api_key or not api_key.strip():
        return "Please add your Claude API key."
    if not title or not title.strip():
        return "Please enter a video title."

    channel_data = presets.load_channel_data(preset_name) if preset_name else None
    if not channel_data:
        channel_data = {"videos": []}

    progress(0.1, desc="Claude is writing the script...")

    try:
        from core.script_gen import generate_script
        script = generate_script(
            title.strip(), idea or "", channel_data,
            api_key.strip(), int(target_min),
        )
    except Exception as e:
        return f"Error generating script: {e}"

    if preset_name:
        presets.save_studio_field(preset_name, "script", script)
        presets.save_studio_field(preset_name, "script_title", title.strip())
        if idea:
            presets.save_studio_field(preset_name, "script_idea", idea.strip())
        presets.add_history_entry(preset_name, "script", {"title": title.strip(), "text": script})

    progress(1.0, desc="Script complete!")
    return script


def save_studio_text(preset_name, field_key, text):
    """Save any user-edited text field to the preset."""
    if preset_name and preset_name.strip() and text is not None:
        presets.save_studio_field(preset_name, field_key, text.strip() if text else "")


def load_preset_into_script_studio(preset_name):
    """Load all Script Studio state from preset."""
    if not preset_name or not presets.preset_exists(preset_name):
        return ("", "", "", "", "", "", "", "", "", "")

    studio = presets.load_all_studio(preset_name)
    channel_data = presets.load_channel_data(preset_name)
    analysis_data = presets.load_channel_analysis(preset_name)

    ch_json = json.dumps(channel_data, indent=2) if channel_data else ""
    analysis_text = ""
    if analysis_data:
        analysis_text = analysis_data.get("text", "")
    elif studio.get("analysis"):
        analysis_text = studio["analysis"]

    return (
        ch_json,                              # ch_fetch_json
        analysis_text,                        # ch_analysis_output
        studio.get("ideas", ""),              # ideas_output
        studio.get("selected_idea", ""),      # ideas_selected
        studio.get("title_idea", ""),         # title_idea_input
        studio.get("titles", ""),             # titles_output
        studio.get("selected_title", ""),     # title_selected
        studio.get("script", ""),             # sw_output
        studio.get("script_title", ""),       # sw_title
        studio.get("script_idea", ""),        # sw_idea
    )


# =========================================================================
# VOICEOVER STUDIO TAB
# =========================================================================
def generate_vo(preset_name, script_text, voice, style_preset, custom_notes, progress=gr.Progress()):
    if not script_text or not script_text.strip():
        return None, "Please enter a script."

    progress(0.1, desc="Generating voiceover with Gemini TTS...")

    from core.voiceover_gen import generate_voiceover

    out_dir = str(Path(__file__).parent / "output" / "voiceovers" / str(int(time.time())))

    try:
        wav_path = generate_voiceover(
            script=script_text.strip(),
            voice=voice,
            style_preset=style_preset,
            custom_notes=custom_notes or "",
            output_dir=out_dir,
        )
        progress(1.0, desc="Voiceover generated!")

        if preset_name and preset_name.strip():
            if presets.preset_exists(preset_name):
                presets.add_history_entry(preset_name, "voiceover", {
                    "path": wav_path,
                    "voice": voice,
                    "style": style_preset,
                })

        return wav_path, f"Voiceover saved to: {wav_path}"
    except Exception as e:
        return None, f"Error generating voiceover: {e}"


def on_vo_style_change(style_preset):
    return gr.Textbox(visible=style_preset == "Custom")


# =========================================================================
# SETTINGS TAB
# =========================================================================
def save_api_keys(gemini, pexels, heygen, anthropic_key, yt_key, ds_key, atlas_key):
    import config as cfg

    env_path = Path(__file__).parent / ".env"
    lines = []
    if gemini:
        lines.append(f"GEMINI_KEY={gemini}")
    if pexels:
        lines.append(f"PEXELS_KEY={pexels}")
    if heygen:
        lines.append(f"HEYGEN_KEY={heygen}")
    if anthropic_key:
        lines.append(f"ANTHROPIC_KEY={anthropic_key}")
    if yt_key:
        lines.append(f"YOUTUBE_API_KEY={yt_key}")
    if ds_key:
        lines.append(f"DOWNSUB_KEY={ds_key}")
    if atlas_key:
        lines.append(f"ATLASCLOUD_KEY={atlas_key}")

    env_content = "\n".join(lines) + "\n"

    try:
        with open(env_path, "w") as f:
            f.write(env_content)
    except (OSError, IsADirectoryError):
        pass

    persistent_env = Path(__file__).parent / "presets" / ".env"
    persistent_env.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(persistent_env, "w") as f:
            f.write(env_content)
    except OSError:
        pass

    import os
    os.environ["GEMINI_KEY"] = gemini or ""
    os.environ["PEXELS_KEY"] = pexels or ""
    os.environ["HEYGEN_KEY"] = heygen or ""
    os.environ["ANTHROPIC_KEY"] = anthropic_key or ""
    os.environ["YOUTUBE_API_KEY"] = yt_key or ""
    os.environ["DOWNSUB_KEY"] = ds_key or ""
    os.environ["ATLASCLOUD_KEY"] = atlas_key or ""

    cfg.GEMINI_KEY = gemini or ""
    cfg.PEXELS_KEY = pexels or ""
    cfg.HEYGEN_KEY = heygen or ""
    cfg.ANTHROPIC_KEY = anthropic_key or ""
    cfg.YOUTUBE_API_KEY = yt_key or ""
    cfg.DOWNSUB_KEY = ds_key or ""
    cfg.ATLASCLOUD_KEY = atlas_key or ""

    global GEMINI_KEY, PEXELS_KEY, HEYGEN_KEY, ANTHROPIC_KEY, YOUTUBE_API_KEY, DOWNSUB_KEY, ATLASCLOUD_KEY
    GEMINI_KEY = gemini or ""
    PEXELS_KEY = pexels or ""
    HEYGEN_KEY = heygen or ""
    ANTHROPIC_KEY = anthropic_key or ""
    YOUTUBE_API_KEY = yt_key or ""
    DOWNSUB_KEY = ds_key or ""
    ATLASCLOUD_KEY = atlas_key or ""

    return "API keys saved successfully!"


def test_api_key(key_type, key_value):
    if not key_value or not key_value.strip():
        return f"{key_type}: No key provided"

    try:
        if key_type == "Gemini":
            from google import genai
            client = genai.Client(api_key=key_value)
            client.models.list()
            return "Gemini: Connected successfully"

        elif key_type == "Pexels":
            import httpx
            resp = httpx.get(
                "https://api.pexels.com/v1/search",
                params={"query": "test", "per_page": "1"},
                headers={"Authorization": key_value},
                timeout=10,
            )
            if resp.status_code == 200:
                return "Pexels: Connected successfully"
            return f"Pexels: HTTP {resp.status_code}"

        elif key_type == "HeyGen":
            import httpx
            resp = httpx.get(
                "https://api.heygen.com/v2/voices",
                headers={"X-Api-Key": key_value},
                timeout=15,
            )
            if resp.status_code == 200:
                count = len(resp.json().get("data", {}).get("voices", []))
                return f"HeyGen: Connected ({count} voices available)"
            return f"HeyGen: HTTP {resp.status_code}"

        elif key_type == "Claude":
            import anthropic
            client = anthropic.Anthropic(api_key=key_value)
            models_to_try = ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-sonnet-4-20250514"]
            last_err = None
            for model_name in models_to_try:
                try:
                    resp = client.messages.create(
                        model=model_name,
                        max_tokens=10,
                        messages=[{"role": "user", "content": "Hi"}],
                    )
                    return f"Claude: Connected successfully ({model_name})"
                except anthropic.NotFoundError:
                    last_err = f"model {model_name} not found"
                    continue
            return f"Claude: Key valid but no models available ({last_err})"

        elif key_type == "YouTube":
            import httpx
            resp = httpx.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={"part": "id", "q": "test", "maxResults": "1", "key": key_value},
                timeout=10,
            )
            if resp.status_code == 200:
                return "YouTube: Connected successfully"
            elif resp.status_code == 403:
                return "YouTube: Key valid but quota exceeded or API not enabled"
            return f"YouTube: HTTP {resp.status_code}"

        elif key_type == "AtlasCloud":
            import httpx
            resp = httpx.get(
                "https://api.atlascloud.ai/api/v1/models",
                timeout=10,
            )
            if resp.status_code == 200:
                return "Atlas Cloud: API reachable, key format accepted"
            return f"Atlas Cloud: HTTP {resp.status_code}"

    except Exception as e:
        return f"{key_type}: Error - {str(e)[:100]}"

    return f"{key_type}: Unknown error"


def refresh_preset_choices():
    """Dynamically fetch current presets for dropdowns."""
    return presets.list_presets()


# =========================================================================
# APP LAYOUT
# =========================================================================
with gr.Blocks(title="Video Factory") as app:
    gr.Markdown("# Video Factory\nYour complete YouTube video production toolkit.")

    preset_choices = presets.list_presets()

    recipes = list_recipes()
    recipe_labels = [r["label"] for r in recipes]

    with gr.Tabs():

        # ===== BUILD VIDEO TAB =====
        with gr.Tab("Build Video"):
            with gr.Row():
                build_preset_dd = gr.Dropdown(
                    choices=preset_choices, label="Load Preset",
                    allow_custom_value=True,
                    info="Select a saved preset or type a new name to save",
                )
                build_refresh_btn = gr.Button("↻", size="sm", min_width=40)
            build_refresh_btn.click(
                fn=lambda: gr.Dropdown(choices=refresh_preset_choices()),
                outputs=[build_preset_dd],
            )

            with gr.Row():
                with gr.Column(scale=1):
                    recipe_input = gr.Dropdown(
                        choices=recipe_labels, value=recipe_labels[0],
                        label="Recipe",
                    )

                    voiceover_input = gr.Audio(
                        label="Voiceover Audio", type="filepath", sources=["upload"],
                    )
                    script_input = gr.Textbox(
                        label="Script",
                        placeholder="Paste your narration script here...",
                        lines=10,
                    )

                    avatar_id_input = gr.Textbox(
                        label="HeyGen Avatar ID",
                        placeholder="e.g. Abigail_expressive_2024112501",
                        visible=False,
                    )
                    voice_id_input = gr.Textbox(
                        label="HeyGen Voice ID",
                        placeholder="e.g. f38a635bee7a4d1f9b0a654a31d050d2",
                        visible=False,
                    )
                    avatar_ratio_input = gr.Slider(
                        minimum=0.1, maximum=0.9, value=0.5, step=0.1,
                        label="Avatar Ratio", visible=False,
                    )
                    use_ai_images_input = gr.Checkbox(
                        label="Use AI-generated illustrations",
                        value=True, visible=False,
                    )

                    bg_choice_input = gr.Dropdown(
                        choices=list(BG_PRESETS.keys()),
                        value="White",
                        label="Avatar Background",
                        visible=False,
                    )
                    bg_custom_color = gr.Textbox(
                        label="Custom Background Color (hex)",
                        placeholder="#1a1a2e",
                        visible=False,
                    )
                    bg_custom_url = gr.Textbox(
                        label="Background Image URL",
                        placeholder="https://...",
                        visible=False,
                    )

                    with gr.Row():
                        swap_rate_input = gr.Dropdown(
                            choices=["Fast", "Medium", "Slow"],
                            value="Medium", label="B-Roll Swap Rate",
                        )
                        style_input = gr.Dropdown(
                            choices=["Auto", "Historical BW", "Cinematic Dark",
                                     "Modern Color", "Neutral"],
                            value="Auto", label="Visual Style",
                        )

                    with gr.Row():
                        caption_style_input = gr.Dropdown(
                            choices=list_caption_styles(),
                            value="Clean", label="Caption Style",
                        )
                        caption_accent_input = gr.Textbox(
                            label="Accent Color",
                            value="#00BFFF",
                            placeholder="#00BFFF",
                        )
                    with gr.Row():
                        caption_position_input = gr.Dropdown(
                            choices=["Bottom", "Center", "Top"],
                            value="Bottom", label="Caption Position",
                        )
                        caption_size_input = gr.Dropdown(
                            choices=["Small", "Medium", "Large"],
                            value="Medium", label="Caption Size",
                        )

                    niche_profile_input = gr.Textbox(
                        label="Niche Profile (JSON, optional)",
                        placeholder="Paste from Niche Screener or load a preset...",
                        lines=3,
                    )

                    build_btn = gr.Button("Build Video", variant="primary", size="lg")

                with gr.Column(scale=1):
                    video_output = gr.Video(label="Output Video")
                    summary_output = gr.Textbox(label="Summary", lines=10)
                    log_output = gr.Textbox(label="Build Log", lines=6)

            recipe_input.change(
                fn=on_recipe_change,
                inputs=[recipe_input],
                outputs=[
                    avatar_id_input, voice_id_input,
                    avatar_ratio_input, use_ai_images_input,
                    voiceover_input,
                    bg_choice_input, bg_custom_color, bg_custom_url,
                ],
            )

            bg_choice_input.change(
                fn=on_bg_change,
                inputs=[bg_choice_input],
                outputs=[bg_custom_color, bg_custom_url],
            )

            build_preset_dd.change(
                fn=load_preset_into_build,
                inputs=[build_preset_dd],
                outputs=[
                    recipe_input, avatar_id_input, voice_id_input,
                    avatar_ratio_input, use_ai_images_input,
                    swap_rate_input, style_input, niche_profile_input,
                ],
            )

            build_btn.click(
                fn=build_video,
                inputs=[
                    recipe_input, voiceover_input, script_input,
                    swap_rate_input, style_input,
                    avatar_id_input, voice_id_input,
                    avatar_ratio_input, use_ai_images_input,
                    niche_profile_input,
                    bg_choice_input, bg_custom_color, bg_custom_url,
                    build_preset_dd,
                    caption_style_input, caption_accent_input,
                    caption_position_input, caption_size_input,
                ],
                outputs=[video_output, summary_output, log_output],
            )

        # ===== VOICEOVER STUDIO TAB =====
        with gr.Tab("Voiceover Studio"):
            gr.Markdown(
                "### Voiceover Studio\n"
                "Generate AI voiceovers using Gemini TTS. Choose a voice, set the style, "
                "and generate audio you can use in the Build Video tab."
            )

            vo_preset_dd = gr.Dropdown(
                choices=preset_choices, label="Preset (for history tracking)",
                allow_custom_value=True,
            )

            with gr.Row():
                with gr.Column(scale=1):
                    vo_script_input = gr.Textbox(
                        label="Script",
                        placeholder="Paste the script to narrate...",
                        lines=12,
                    )
                    vo_voice_input = gr.Dropdown(
                        choices=VOICE_CHOICES,
                        value=VOICE_CHOICES[2],
                        label="Voice",
                        info="30 voices available -- each has a different character",
                    )
                    vo_style_input = gr.Dropdown(
                        choices=list(STYLE_PRESETS.keys()),
                        value="Narrator",
                        label="Style Preset",
                    )
                    vo_custom_notes = gr.Textbox(
                        label="Custom Director's Notes",
                        placeholder="e.g. Speak like a seasoned documentary narrator...",
                        lines=4,
                        visible=False,
                    )
                    vo_generate_btn = gr.Button(
                        "Generate Voiceover", variant="primary", size="lg",
                    )

                with gr.Column(scale=1):
                    vo_audio_output = gr.Audio(label="Generated Voiceover", type="filepath")
                    vo_status_output = gr.Textbox(label="Status", interactive=False)
                    gr.Markdown(
                        "*Use the generated audio file in the **Build Video** tab "
                        "by uploading it as the voiceover.*"
                    )

            vo_style_input.change(
                fn=on_vo_style_change,
                inputs=[vo_style_input],
                outputs=[vo_custom_notes],
            )

            vo_generate_btn.click(
                fn=generate_vo,
                inputs=[vo_preset_dd, vo_script_input, vo_voice_input, vo_style_input, vo_custom_notes],
                outputs=[vo_audio_output, vo_status_output],
            )

        # ===== THUMBNAILS TAB =====
        with gr.Tab("Thumbnails"):
            gr.Markdown(
                "### Thumbnail Generator\n"
                "Upload channel screenshots to learn the style, then generate "
                "thumbnails for any video title."
            )

            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("**Setup (one-time per niche)**")
                    thumb_preset_name = gr.Textbox(
                        label="Preset Name",
                        placeholder="e.g. my_history_channel",
                    )
                    thumb_ref_upload = gr.File(
                        label="Upload Reference Screenshots",
                        file_count="multiple",
                        file_types=["image"],
                    )
                    thumb_style_prompt = gr.Textbox(
                        label="Additional Style Instructions (optional)",
                        placeholder="e.g. Always use red/black color scheme, bold Impact font...",
                        lines=3,
                    )
                    thumb_save_btn = gr.Button("Save Preset")
                    thumb_save_status = gr.Textbox(label="Status", interactive=False)

                with gr.Column(scale=1):
                    gr.Markdown("**Generate**")
                    thumb_gen_preset = gr.Dropdown(
                        choices=preset_choices,
                        label="Load Preset",
                        allow_custom_value=True,
                    )
                    thumb_title = gr.Textbox(
                        label="Video Title",
                        placeholder="Enter the video title to generate a thumbnail for...",
                    )
                    thumb_num = gr.Slider(
                        minimum=1, maximum=4, value=2, step=1,
                        label="Number of Options",
                    )
                    thumb_gen_btn = gr.Button("Generate Thumbnails", variant="primary")
                    thumb_gallery = gr.Gallery(label="Generated Thumbnails", columns=2)
                    thumb_gen_status = gr.Textbox(label="Status", interactive=False)

            thumb_save_btn.click(
                fn=save_thumb_preset,
                inputs=[thumb_preset_name, thumb_ref_upload, thumb_style_prompt],
                outputs=[thumb_save_status],
            )

            thumb_gen_btn.click(
                fn=generate_thumb,
                inputs=[thumb_gen_preset, thumb_title, thumb_num],
                outputs=[thumb_gallery, thumb_gen_status],
            )

        # ===== SCRIPT STUDIO TAB =====
        with gr.Tab("Script Studio"):
            gr.Markdown(
                "### Script Studio\n"
                "Claude-powered content pipeline: Channel Analysis -> Video Ideas "
                "-> Titles -> Script. Uses your own Claude API key."
            )

            with gr.Row():
                script_preset_dd = gr.Dropdown(
                    choices=preset_choices, label="Preset",
                    allow_custom_value=True,
                )
                script_refresh_btn = gr.Button("↻", size="sm", min_width=40)
                claude_key_input = gr.Textbox(
                    label="Claude API Key",
                    placeholder="sk-ant-...",
                    type="password",
                    value=ANTHROPIC_KEY,
                )
            script_refresh_btn.click(
                fn=lambda: gr.Dropdown(choices=refresh_preset_choices()),
                outputs=[script_preset_dd],
            )

            with gr.Tabs():
                # --- Channel Setup ---
                with gr.Tab("1. Channel Data"):
                    gr.Markdown(
                        "### Fetch from YouTube (automated)\n"
                        "Enter a channel URL to automatically fetch video titles, "
                        "view counts, and transcripts."
                    )
                    with gr.Row():
                        with gr.Column():
                            ch_yt_url = gr.Textbox(
                                label="YouTube Channel URL",
                                placeholder="https://www.youtube.com/@channelname",
                            )
                            with gr.Row():
                                ch_num_videos = gr.Slider(
                                    minimum=5, maximum=50, value=20, step=5,
                                    label="Number of Videos",
                                )
                                ch_fetch_transcripts = gr.Checkbox(
                                    label="Fetch transcripts",
                                    value=True,
                                )
                            with gr.Row():
                                ch_yt_key = gr.Textbox(
                                    label="YouTube API Key",
                                    value=YOUTUBE_API_KEY,
                                    type="password",
                                    placeholder="AIza...",
                                )
                                ch_ds_key = gr.Textbox(
                                    label="Transcript API Key",
                                    value=DOWNSUB_KEY,
                                    type="password",
                                    placeholder="Your transcript API key...",
                                )
                            ch_fetch_btn = gr.Button(
                                "Fetch Channel Data", variant="primary",
                            )
                            ch_fetch_status = gr.Textbox(
                                label="Results", lines=12, interactive=False,
                            )

                        with gr.Column():
                            ch_fetch_json = gr.Code(
                                label="Channel Data JSON",
                                language="json", lines=15,
                            )
                            ch_analyze_btn = gr.Button(
                                "Analyze Channel with Claude", variant="primary",
                            )
                            ch_analysis_output = gr.Textbox(
                                label="Channel Analysis", lines=12, interactive=False,
                            )

                    gr.Markdown("---\n**Or import manually:**")
                    with gr.Row():
                        ch_data_file = gr.File(
                            label="Upload Channel Data (JSON)",
                            file_types=[".json"],
                        )
                        ch_data_paste = gr.Textbox(
                            label="Or Paste Data",
                            lines=4,
                            placeholder='{"videos": [{"title": "...", "views": 1234}, ...]}',
                        )
                    ch_import_btn = gr.Button("Import Manual Data")
                    ch_import_status = gr.Textbox(label="Import Status", interactive=False)

                    ch_fetch_btn.click(
                        fn=fetch_yt_channel,
                        inputs=[
                            script_preset_dd, ch_yt_url, ch_num_videos,
                            ch_yt_key, ch_ds_key, ch_fetch_transcripts,
                        ],
                        outputs=[ch_fetch_json, ch_fetch_status],
                    )
                    ch_import_btn.click(
                        fn=import_channel_data,
                        inputs=[script_preset_dd, ch_data_file, ch_data_paste],
                        outputs=[ch_import_status],
                    )
                    ch_analyze_btn.click(
                        fn=run_channel_analysis,
                        inputs=[script_preset_dd, claude_key_input],
                        outputs=[ch_analysis_output],
                    )

                # --- Video Ideas ---
                with gr.Tab("2. Video Ideas"):
                    gr.Markdown("Generate viral video ideas based on your channel data.")
                    ideas_num = gr.Slider(
                        minimum=3, maximum=15, value=7, step=1,
                        label="Number of Ideas",
                    )
                    ideas_btn = gr.Button("Generate Video Ideas", variant="primary")
                    ideas_output = gr.Textbox(label="Video Ideas", lines=20, interactive=False)
                    ideas_selected = gr.Textbox(
                        label="Selected Idea (copy-paste or type your own)",
                        placeholder="Paste or type the idea you want to develop...",
                    )

                    ideas_btn.click(
                        fn=run_generate_ideas,
                        inputs=[script_preset_dd, claude_key_input, ideas_num],
                        outputs=[ideas_output],
                    )
                    ideas_selected.change(
                        fn=lambda p, t: save_studio_text(p, "selected_idea", t),
                        inputs=[script_preset_dd, ideas_selected],
                        outputs=[],
                    )

                # --- Titles ---
                with gr.Tab("3. Titles"):
                    gr.Markdown("Generate viral title options for your chosen video idea.")
                    title_idea_input = gr.Textbox(
                        label="Video Idea",
                        placeholder="Paste the idea from the Ideas tab...",
                        lines=3,
                    )
                    titles_btn = gr.Button("Generate Titles", variant="primary")
                    titles_output = gr.Textbox(label="Title Options", lines=12, interactive=False)
                    title_selected = gr.Textbox(
                        label="Selected Title",
                        placeholder="Copy your chosen title here...",
                    )

                    titles_btn.click(
                        fn=run_generate_titles,
                        inputs=[script_preset_dd, claude_key_input, title_idea_input],
                        outputs=[titles_output],
                    )
                    title_selected.change(
                        fn=lambda p, t: save_studio_text(p, "selected_title", t),
                        inputs=[script_preset_dd, title_selected],
                        outputs=[],
                    )
                    title_idea_input.change(
                        fn=lambda p, t: save_studio_text(p, "title_idea", t),
                        inputs=[script_preset_dd, title_idea_input],
                        outputs=[],
                    )

                # --- Script Writer ---
                with gr.Tab("4. Write Script"):
                    gr.Markdown("Generate a full video script from your title and idea.")
                    with gr.Row():
                        sw_title = gr.Textbox(
                            label="Video Title", placeholder="Paste the chosen title...",
                        )
                        sw_idea = gr.Textbox(
                            label="Video Idea (optional context)",
                            placeholder="Paste the idea for extra context...",
                        )
                    sw_length = gr.Slider(
                        minimum=3, maximum=20, value=8, step=1,
                        label="Target Length (minutes)",
                    )
                    sw_btn = gr.Button("Write Script", variant="primary")
                    sw_output = gr.Textbox(
                        label="Generated Script", lines=20,
                        info="Copy this into the Build Video tab to create your video",
                    )

                    sw_btn.click(
                        fn=run_generate_script,
                        inputs=[
                            script_preset_dd, claude_key_input,
                            sw_title, sw_idea, sw_length,
                        ],
                        outputs=[sw_output],
                    )
                    sw_title.change(
                        fn=lambda p, t: save_studio_text(p, "script_title", t),
                        inputs=[script_preset_dd, sw_title],
                        outputs=[],
                    )
                    sw_idea.change(
                        fn=lambda p, t: save_studio_text(p, "script_idea", t),
                        inputs=[script_preset_dd, sw_idea],
                        outputs=[],
                    )

            # Wire preset selection to load all Script Studio state
            script_preset_dd.change(
                fn=load_preset_into_script_studio,
                inputs=[script_preset_dd],
                outputs=[
                    ch_fetch_json, ch_analysis_output,
                    ideas_output, ideas_selected,
                    title_idea_input, titles_output, title_selected,
                    sw_output, sw_title, sw_idea,
                ],
            )

        # ===== NICHE SCREENER TAB =====
        with gr.Tab("Niche Screener"):
            gr.Markdown(
                "### Niche Screener\n"
                "Paste a YouTube URL and Gemini will watch the video to analyze its "
                "B-roll strategy, visual style, and production techniques."
            )
            with gr.Row():
                with gr.Column(scale=1):
                    yt_url_input = gr.Textbox(
                        label="YouTube URL",
                        placeholder="https://www.youtube.com/watch?v=...",
                    )
                    analyze_minutes_input = gr.Slider(
                        minimum=1, maximum=10, value=5, step=1,
                        label="Minutes to Analyze",
                    )
                    screener_preset_name = gr.Textbox(
                        label="Save to Preset (optional)",
                        placeholder="e.g. geopolitical_military",
                    )
                    screen_btn = gr.Button("Analyze Niche", variant="primary")

                with gr.Column(scale=1):
                    screener_verdict = gr.Textbox(label="Analysis", lines=15)
                    screener_profile = gr.Code(
                        label="NicheProfile JSON (copy to Build tab)",
                        language="json", lines=15,
                    )

            screen_btn.click(
                fn=analyze_niche,
                inputs=[yt_url_input, analyze_minutes_input, screener_preset_name],
                outputs=[screener_profile, screener_verdict],
            )

        # ===== HISTORY TAB =====
        with gr.Tab("History"):
            gr.Markdown(
                "### Generation History\n"
                "Review all past generations for a preset: video ideas, titles, "
                "scripts, thumbnails, voiceovers, and videos."
            )

            with gr.Row():
                hist_preset_dd = gr.Dropdown(
                    choices=preset_choices, label="Preset",
                    allow_custom_value=False,
                )
                hist_refresh_presets_btn = gr.Button("↻", size="sm", min_width=40)
            hist_refresh_presets_btn.click(
                fn=lambda: gr.Dropdown(choices=refresh_preset_choices()),
                outputs=[hist_preset_dd],
            )
            hist_filter = gr.Dropdown(
                choices=["All", "ideas", "titles", "script", "thumbnail", "voiceover", "video", "analysis"],
                value="All", label="Filter by type",
            )
            hist_refresh_btn = gr.Button("Load History", variant="primary")
            hist_output = gr.Textbox(label="History", lines=30, interactive=False)
            hist_gallery = gr.Gallery(label="Thumbnail History", columns=3, visible=False)

            def load_history(preset_name, filter_type):
                if not preset_name:
                    return "Select a preset first.", []

                entry_type = "" if filter_type == "All" else filter_type
                entries = presets.get_history(preset_name, entry_type)

                if not entries:
                    return "No history entries found.", []

                from datetime import datetime
                lines = []
                thumb_paths = []

                for e in reversed(entries):
                    ts = datetime.fromtimestamp(e["timestamp"]).strftime("%Y-%m-%d %H:%M")
                    etype = e["type"].upper()
                    data = e.get("data", {})

                    lines.append(f"{'='*60}")
                    lines.append(f"[{ts}] {etype}")
                    lines.append(f"{'='*60}")

                    if e["type"] == "thumbnail":
                        paths = data.get("paths", [])
                        thumb_paths.extend(p for p in paths if Path(p).exists())
                        lines.append(f"Title: {data.get('title', 'N/A')}")
                        lines.append(f"Files: {len(paths)}")
                    elif e["type"] == "video":
                        lines.append(f"Path: {data.get('path', 'N/A')}")
                        lines.append(f"Recipe: {data.get('recipe', 'N/A')}")
                        lines.append(data.get("summary", ""))
                    elif e["type"] == "voiceover":
                        lines.append(f"Path: {data.get('path', 'N/A')}")
                        lines.append(f"Voice: {data.get('voice', 'N/A')}")
                        lines.append(f"Style: {data.get('style', 'N/A')}")
                    else:
                        text = data.get("text", "")
                        if data.get("title"):
                            lines.append(f"Title: {data['title']}")
                        if data.get("idea"):
                            lines.append(f"Idea: {data['idea']}")
                        lines.append(text[:1000] if len(text) > 1000 else text)

                    lines.append("")

                gallery_visible = len(thumb_paths) > 0
                return "\n".join(lines), gr.Gallery(
                    value=thumb_paths if thumb_paths else [],
                    visible=gallery_visible,
                )

            hist_refresh_btn.click(
                fn=load_history,
                inputs=[hist_preset_dd, hist_filter],
                outputs=[hist_output, hist_gallery],
            )

        # ===== SETTINGS TAB =====
        with gr.Tab("Settings"):
            gr.Markdown(
                "### API Keys\n"
                "Add your API keys here. Keys are saved to `.env` and persist across sessions."
            )

            with gr.Row():
                with gr.Column():
                    set_gemini = gr.Textbox(
                        label="Gemini API Key (required)",
                        value=GEMINI_KEY,
                        type="password",
                    )
                    set_gemini_test = gr.Button("Test", size="sm")
                    set_gemini_status = gr.Textbox(show_label=False, interactive=False)

                    set_pexels = gr.Textbox(
                        label="Pexels API Key (optional - stock photos)",
                        value=PEXELS_KEY,
                        type="password",
                    )
                    set_pexels_test = gr.Button("Test", size="sm")
                    set_pexels_status = gr.Textbox(show_label=False, interactive=False)

                with gr.Column():
                    set_heygen = gr.Textbox(
                        label="HeyGen API Key (optional - avatar videos)",
                        value=HEYGEN_KEY,
                        type="password",
                    )
                    set_heygen_test = gr.Button("Test", size="sm")
                    set_heygen_status = gr.Textbox(show_label=False, interactive=False)

                    set_claude = gr.Textbox(
                        label="Claude API Key (optional - Script Studio)",
                        value=ANTHROPIC_KEY,
                        type="password",
                    )
                    set_claude_test = gr.Button("Test", size="sm")
                    set_claude_status = gr.Textbox(show_label=False, interactive=False)

                    set_yt = gr.Textbox(
                        label="YouTube API Key (optional - channel data fetch)",
                        value=YOUTUBE_API_KEY,
                        type="password",
                    )
                    set_yt_test = gr.Button("Test", size="sm")
                    set_yt_status = gr.Textbox(show_label=False, interactive=False)

                    set_ds = gr.Textbox(
                        label="Transcript API Key (optional - transcript fetch)",
                        value=DOWNSUB_KEY,
                        type="password",
                    )

                    set_atlas = gr.Textbox(
                        label="Atlas Cloud API Key (Thumbnails - Nano Banana Pro)",
                        value=ATLASCLOUD_KEY,
                        type="password",
                    )
                    set_atlas_test = gr.Button("Test", size="sm")
                    set_atlas_status = gr.Textbox(show_label=False, interactive=False)

            save_keys_btn = gr.Button("Save All Keys", variant="primary")
            save_keys_status = gr.Textbox(label="Status", interactive=False)

            save_keys_btn.click(
                fn=save_api_keys,
                inputs=[set_gemini, set_pexels, set_heygen, set_claude, set_yt, set_ds, set_atlas],
                outputs=[save_keys_status],
            )

            set_gemini_test.click(
                fn=lambda k: test_api_key("Gemini", k),
                inputs=[set_gemini], outputs=[set_gemini_status],
            )
            set_pexels_test.click(
                fn=lambda k: test_api_key("Pexels", k),
                inputs=[set_pexels], outputs=[set_pexels_status],
            )
            set_heygen_test.click(
                fn=lambda k: test_api_key("HeyGen", k),
                inputs=[set_heygen], outputs=[set_heygen_status],
            )
            set_claude_test.click(
                fn=lambda k: test_api_key("Claude", k),
                inputs=[set_claude], outputs=[set_claude_status],
            )
            set_yt_test.click(
                fn=lambda k: test_api_key("YouTube", k),
                inputs=[set_yt], outputs=[set_yt_status],
            )
            set_atlas_test.click(
                fn=lambda k: test_api_key("AtlasCloud", k),
                inputs=[set_atlas], outputs=[set_atlas_status],
            )


if __name__ == "__main__":
    app.queue(default_concurrency_limit=1)
    app.launch(
        share=False,
        server_name="0.0.0.0",
        server_port=7860,
        theme=gr.themes.Soft(),
    )
