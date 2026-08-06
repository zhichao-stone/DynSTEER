import pytest

from dynsteer.harness.config import milestone_generation_from_mapping


def test_milestone_generation_parser() -> None:
    config = milestone_generation_from_mapping(
        {
            "use_origin_milestone": False,
            "simulated_path_count": 4,
            "generator": {"provider": "openai", "model": "model"},
        }
    )
    assert config.use_origin_milestone is False
    assert config.simulated_path_count == 4


@pytest.mark.parametrize(
    "data",
    [
        {"unknown": True},
        {"simulated_path_count": 2},
        {"generator": {"api_key": "secret"}},
    ],
)
def test_milestone_generation_parser_rejects_invalid_fields(
    data: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        milestone_generation_from_mapping(data)
