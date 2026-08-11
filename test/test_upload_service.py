"""文档上传加固服务行为测试（P1-3/P1-1，真实临时文件，不依赖 Milvus/docker）。

覆盖验收点：空文件、非法扩展名、超限文件、写入中断、路径型文件名和正常文件；
以及 file-to-chunk / 图谱导入的裸文件名解析（P1-1：绝对路径、盘符、UNC、
目录、`..`、软链接逃逸一律拒绝，只接受受控目录内的裸 file_id）。
"""

import asyncio
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from server.services import upload_service
from server.services.upload_service import (
    UploadError,
    build_stored_filename,
    resolve_upload_path,
    resolve_upload_paths,
    save_upload_stream_async,
)


@contextmanager
def _temp_upload_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


class _FakeFile:
    """模拟 starlette UploadFile 的最小接口：filename + async read(size)。"""

    def __init__(self, filename, data=b"", fail_after=None):
        self.filename = filename
        self._data = data
        self._pos = 0
        self._fail_after = fail_after

    async def read(self, size=-1):
        if self._fail_after is not None and self._pos >= self._fail_after:
            raise OSError("模拟读取中断")
        if size is None or size < 0:
            size = len(self._data) - self._pos
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _list_files(dirpath):
    return [p for p in Path(dirpath).iterdir() if p.is_file()]


class UploadServiceBehaviorTests(unittest.TestCase):
    def test_normal_upload_streams_and_stores_atomically(self):
        data = b"hello knowledge base" * 100
        with _temp_upload_dir() as d:
            stored, size = _run(save_upload_stream_async(_FakeFile("guide.txt", data), d))
            self.assertEqual(size, len(data))
            self.assertTrue(stored.endswith(".txt"))
            # 存储名是裸文件名，不含任何路径成分
            self.assertEqual(Path(stored).parts, (stored,))
            self.assertEqual((d / stored).read_bytes(), data)
            # 不留临时 .part 文件
            self.assertEqual([p.name for p in _list_files(d)], [stored])

    def test_empty_file_rejected_and_cleaned(self):
        with _temp_upload_dir() as d:
            with self.assertRaises(UploadError) as ctx:
                _run(save_upload_stream_async(_FakeFile("empty.txt", b""), d))
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(_list_files(d), [])

    def test_illegal_extension_rejected(self):
        with _temp_upload_dir() as d:
            with self.assertRaises(UploadError) as ctx:
                _run(save_upload_stream_async(_FakeFile("malware.exe", b"x" * 10), d))
            self.assertEqual(ctx.exception.status_code, 415)
            self.assertEqual(_list_files(d), [])

    def test_missing_extension_rejected(self):
        with self.assertRaises(UploadError) as ctx:
            build_stored_filename("README")
        self.assertEqual(ctx.exception.status_code, 415)

    def test_path_type_filenames_rejected(self):
        with _temp_upload_dir() as d:
            for bad in ("/etc/passwd", "../../etc/passwd", "a/b.txt", r"C:\fakepath\x.txt"):
                with self.subTest(bad=bad):
                    with self.assertRaises(UploadError) as ctx:
                        _run(save_upload_stream_async(_FakeFile(bad, b"x"), d))
                    self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(_list_files(d), [])

    def test_oversized_upload_rejected_and_temp_removed(self):
        with _temp_upload_dir() as d:
            # 上限压到 ~1KB，上传 2KB
            with mock.patch.dict(os.environ, {"MAX_UPLOAD_SIZE_MB": "0.001"}):
                with self.assertRaises(UploadError) as ctx:
                    _run(save_upload_stream_async(_FakeFile("big.csv", b"y" * 2048), d))
            self.assertEqual(ctx.exception.status_code, 413)
            # 临时文件与最终文件都不得残留
            self.assertEqual(_list_files(d), [])

    def test_write_interruption_cleans_temp_and_raises(self):
        with _temp_upload_dir() as d:
            with mock.patch.object(upload_service.os, "replace", side_effect=OSError("写盘失败")):
                with self.assertRaises(UploadError) as ctx:
                    _run(save_upload_stream_async(_FakeFile("doc.md", b"z" * 5000), d))
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertEqual(_list_files(d), [])

    def test_read_interruption_cleans_temp_and_raises(self):
        with _temp_upload_dir() as d:
            fake = _FakeFile("doc.md", b"z" * 5000, fail_after=1024)
            with self.assertRaises(UploadError) as ctx:
                _run(save_upload_stream_async(fake, d))
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertEqual(_list_files(d), [])

    def test_max_upload_bytes_falls_back_on_bad_env(self):
        with mock.patch.dict(os.environ, {"MAX_UPLOAD_SIZE_MB": "abc"}, clear=False):
            self.assertEqual(
                upload_service.max_upload_bytes(),
                100 * 1024 * 1024,
            )
        with mock.patch.dict(os.environ, {"MAX_UPLOAD_SIZE_MB": "-3"}, clear=False):
            self.assertEqual(
                upload_service.max_upload_bytes(),
                100 * 1024 * 1024,
            )

    def test_allowed_extensions_normalized_from_env(self):
        with mock.patch.dict(os.environ, {"UPLOAD_ALLOWED_EXTENSIONS": "pdf, .md,json"}, clear=False):
            exts = upload_service.allowed_extensions()
            self.assertEqual(exts, {".pdf", ".md", ".json"})

    def test_allowed_extensions_empty_env_uses_default(self):
        """Compose 用 ${VAR:-} 透传时未设置会变成空串，必须回退默认白名单。"""
        with mock.patch.dict(os.environ, {"UPLOAD_ALLOWED_EXTENSIONS": ""}, clear=False):
            exts = upload_service.allowed_extensions()
            self.assertGreater(len(exts), 0)
            self.assertIn(".txt", exts)
            self.assertIn(".pdf", exts)


