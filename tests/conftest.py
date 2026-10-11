import pytest

from clearclassnet.dispatch.registry import get_registry


@pytest.fixture(autouse=True)
def _clear_matchers() -> None:
    get_registry().clear()
    yield
    get_registry().clear()
