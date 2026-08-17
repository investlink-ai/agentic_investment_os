from importlib import import_module


def test_package_is_importable() -> None:
    package = import_module("agentic_investment_os")

    assert package.__name__ == "agentic_investment_os"
