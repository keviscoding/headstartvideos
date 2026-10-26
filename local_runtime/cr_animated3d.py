"""Studio's animated 3D visuals (look.visuals "cr_animated3d"): a 3D channel's own renderer, which the creator's
Claude Code writes during the style study on the 3D kit (three.js), drawn in the same locked page as the 2D renderers
(cr_animated2d) with WebGL. Per video Claude only casts and directs; the renderer draws every frame on this computer."""
from cr_animated2d import assemble, prepare

__all__ = ["assemble", "prepare"]
