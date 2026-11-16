"""Small output helpers."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise RuntimeError(f"No rows were produced for {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def save_figure(fig, results: Path, stem: str) -> None:
    """Save a PNG for insertion and an SVG for editing."""
    results.mkdir(parents=True, exist_ok=True)
    fig.savefig(results / f"{stem}.png", dpi=220, bbox_inches="tight")
    fig.savefig(results / f"{stem}.svg", bbox_inches="tight")
