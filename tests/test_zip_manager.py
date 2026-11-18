import os
import zipfile
import pytest
from app.zip_manager import ZipCancelled, ZipError, create_zip, unique_arcnames


def mk(tmp, name, size):
    p = tmp / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(os.urandom(size))
    return str(p)


def test_zip_contents_progress_and_skips(tmp_path):
    a, b = mk(tmp_path, "a/v.mp4", 3_000_000), mk(tmp_path, "b/v.mp4", 10)   # same file name twice
    events = []
    res = create_zip([a, b, str(tmp_path / "gone.mp4")], tmp_path / "out" / "export",
                     progress=lambda d, t, n: events.append((d, t, n)))
    assert res["count"] == 2 and res["skipped"] == [str(tmp_path / "gone.mp4")]
    assert res["path"].endswith("export.zip") and os.path.getsize(res["path"]) == res["size"]
    with zipfile.ZipFile(res["path"]) as z:
        assert z.testzip() is None
        assert sorted(z.namelist()) == ["v (2).mp4", "v.mp4"]
        assert z.read("v.mp4") == open(a, "rb").read()
    assert events[-1][0] == events[-1][1] == 3_000_010
    assert not list((tmp_path / "out").glob("*.part"))


def test_existing_zip_is_not_overwritten(tmp_path):
    f = mk(tmp_path, "x.mp4", 100)
    r1 = create_zip([f], tmp_path / "e.zip")
    r2 = create_zip([f], tmp_path / "e.zip")
    assert r1["path"] != r2["path"] and os.path.exists(r1["path"]) and os.path.exists(r2["path"])


def test_not_enough_space_is_clear_and_leaves_nothing(tmp_path):
    f = mk(tmp_path, "x.mp4", 100)
    with pytest.raises(ZipError) as e:
        create_zip([f], tmp_path / "e.zip", free_space=lambda d: 10)
    assert "free space" in str(e.value) and not (tmp_path / "e.zip").exists()


def test_cancel_removes_partial_archive(tmp_path):
    f = mk(tmp_path, "big.mp4", 5_000_000)
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 2
    with pytest.raises(ZipCancelled):
        create_zip([f], tmp_path / "e.zip", should_cancel=cancel)
    assert list(tmp_path.glob("e.zip*")) == []


def test_disk_full_during_write_is_reported(tmp_path, monkeypatch):
    import errno
    f = mk(tmp_path, "x.mp4", 2_000_000)
    real = zipfile.ZipFile.open

    def boom(self, *a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(zipfile.ZipFile, "open", boom)
    with pytest.raises(ZipError) as e:
        create_zip([f], tmp_path / "e.zip")
    assert "disk became full" in str(e.value) and list(tmp_path.glob("e.zip*")) == []


def test_no_files_and_names():
    with pytest.raises(ZipError):
        create_zip([], "/tmp/none.zip")
    assert unique_arcnames(["/a/x.mp4", "/b/X.mp4", "/c/y.mp4"]) == ["x.mp4", "X (2).mp4", "y.mp4"]
