from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from capture_formal_runtime_environment import (  # noqa: E402
    direct_pip_packages,
    direct_pip_requirements,
    direct_runtime_requirements,
)


def test_direct_pip_packages_preserve_complete_unique_environment_contract() -> None:
    environment = {
        "dependencies": [
            "python=3.11",
            "pip=25.3",
            {
                "pip": [
                    "chardet==7.4.3",
                    "requests==2.34.2",
                ]
            },
        ]
    }

    assert direct_pip_packages(environment) == ["chardet", "requests"]
    assert direct_pip_requirements(environment) == {
        "chardet": "7.4.3",
        "requests": "2.34.2",
    }
    assert direct_runtime_requirements(environment) == {
        "python": "3.11",
        "pip": "25.3",
    }
