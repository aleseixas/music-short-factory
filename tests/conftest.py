"""Legacy unit fixtures stay offline; distributed tests inject a real shared backend."""
import pytest
from pathlib import Path
from unittest.mock import patch


def pytest_configure(config):
    config.addinivalue_line("markers", "distributed_coordination: exercise the authoritative shared backend")


@pytest.fixture(autouse=True)
def isolated_coordination_for_legacy_units(request):
    if request.node.get_closest_marker("distributed_coordination"):
        yield
        return

    def fixture_factory(root):
        from engine.shared_coordination import _OVERRIDES
        return _OVERRIDES.get().get(str(Path(root).resolve()))

    # Production has no environment switch or implicit local fallback.
    with patch("engine.coordination_runtime._factory", side_effect=fixture_factory):
        yield
