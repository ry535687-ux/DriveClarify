import hashlib

import pytest

from driveclarify.tools.assets import assemble, checked_path, verify


def record(name, data):
    return {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_split_model_assembly_preserves_identity_and_rejects_tampering(tmp_path):
    first, second = b"header", b"parameters"
    (tmp_path / "one").write_bytes(first)
    (tmp_path / "two").write_bytes(second)
    spec = {**record("model", first + second),
            "parts": [record("one", first), record("two", second)]}
    assemble(spec, tmp_path)
    assert (tmp_path / "model").read_bytes() == first + second
    assemble(spec, tmp_path)
    (tmp_path / "model").write_bytes(b"changed")
    with pytest.raises(ValueError, match="摘要不符"):
        verify(tmp_path / "model", spec)


@pytest.mark.parametrize("partial", [b"hea", b"headerparameters"])
def test_interrupted_assembly_can_complete(tmp_path, partial):
    (tmp_path / "one").write_bytes(b"header")
    (tmp_path / "two").write_bytes(b"parameters")
    (tmp_path / "model.assembling").write_bytes(partial)
    spec = {**record("model", b"headerparameters"),
            "parts": [record("one", b"header"), record("two", b"parameters")]}
    assemble(spec, tmp_path)
    assert (tmp_path / "model").read_bytes() == b"headerparameters"
    assert not (tmp_path / "model.assembling").exists()


@pytest.mark.parametrize("name", ["../escape", "/escape", "a/../../escape", "a\\b"])
def test_download_target_cannot_escape_workspace(tmp_path, name):
    with pytest.raises(ValueError):
        checked_path(tmp_path, name)


def test_download_target_does_not_follow_existing_symlink(tmp_path):
    (tmp_path / "linked").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="符号链接"):
        checked_path(tmp_path, "linked/model")
