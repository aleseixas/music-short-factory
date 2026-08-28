"""Motor reutilizavel do Music Short Factory."""


async def build_video(*args, **kwargs):
    from .pipeline import build_video as _build_video

    return await _build_video(*args, **kwargs)


__all__ = ["build_video"]
