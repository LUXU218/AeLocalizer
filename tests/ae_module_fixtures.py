"""Exact test fixtures for the alternate After Effects 2026 installation."""
from pathlib import Path

from runtime import build_profile


AE_26_3_0_87 = {
    "AfterFXLib.dll": "58591e27941fa0a4d1998967fa125d030f2cf307a62d02a6037dfcaffce8b53c",
    "flt.dll": "65eb2a26803a76e868d20abc72e96ea45e2949573213d21d78d0f7a36494b940",
    "dvaui.dll": "86d43382ac61ea4b72ad8d821cf57a73feba1532881e02315abb2c50fd7bdf50",
}


def is_ae_26_3_0_87(path, sha=None):
    expected = AE_26_3_0_87.get(Path(path).name)
    return bool(expected and (sha or build_profile.sha256_file(path)) == expected)
