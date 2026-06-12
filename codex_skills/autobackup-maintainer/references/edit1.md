# EDIT_1 Reference

## Key files

- `EDIT_1/main.py`: app entry/UI orchestration.
- `EDIT_1/ui_main.py`: UI widgets and settings.
- `EDIT_1/video_engine.py`: FFmpeg command construction and render execution.
- `EDIT_1/preview_engine.py`: preview image/frame rendering.
- `EDIT_1/overlay_layout.py`: overlay/text/background layout calculations.
- `EDIT_1/queue_manager.py`: render queue handling.
- `EDIT_1/style.qss`: UI style.

## Rules

- Treat preview and final render as one contract.
- If a layout option changes, inspect both `preview_engine.py` and `video_engine.py`.
- Prefer structured FFmpeg filter graph construction over string patches spread across UI code.
- Keep generated temp files in `.codex_tmp/` or app temp dirs, not root.
- Do not commit test outputs like zero-byte mp4 files unless explicitly needed.

## Checks

Syntax:

```powershell
python -c "import ast, pathlib; files=list(pathlib.Path('EDIT_1').glob('*.py')); [ast.parse(p.read_text(encoding='utf-8-sig'), filename=str(p)) for p in files]; print('edit1 syntax ok')"
```

Search render graph:

```powershell
rg -n "filter_complex|drawtext|overlay|bg_image|text|subtitle|ffmpeg|map" EDIT_1
```

## Common failure modes

- Preview differs from render: layout math duplicated or missing in one side.
- FFmpeg audio map error: `-map [0:a]` or similar invalid label.
- Text clipping/overlap: UI allows values that render engine does not constrain.
- Background image conflict: composed/background fit and crop filters ordered incorrectly.
