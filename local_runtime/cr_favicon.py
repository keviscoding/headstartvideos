"""The studio's tab icon: the same mark as channelrecipe.com, served by the studio itself (it works offline).

The browser asks every page for /favicon.ico; pages also link the SVG, which can carry a status dot: violet while
the studio is making something, green when a video is ready. The ICO is the website's own favicon.ico.
"""
from __future__ import annotations

import base64

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" fill="none" width="32" height="32"><defs><linear'
       'Gradient id="crpot-heat" x1="0" y1="1" x2="0.6" y2="0"><stop offset="0" stop-color="#6D5AE0"/><stop offset="0.'
       '6" stop-color="#9B5BE6"/><stop offset="1" stop-color="#E8608F"/></linearGradient></defs><g stroke="#FF7A59" st'
       'roke-width="6" stroke-linecap="round"><path d="M37 24C33 19 41 15 37 7"/><path d="M50 24C46 19 54 15 50 7"/><p'
       'ath d="M63 24C59 19 67 15 63 7"/></g><rect x="44" y="27" width="12" height="6" rx="3" fill="#9B5BE6"/><rect x='
       '"14" y="32" width="72" height="8" rx="4" fill="#9B5BE6"/><rect x="7" y="49" width="14" height="9" rx="4.5" fil'
       'l="#7F5AE3"/><rect x="79" y="49" width="14" height="9" rx="4.5" fill="#A95BE4"/><path d="M18 43H82V74A14 14 0 '
       '0 1 68 88H32A14 14 0 0 1 18 74Z" fill="url(#crpot-heat)"/><path d="M44 55L44 76L62 65.5Z" fill="#F7F5F0" strok'
       'e="#F7F5F0" stroke-width="5" stroke-linejoin="round"/></svg>')

_ICO = ('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')
ICO = base64.b64decode(_ICO)

_DOTS = {"busy": "#9B8CFF", "done": "#14B87A"}


def svg(state: str = "") -> str:
    """The mark, with a small status dot in the corner for "busy" or "done"."""
    color = _DOTS.get(state)
    if not color:
        return SVG
    dot = (f'<circle cx="82" cy="18" r="17" fill="#0F1222"/><circle cx="82" cy="18" r="13" fill="{color}"/>')
    return SVG[:-len("</svg>")] + dot + "</svg>"
