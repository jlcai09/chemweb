from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


STRUCTURE_EXTENSIONS = {
    ".xyz": "xyz",
    ".pdb": "pdb",
    ".mol": "mol",
    ".sdf": "sdf",
    ".cif": "cif",
    ".traj": "traj",
    ".extxyz": "extxyz",
    ".vasp": "vasp",
    ".xsf": "xsf",
    ".xsd": "xsd",
    ".xtd": "xtd",
    ".arc": "dmol-arc",
    ".cube": "cube",
    ".gen": "gen",
    ".db": "db",
    ".lammps": "lammps",
    ".dump": "lammps-dump-text",
    ".gjf": "gaussian-in",
    ".com": "gaussian-in",
    ".fdf": "fdf",
    ".pwi": "espresso-in",
}

TEXT_EXTENSIONS = {
    ".txt",
    ".log",
    ".out",
    ".in",
    ".inp",
    ".sh",
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".dat",
    ".csv",
    ".err",
    ".xml",
}

TEXT_NAMES = {
    "INCAR",
    "KPOINTS",
    "OSZICAR",
    "POTCAR",
}

ASE_STRUCTURE_NAMES: dict[str, Optional[str]] = {
    "vasp.xml": "vasp-xml",
    "vasprun.xml": "vasp-xml",
}

VASP_STRUCTURE_NAME_RE = re.compile(
    r"(^|[^A-Z0-9])(POSCAR|CONTCAR|XDATCAR|OUTCAR)(?:[0-9]+)?($|[^A-Z0-9])"
)


def is_forced_structure_name(path: Path) -> bool:
    return bool(VASP_STRUCTURE_NAME_RE.search(path.name.upper()))


def detect_preview(path: Path, *, is_dir: bool = False) -> tuple[str, Optional[str]]:
    if is_dir:
        return "directory", None

    lower_name = path.name.lower()
    if lower_name in ASE_STRUCTURE_NAMES:
        return "structure", ASE_STRUCTURE_NAMES[lower_name]

    if is_forced_structure_name(path):
        return "structure", None

    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS or path.name.upper() in TEXT_NAMES:
        return "text", None

    if suffix in STRUCTURE_EXTENSIONS:
        return "structure", STRUCTURE_EXTENSIONS[suffix]
    return "file", None
