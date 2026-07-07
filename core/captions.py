"""
Professional caption system using pycaps.

Provides styled, animated, word-level captions with preset styles.
Replaces the old ASS subtitle generation in assembler.py.
"""

from __future__ import annotations
from pathlib import Path
from config import VIDEO_WIDTH, VIDEO_HEIGHT

CAPTION_STYLES = {
    "None": None,
    "Clean": {
        "css": """\
.word {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 32px;
    font-weight: 700;
    color: white;
    text-transform: uppercase;
    text-shadow:
        -2px -2px 0 rgba(0,0,0,0.8),
        2px -2px 0 rgba(0,0,0,0.8),
        -2px 2px 0 rgba(0,0,0,0.8),
        2px 2px 0 rgba(0,0,0,0.8);
}
.word-being-narrated {
    color: #FFD700;
}
""",
        "animations": [
            {"type": "fade_in", "when": "narration-starts", "what": "segment", "duration": 0.2}
        ],
        "layout": {"max_number_of_lines": 2, "vertical_align": {"align": "bottom", "offset": -0.08}},
    },
    "Modern": {
        "css": """\
.word {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 36px;
    font-weight: 800;
    color: white;
    text-transform: uppercase;
    letter-spacing: 1px;
    text-shadow:
        0 2px 8px rgba(0,0,0,0.6);
}
.word-being-narrated {
    color: {accent_color};
    transform: scale(1.1);
}
""",
        "animations": [
            {"type": "pop_in", "when": "narration-starts", "what": "word", "duration": 0.15}
        ],
        "layout": {"max_number_of_lines": 1, "vertical_align": {"align": "bottom", "offset": -0.1}},
    },
    "Minimal": {
        "css": """\
.word {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 28px;
    font-weight: 400;
    color: rgba(255,255,255,0.9);
    text-shadow: 0 1px 4px rgba(0,0,0,0.5);
}
.word-being-narrated {
    color: white;
    font-weight: 600;
}
""",
        "animations": [
            {"type": "fade_in", "when": "narration-starts", "what": "segment", "duration": 0.3}
        ],
        "layout": {"max_number_of_lines": 2, "vertical_align": {"align": "bottom", "offset": -0.06}},
    },
    "News": {
        "css": """\
.line {
    background-color: rgba(0,0,0,0.75);
    padding: 4px 12px;
    border-radius: 2px;
}
.word {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 30px;
    font-weight: 700;
    color: white;
}
.word-being-narrated {
    color: #FFDD00;
}
""",
        "animations": [
            {"type": "fade_in", "when": "narration-starts", "what": "segment", "duration": 0.15}
        ],
        "layout": {"max_number_of_lines": 2, "vertical_align": {"align": "bottom", "offset": -0.05}},
    },
}


def list_caption_styles() -> list[str]:
    """Return all available caption style names."""
    return list(CAPTION_STYLES.keys())


def burn_captions(
    video_path: str,
    output_path: str,
    caption_style: str = "Clean",
    accent_color: str = "#00BFFF",
    font_size: str = "Medium",
    position: str = "Bottom",
) -> str:
    """
    Burn captions into a video using pycaps.

    Transcribes audio from the video and applies styled, word-level captions.
    Returns path to the output video with captions.
    """
    if caption_style == "None" or caption_style not in CAPTION_STYLES:
        import shutil
        shutil.copy2(video_path, output_path)
        return output_path

    style_config = CAPTION_STYLES[caption_style]

    size_map = {"Small": 0.75, "Medium": 1.0, "Large": 1.3}
    size_factor = size_map.get(font_size, 1.0)

    pos_map = {
        "Bottom": {"align": "bottom", "offset": -0.08},
        "Center": {"align": "center", "offset": 0.0},
        "Top": {"align": "top", "offset": 0.08},
    }
    vert_align = pos_map.get(position, pos_map["Bottom"])

    css = style_config["css"].replace("{accent_color}", accent_color)

    if size_factor != 1.0:
        import re
        def _scale_font(match):
            original = int(match.group(1))
            return f"font-size: {int(original * size_factor)}px"
        css = re.sub(r'font-size:\s*(\d+)px', _scale_font, css)

    from pycaps import CapsPipelineBuilder, EventType, ElementType
    from pycaps.animation import FadeIn

    builder = CapsPipelineBuilder()
    builder.with_input_video(video_path)
    builder.with_output_video(output_path)

    builder.add_css_content(css)

    layout = dict(style_config.get("layout", {}))
    layout["vertical_align"] = vert_align
    builder.with_layout(layout)

    builder.add_animation(
        animation=FadeIn(duration=0.2),
        when=EventType.ON_NARRATION_STARTS,
        what=ElementType.SEGMENT,
    )

    pipeline = builder.build()
    pipeline.run()

    print(f"[captions] Burned '{caption_style}' captions into {output_path}")
    return output_path


def burn_captions_simple(
    video_path: str,
    output_path: str,
    caption_style: str = "Clean",
    accent_color: str = "#00BFFF",
    font_size: str = "Medium",
    position: str = "Bottom",
) -> str:
    """
    Caption burner using pycaps. Runs in a subprocess to avoid library
    conflicts between PyAV and OpenCV that can crash the main process.
    """
    if caption_style == "None":
        import shutil
        shutil.copy2(video_path, output_path)
        return output_path

    template_map = {
        "Clean": "minimalist",
        "Modern": "hype",
        "Minimal": "minimalist",
        "News": "classic",
    }
    template_name = template_map.get(caption_style, "minimalist")

    import subprocess
    import sys
    import os

    script = (
        f"import ssl, certifi, os\n"
        f"os.environ['SSL_CERT_FILE'] = certifi.where()\n"
        f"os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()\n"
        f"from pycaps import TemplateLoader\n"
        f"builder = TemplateLoader('{template_name}')"
        f".with_input_video('{video_path}').load(False)\n"
        f"builder.with_output_video('{output_path}')\n"
        f"pipeline = builder.build()\n"
        f"pipeline.run()\n"
        f"print('PYCAPS_OK')\n"
    )

    env = os.environ.copy()
    try:
        import certifi
        env["SSL_CERT_FILE"] = certifi.where()
        env["REQUESTS_CA_BUNDLE"] = certifi.where()
    except ImportError:
        pass

    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True,
            timeout=300,
            cwd=os.path.dirname(os.path.abspath(video_path)),
            env=env,
        )
        if result.returncode == 0 and "PYCAPS_OK" in result.stdout:
            print(f"[captions] Burned '{template_name}' template captions")
            return output_path
        else:
            stderr = result.stderr[-300:] if result.stderr else "no stderr"
            print(f"[captions] pycaps subprocess failed: {stderr}")
    except subprocess.TimeoutExpired:
        print(f"[captions] pycaps timed out after 300s")
    except Exception as e:
        print(f"[captions] pycaps error: {e}")

    import shutil
    shutil.copy2(video_path, output_path)
    return output_path
