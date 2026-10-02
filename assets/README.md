# Assets

## overlay_dust.mp4

**Purpose**: Procedural dust particle overlay for Frontier recipe video assembly.

**Specifications**:
- Resolution: 960x540 (scaled during blend)
- Duration: 10 seconds (seamless loop via FFmpeg `-stream_loop -1`)
- Size: 32MB
- Encoding: H.264, yuv420p

**Generation**:
```bash
ffmpeg -f lavfi -i "nullsrc=s=1920x1080:d=10,geq='lum=random(1)*255:cb=128:cr=128',noise=alls=20:allf=t+u" \
  -vf "eq=brightness=-0.3:contrast=0.8,fade=in:st=0:d=0.5,fade=out:st=9.5:d=0.5" \
  -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -t 10 overlay_dust.mp4
```

**Usage**:
Applied in `core/frontier_assembler.py` via screen blend at 30% opacity:
```python
blend=all_mode=screen:all_opacity=0.3
```

**Legal**: Procedurally generated, no licensing restrictions.
