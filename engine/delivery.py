from __future__ import annotations

from dataclasses import dataclass


DEFAULT_DELIVERY = "neutral"


@dataclass(frozen=True)
class DeliveryPreset:
    """Provider-independent voice adjustments for one editorial intention."""

    rate_delta_percent: int = 0
    pitch_delta_hz: int = 0
    volume_delta_percent: int = 0


# Keep these adjustments deliberately moderate. Providers translate only the
# controls they support; the story schema remains independent from that choice.
DELIVERY_PRESETS: dict[str, DeliveryPreset] = {
    "neutral": DeliveryPreset(),
    "hook": DeliveryPreset(
        rate_delta_percent=6,
        pitch_delta_hz=3,
        volume_delta_percent=1,
    ),
    "curious": DeliveryPreset(
        rate_delta_percent=1,
        pitch_delta_hz=2,
    ),
    "emotional": DeliveryPreset(
        rate_delta_percent=-5,
        pitch_delta_hz=-2,
        volume_delta_percent=-1,
    ),
    "dramatic": DeliveryPreset(
        rate_delta_percent=-3,
        pitch_delta_hz=-4,
        volume_delta_percent=1,
    ),
    "reveal": DeliveryPreset(
        rate_delta_percent=3,
        pitch_delta_hz=2,
        volume_delta_percent=1,
    ),
    "payoff": DeliveryPreset(
        pitch_delta_hz=-1,
        volume_delta_percent=1,
    ),
}

DELIVERY_NAMES = frozenset(DELIVERY_PRESETS)


def get_delivery_preset(delivery: str) -> DeliveryPreset:
    try:
        return DELIVERY_PRESETS[delivery]
    except KeyError as exc:
        supported = ", ".join(DELIVERY_PRESETS)
        raise ValueError(
            f"Delivery de narracao invalido: {delivery!r}. Valores: {supported}."
        ) from exc
