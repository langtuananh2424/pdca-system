import importlib

import pytest


@pytest.mark.parametrize("package", ["pdca_core", "adapters", "apps"])
def test_packages_importable(package: str) -> None:
    importlib.import_module(package)
