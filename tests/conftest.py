import json
from pathlib import Path

import pytest

from vera.api import app as app_module
from vera.state.store import Store

DATASET_DIR = Path(__file__).parent.parent / "docs" / "challenge-package" / "dataset"


@pytest.fixture(autouse=True)
def _reset_store():
    """Each test gets a fresh in-memory Store: the app module holds one process-wide instance,
    which is exactly the persistence model the contract expects, but tests must not leak state
    into each other."""
    app_module.store = Store()
    yield


def _load_seed(filename: str, container: str) -> list[dict]:
    data = json.loads((DATASET_DIR / filename).read_text())
    return list(data.get(container, []))


@pytest.fixture(scope="session")
def restaurants_category() -> dict:
    return json.loads((DATASET_DIR / "categories" / "restaurants.json").read_text())


@pytest.fixture(scope="session")
def dentists_category() -> dict:
    return json.loads((DATASET_DIR / "categories" / "dentists.json").read_text())


@pytest.fixture(scope="session")
def restaurant_merchants() -> list[dict]:
    merchants = _load_seed("merchants_seed.json", "merchants")
    return [m for m in merchants if m.get("category_slug") == "restaurants"]


@pytest.fixture(scope="session")
def dentist_merchants() -> list[dict]:
    merchants = _load_seed("merchants_seed.json", "merchants")
    return [m for m in merchants if m.get("category_slug") == "dentists"]


@pytest.fixture(scope="session")
def festival_trigger() -> dict:
    triggers = _load_seed("triggers_seed.json", "triggers")
    for t in triggers:
        if t.get("kind") == "festival_upcoming":
            return t
    raise AssertionError("no festival_upcoming trigger in the seed dataset")