class ResolveUploadPathTests(unittest.TestCase):
    def test_bare_name_resolves_within_upload_dir(self):
        with _temp_upload_dir() as d:
            (d / "guide.txt").write_text("abc", encoding="utf-8")
            got = resolve_upload_paths(d, ["guide.txt"])
            self.assertEqual(got, [str(d / "guide.txt")])

    def test_missing_file_raises_404(self):
        with _temp_upload_dir() as d:
            with self.assertRaises(UploadError) as ctx:
                resolve_upload_paths(d, ["nope.txt"])
            self.assertEqual(ctx.exception.status_code, 404)

    def test_traversal_and_path_names_rejected(self):
        with _temp_upload_dir() as d:
            for bad in ("../secret.txt", "a/b.txt", r"..\secret.txt"):
                with self.subTest(bad=bad):
                    with self.assertRaises(UploadError) as ctx:
                        resolve_upload_paths(d, [bad])
                    self.assertEqual(ctx.exception.status_code, 400)

    def test_absolute_path_rejected(self):
        """绝对路径必须被拒绝，解析器不得把越界路径送入文件解析（P1-1）。"""
        with _temp_upload_dir() as d:
            (d / "abs.txt").write_text("x", encoding="utf-8")
            for bad in ("/etc/passwd", str(d / "abs.txt")):
                with self.subTest(bad=bad):
                    with self.assertRaises(UploadError) as ctx:
                        resolve_upload_paths(d, [bad])
                    self.assertEqual(ctx.exception.status_code, 400)

    def test_windows_drive_and_unc_rejected(self):
        with _temp_upload_dir() as d:
            for bad in (
                r"C:\Windows\win.ini",
                r"C:/Windows/win.ini",
                r"\\server\share\doc.txt",
                r"//server/share/doc.txt",
            ):
                with self.subTest(bad=bad):
                    with self.assertRaises(UploadError) as ctx:
                        resolve_upload_paths(d, [bad])
                    self.assertEqual(ctx.exception.status_code, 400)

    def test_symlink_escape_rejected(self):
        """上传目录外软链接必须被拒绝，不得把目标目录外文件送入解析器。"""
        with _temp_upload_dir() as d, tempfile.TemporaryDirectory() as outside:
            outside_path = Path(outside)
            outside_path.joinpath("secret.txt").write_text("s", encoding="utf-8")
            try:
                os.symlink(outside_path / "secret.txt", d / "link.txt")
            except (OSError, NotImplementedError):
                self.skipTest("当前平台不支持创建软链接")
            with self.assertRaises(UploadError) as ctx:
                resolve_upload_paths(d, ["link.txt"])
            self.assertEqual(ctx.exception.status_code, 400)

    def test_directory_name_rejected(self):
        with _temp_upload_dir() as d:
            (d / "sub").mkdir()
            with self.assertRaises(UploadError) as ctx:
                resolve_upload_paths(d, ["sub"])
            self.assertEqual(ctx.exception.status_code, 400)

    def test_general_dir_fallback(self):
        """upload_dir 不存在时回退到通用上传目录（图谱导入等场景）。"""
        with _temp_upload_dir() as up, _temp_upload_dir() as general:
            (general / "graph.jsonl").write_text("{}", encoding="utf-8")
            got = resolve_upload_path(up, "graph.jsonl", general_dir=general)
            self.assertEqual(got, str(general / "graph.jsonl"))

    def test_priority_prefers_upload_dir_over_general_dir(self):
        with _temp_upload_dir() as up, _temp_upload_dir() as general:
            (up / "same.txt").write_text("upload", encoding="utf-8")
            (general / "same.txt").write_text("general", encoding="utf-8")
            got = resolve_upload_path(up, "same.txt", general_dir=general)
            self.assertEqual(got, str(up / "same.txt"))


