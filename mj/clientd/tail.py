"""jsonl 增量 tail 读取器。

Recorder 逐行 flush 到 local/games/*.jsonl;观战/房间流用本模块做
只读增量消费:按(文件, 字节偏移)从上次断点读出完整行,新文件自动发现,
文件被删则清理句柄。规避 UTF-8 多字节在文件尾被截断的坏解码:只在完整
换行边界提交数据,读到 EOF 且末尾无换行时保留同偏移等下次。

实现按"append-only + 偏移"重开句柄读:**不改用持久句柄**,这样 Windows
上文件被删也能 `os.remove`(无需关闭句柄),且偏移 + 截断检测(文件变短则
头部重读)对追加式日志正确。并发追加不变量:每次 poll 只读严格新增字节
→ 无丢行、无重复行。
"""

from __future__ import annotations

import glob
import os

__all__ = ["JsonlTail", "JsonlWatcher"]


class JsonlTail:
    """单文件增量 reader。from_start=False 时从当前大小开头(只读新增)。"""

    def __init__(self, path, from_start=True):
        self.path = os.path.normpath(path)
        if from_start:
            self._offset = 0
        else:
            self._offset = self._size()

    def _size(self):
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def read(self):
        """返回新出现的完整行列表(不含换行);无新增或文件消失返回 []。"""
        if not os.path.exists(self.path):
            return []
        size = self._size()
        if size < self._offset:
            # 文件被截断/重建:回退到头部重读
            self._offset = 0
        if size == self._offset:
            return []
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._offset)
                data = fh.read()
        except OSError:
            return []
        if not data:
            return []
        # 若末尾无换行,只取最后一个换行之前的部分,偏移不推进到未完行内
        if data[-1] != 0x0A:
            idx = data.rfind(b"\n")
            if idx == -1:
                return []
            data = data[: idx + 1]
        self._offset += len(data)
        lines = [seg for seg in data.split(b"\n") if seg]
        out = []
        for seg in lines:
            if seg.endswith(b"\r"):
                seg = seg[:-1]
            try:
                out.append(seg.decode("utf-8"))
            except UnicodeDecodeError:
                out.append(seg.decode("utf-8", "replace"))
        return out

    def close(self):
        pass


class JsonlWatcher:
    """目录级 jsonl 广播:轮询发现新文件并增量读每份文件行。

    poll() 返回 [(path, line,) ...];内层调用方再自行 json.parse。
    from_start=False 用于"只关注新写入"。文件被删自动清理。
    """

    def __init__(self, directory, pattern="*.jsonl", from_start=True):
        self.directory = os.path.normpath(directory)
        self.pattern = pattern
        self.from_start = from_start
        self._tails = {}

    def current_files(self):
        if not os.path.isdir(self.directory):
            return []
        path = os.path.join(self.directory, self.pattern)
        return sorted(os.path.normpath(p) for p in glob.glob(path))

    def poll(self):
        out = []
        files = set(self.current_files())
        for p in files:
            tail = self._tails.get(p)
            if tail is None:
                tail = JsonlTail(p, from_start=self.from_start)
                self._tails[p] = tail
            for line in tail.read():
                out.append((p, line))
        for p in list(self._tails):
            if p not in files:
                del self._tails[p]
        return out

    def close(self):
        self._tails.clear()