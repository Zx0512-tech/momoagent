"""Validation helpers for the verified STbridge OpenSees model."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


VERIFIED_REFERENCE_FREQS = (
    0.05530750551942655,
    0.10040736400449973,
    0.18571922362932938,
    0.22587850675860627,
    0.2985259681015457,
    0.3229275392524449,
    0.37473462678490876,
    0.38708968945802447,
    0.39316339469550815,
    0.4221885435709374,
)


@dataclass(frozen=True)
class OpenSeesReference:
    model_path: str = "bridge_models/stbridge_opensees/stbridge_opensees_modal_model.py"
    earthquake_entry_path: str | None = None
    model_data_path: str | None = None
    frequencies_csv: str = "docs/examples/opensees_reference_frequencies.csv"

    def required_files_exist(self) -> bool:
        return all(
            Path(path).exists()
            for path in [
                self.model_path,
                self.earthquake_entry_path,
                self.model_data_path,
                self.frequencies_csv,
            ]
            if path is not None
        )


def read_frequency_csv(path: str | Path) -> list[float]:
    with Path(path).open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [float(row["frequency_hz"]) for row in reader]


def validate_modal_frequencies(
    frequencies: list[float],
    references: tuple[float, ...] = VERIFIED_REFERENCE_FREQS,
    tolerance_pct: float = 5.0,
) -> dict[str, object]:
    count = min(len(frequencies), len(references))
    errors = [abs(frequencies[i] - references[i]) / references[i] * 100.0 for i in range(count)]
    return {
        "checked_modes": count,
        "errors_pct": errors,
        "all_within_tolerance": count == len(references) and all(error <= tolerance_pct for error in errors),
        "tolerance_pct": tolerance_pct,
    }
