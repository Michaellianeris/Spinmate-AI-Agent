"""The interface every simulation engine implements."""
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class Validation:
    ok: bool
    error: str = ""
    steps: int = 0
    estimated_runtime_s: float = 0.0
    changed_from_defaults: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)   # engine-specific details


@runtime_checkable
class Engine(Protocol):
    name: str          # short id, e.g. "macrospin"
    title: str         # human name
    description: str

    def parameter_schema(self) -> dict:
        """Every parameter: {name: {"default": value, "kind": "float" | "vector" | "flag"}}."""

    def validate(self, parameters_text: str) -> Validation:
        """Parse the input with the engine's own parser, without running it."""

    def run_command(self) -> list:
        """Command that runs one simulation in the current folder (parameters.txt is there)."""

    def outputs(self) -> dict:
        """Files a finished run leaves in its folder: {file name: description}."""
