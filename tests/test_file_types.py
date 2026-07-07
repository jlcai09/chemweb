from pathlib import Path

from backend.app.services.file_types import detect_preview


def test_detect_structure_types() -> None:
    assert detect_preview(Path("mol.xyz")) == ("structure", "xyz")
    assert detect_preview(Path("protein.pdb")) == ("structure", "pdb")
    assert detect_preview(Path("movie.traj")) == ("structure", "traj")
    assert detect_preview(Path("movie.extxyz")) == ("structure", "extxyz")
    assert detect_preview(Path("model.xsd")) == ("structure", "xsd")
    assert detect_preview(Path("model.xtd")) == ("structure", "xtd")
    assert detect_preview(Path("movie.arc")) == ("structure", "dmol-arc")
    assert detect_preview(Path("structures.db")) == ("structure", "db")
    assert detect_preview(Path("vasp.xml")) == ("structure", "vasp-xml")
    assert detect_preview(Path("vasprun.xml")) == ("structure", "vasp-xml")


def test_detect_forced_vasp_structure_names() -> None:
    assert detect_preview(Path("POSCAR")) == ("structure", None)
    assert detect_preview(Path("poscar")) == ("structure", None)
    assert detect_preview(Path("POSCAR.relax")) == ("structure", None)
    assert detect_preview(Path("case_CONTCAR_001")) == ("structure", None)
    assert detect_preview(Path("XDATCAR")) == ("structure", None)
    assert detect_preview(Path("XDATCAR2")) == ("structure", None)
    assert detect_preview(Path("case_xdatcar_backup")) == ("structure", None)
    assert detect_preview(Path("OUTCAR")) == ("structure", None)
    assert detect_preview(Path("POSCAR.bak")) == ("structure", None)
    assert detect_preview(Path("notes_about_POSCAR.txt")) == ("structure", None)


def test_detect_text_types() -> None:
    assert detect_preview(Path("job.out")) == ("text", None)
    assert detect_preview(Path("INCAR")) == ("text", None)
    assert detect_preview(Path("config.xml")) == ("text", None)


def test_unknown_file_defaults_to_file() -> None:
    assert detect_preview(Path("archive.bin")) == ("file", None)
    assert detect_preview(Path("MYPOSCAR.bak")) == ("file", None)
