"""资产校验、分片恢复与归档边界的行为检查。"""
import hashlib
import io
import tarfile

import pytest

from engineering.assets import assemble, extract_archive, verify


def record(path, data):
    return {"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_split_checkpoint_is_reassembled_and_verified(tmp_path):
    first, second = b"model-header", b"model-parameters"
    (tmp_path / "one").write_bytes(first)
    (tmp_path / "two").write_bytes(second)
    spec = {**record("checkpoint", first + second),
            "parts": [record("one", first), record("two", second)]}
    assemble(spec, tmp_path)
    assert (tmp_path / "checkpoint").read_bytes() == first + second
    assemble(spec, tmp_path)
    (tmp_path / "checkpoint").write_bytes(b"changed")
    with pytest.raises(ValueError, match="摘要不符"):
        verify(tmp_path / "checkpoint", spec)


@pytest.mark.parametrize("name,kind", [("../escape", "file"), ("link", "symlink")])
def test_unsafe_archive_is_rejected_before_any_write(tmp_path, name, kind):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        good = tarfile.TarInfo("good")
        good.size = 2
        bundle.addfile(good, io.BytesIO(b"ok"))
        bad = tarfile.TarInfo(name)
        if kind == "symlink":
            bad.type = tarfile.SYMTYPE
            bad.linkname = "../escape"
        bundle.addfile(bad)
    with pytest.raises(ValueError):
        extract_archive(archive, tmp_path / "output")
    assert not (tmp_path / "output/good").exists()
    assert not (tmp_path / "escape").exists()


def test_existing_evidence_can_be_verified_but_never_overwritten(tmp_path):
    archive = tmp_path / "evidence.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo("case.json")
        member.size = 2
        bundle.addfile(member, io.BytesIO(b"{}"))
    output = tmp_path / "output"
    extract_archive(archive, output)
    extract_archive(archive, output)
    (output / "case.json").write_bytes(b"edited")
    with pytest.raises(ValueError, match="拒绝覆盖"):
        extract_archive(archive, output)
    assert (output / "case.json").read_bytes() == b"edited"