class FileParseHttpInputTests(unittest.TestCase):
    """HTTP 请求级输入回归（P1-1）：与 data_router 的 file_to_chunk / 图谱导入
    完全一致的解析入口（resolve_upload_path(s)），输入来自请求体原始字符串。
    覆盖验收点：绝对路径、目录穿越、合法 file_id 三个真实请求场景。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.upload_dir = Path(self._tmp.name) / "uploads"
        self.upload_dir.mkdir()
        self.general_dir = Path(self._tmp.name) / "general"
        self.general_dir.mkdir()
        (self.upload_dir / "kb_doc.txt").write_text("kb", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_http_absolute_path_input_rejected(self):
        """请求传入 /etc/passwd 或 C:\\Windows\\win.ini → 400，解析器未收到越界路径。"""
        for bad in ("/etc/passwd", r"C:\Windows\win.ini"):
            with self.subTest(bad=bad):
                with self.assertRaises(UploadError) as ctx:
                    resolve_upload_path(self.upload_dir, bad, general_dir=self.general_dir)
                self.assertEqual(ctx.exception.status_code, 400)

    def test_http_traversal_input_rejected(self):
        """请求传入 ../ 或 ..\\ 穿越 → 400。"""
        for bad in ("../outside.txt", r"..\outside.txt", "a/../b.txt"):
            with self.subTest(bad=bad):
                with self.assertRaises(UploadError) as ctx:
                    resolve_upload_path(self.upload_dir, bad, general_dir=self.general_dir)
                self.assertEqual(ctx.exception.status_code, 400)

    def test_http_valid_file_id_resolves_inside_controlled_dir(self):
        """合法 file_id 解析成功，且结果位于受控上传目录内。"""
        got = resolve_upload_path(self.upload_dir, "kb_doc.txt", general_dir=self.general_dir)
        self.assertEqual(got, str(self.upload_dir / "kb_doc.txt"))

    def test_http_batch_mixed_input_fails_wholesale(self):
        """批量解析中任一非法输入整体失败（与 file-to-chunk 整批语义一致）。"""
        with self.assertRaises(UploadError) as ctx:
            resolve_upload_paths(
                self.upload_dir, ["kb_doc.txt", "../escape.txt"], general_dir=self.general_dir
            )
        self.assertEqual(ctx.exception.status_code, 400)


class UploadRouterSourceTests(unittest.TestCase):
    """源码级配套断言：路由不再整体读入内存、不再返回绝对路径（行为已在服务层验证）。"""

    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.src = (root / "server" / "routers" / "data_router.py").read_text(encoding="utf-8")

    def test_upload_uses_streaming_service(self):
        self.assertIn("save_upload_stream_async(file, upload_dir)", self.src)
        self.assertNotIn("buffer.write(await file.read())", self.src)

    def test_upload_response_has_no_absolute_path(self):
        self.assertIn('"file_id": stored_name', self.src)
        self.assertNotIn('"file_path": file_path', self.src)
        self.assertNotIn('"file_path":', self.src)

    def test_file_to_chunk_resolves_stored_names(self):
        self.assertIn("resolve_upload_paths(upload_dir, files, general_dir=GENERAL_UPLOAD_DIR)", self.src)

    def test_graph_import_reuses_safe_resolver(self):
        # 图谱导入端点不得各自拼接路径，必须复用同一个安全解析函数
        self.assertIn("resolve_upload_path(GENERAL_UPLOAD_DIR, file_path)", self.src)
        self.assertNotIn("ROOT_DIR / file_path", self.src)


if __name__ == "__main__":
    unittest.main()
