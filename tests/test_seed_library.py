"""Task 2.5 验收:种子库 CRUD/重名/批量入库/选用。"""

import pytest

from mj.clientd.seeds import SeedLibrary, safe_name
from mj.clientd.errors import ConflictError


@pytest.fixture
def lib(tmp_path):
    return SeedLibrary(str(tmp_path / "seeds"))


def _module_tmp(tmp_path_factory):
    return tmp_path_factory.mktemp("seedtmp")


def test_save_and_get(lib):
    lib.save("甲", 123, note="手工")
    assert lib.get("甲") == 123
    assert lib.get("missing") is None


def test_sanitize(lib):
    lib.save(" 玩家 ABC/1 ", 7)
    assert safe_name("__玩家__") == "玩家"


def test_overwrite_same_name(lib):
    lib.save("s", 1)
    lib.save("s", 2)          # 默认 overwrite=True
    assert lib.get("s") == 2
    with pytest.raises(ConflictError):
        lib.save("s", 3, overwrite=False)


def test_list_delete(lib):
    lib.save("a", 10)
    lib.save("b", 20)
    names = sorted(r["name"] for r in lib.list())
    assert names == ["a", "b"]
    lib.delete("a")
    assert lib.get("a") is None
    with pytest.raises(Exception):
        lib.delete("missing")


def test_save_batch(lib):
    recs = lib.save_batch([11, 22, 33], source="batch-x")
    assert [r["seed"] for r in recs] == [11, 22, 33]
    assert lib.get(recs[0]["name"]) == 11
    assert lib.get(recs[2]["name"]) == 33


def test_empty_name_rejected():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        lib = SeedLibrary(d)
        with pytest.raises(Exception):
            lib.save("   ", 1)