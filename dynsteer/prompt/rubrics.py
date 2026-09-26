import json
from pathlib import Path

from dynsteer.model import Dimension, JsonObject

_RUBRICS_PATH = Path(__file__).with_name("rubrics.json")
_RUBRICS: JsonObject = json.loads(_RUBRICS_PATH.read_text(encoding="utf-8"))


def rubrics_for_dimensions(dimensions: list[Dimension] | tuple[Dimension, ...]) -> JsonObject:
    """Returns the detailed rubric JSON of the specified dimensions."""
    if dimensions is None:
        raise ValueError("It can't be empty.")
    return {dimension.value: _RUBRICS[dimension.value] for dimension in dimensions}
