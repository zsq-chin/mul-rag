"""阶段 D1：远端图片目录「真正的服务端分页 + 缩略图生命周期」单测。

覆盖对象是 `mul_rag/backend/services/image_catalog.py`（远端数据源进程内的分页实现）。
SAGE 端不在此处测试；SAGE 代理的严格分页语义在 test_multimodal_remote /
test_multimodal_contract 中验证。

验证：
- 分页只在数据源侧切片，响应固定 items/page/pageSize/total；pageSize 上限 100；
- 稳定排序，翻页不重复、不遗漏；
- 列表条目只含轻量元数据，不含 Base64 / 绝对路径 / 全文 / 向量；
- 缩略图按需生成 320px 缓存，源图更新后重新生成；ETag 随源图更新而变；
- 图片名 / kbId 路径安全（拒绝穿越、绝对路径、盘符、UNC、NUL）。
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from mul_rag.backend.services.image_catalog import (
    SUMMARY_PREVIEW_CHARS,
    THUMB_MAX_WIDTH,
    coerce_int,
    ensure_thumbnail,
    image_summary_preview,
    make_image_etag,
    paginate_kb_images,
    resolve_image_for_serve,
    safe_kb_id,
    source_image_path,
    thumb_dir,
)


def _write_png(path: Path, size: int = 64):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (size, size), (120, 60, 200)).save(path, "PNG")


def _summary_item(name, page, text, extra=None):
    item = {"img_name": name, "summary": text, "page_num": page}
    if extra:
        item.update(extra)
    return item


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # 三个文件，共 30 张图片，页码故意乱序以验证稳定排序
        base = self.root / "kb-1" / "files"
        for idx, file_id in enumerate(("file-A", "file-B", "file-C")):
            fdir = base / file_id
            fdir.mkdir(parents=True, exist_ok=True)
            (fdir / ("doc-%d.pdf" % idx)).write_text("x", encoding="utf-8")
            summaries = []
            for n in range(10):
                summaries.append(
                    _summary_item(f"img{n}.png", (9 - n), f"第{n}张图片的摘要")
                )
            (fdir / "image_summaries.json").write_text(
                json.dumps(summaries), encoding="utf-8"
            )

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_page_returns_only_current_page(self):
        page = paginate_kb_images(self.root, "kb-1", page=1, page_size=24)
        self.assertEqual(page["total"], 30)
        self.assertEqual(page["page"], 1)
        self.assertEqual(page["pageSize"], 24)
        self.assertEqual(len(page["items"]), 24)

    def test_second_page_returns_remainder(self):
        page = paginate_kb_images(self.root, "kb-1", page=2, page_size=24)
        self.assertEqual(page["total"], 30)
        self.assertEqual(len(page["items"]), 6)

    def test_stable_sort_no_dup_no_miss_across_pages(self):
        all_items = []
        seen = set()
        page = 1
        while True:
            result = paginate_kb_images(self.root, "kb-1", page=page, page_size=7)
            for item in result["items"]:
                key = (item["fileName"], item["page_num"], item["img_name"])
                self.assertNotIn(key, seen, f"翻页出现重复条目: {key}")
                seen.add(key)
                all_items.append(item)
            if page * 7 >= result["total"]:
                break
            page += 1
        self.assertEqual(len(all_items), 30)
        # 排序稳定：fileName → page_num → img_name
        keys = [(i["fileName"], i["page_num"], i["img_name"]) for i in all_items]
        self.assertEqual(keys, sorted(keys))

    def test_page_size_capped_at_100(self):
        result = paginate_kb_images(self.root, "kb-1", page=1, page_size=5000)
        self.assertEqual(result["pageSize"], 100)

    def test_page_size_min_is_1(self):
        result = paginate_kb_images(self.root, "kb-1", page=1, page_size=0)
        self.assertEqual(result["pageSize"], 1)
        self.assertEqual(len(result["items"]), 1)

    def test_page_out_of_range_returns_empty_items(self):
        result = paginate_kb_images(self.root, "kb-1", page=999, page_size=24)
        self.assertEqual(result["total"], 30)
        self.assertEqual(result["items"], [])

    def test_missing_kb_returns_empty_catalog(self):
        result = paginate_kb_images(self.root, "no-such-kb")
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["items"], [])

    def test_empty_kb_returns_empty_catalog(self):
        (self.root / "empty-kb" / "files").mkdir(parents=True)
        result = paginate_kb_images(self.root, "empty-kb")
        self.assertEqual(result["total"], 0)

    def test_traversal_kb_id_returns_empty_catalog(self):
        # kbId 不能带路径分隔符，防止目录穿越
        result = paginate_kb_images(self.root, "../etc")
        self.assertEqual(result["total"], 0)


class LightMetadataTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        base = self.root / "kb-1" / "files" / "file-A"
        base.mkdir(parents=True, exist_ok=True)
        (base / "doc.pdf").write_text("x", encoding="utf-8")
        self.base = base

    def tearDown(self):
        self._tmp.cleanup()

    def _paginate(self):
        return paginate_kb_images(self.root, "kb-1", page=1, page_size=24)

    def test_items_only_contain_lightweight_keys(self):
        long_text = "长" * 5000
        summaries = [
            _summary_item("a.png", 1, long_text, extra={"image_base64": "iVBORw0KGgoAAA=="}),
            _summary_item("b.png", 2, "普通摘要"),
        ]
        (self.base / "image_summaries.json").write_text(
            json.dumps(summaries), encoding="utf-8"
        )
        items = self._paginate()["items"]
        allowed = {"img_name", "summary", "page_num", "source_page_num", "original_img_name", "fileId", "fileName"}
        for item in items:
            self.assertTrue(set(item.keys()) <= allowed, f"出现不允许的字段: {item}")
            serialized = json.dumps(item)
            self.assertNotIn("iVBORw0KGgoAAA==", serialized, "泄漏 Base64 内容")
            self.assertNotIn("D:\\", serialized)
            self.assertNotIn("http://", serialized)
            self.assertNotIn("vector", serialized)

    def test_summary_is_truncated_to_preview(self):
        long_text = "内容" * 1000
        summaries = [_summary_item("a.png", 1, long_text)]
        (self.base / "image_summaries.json").write_text(
            json.dumps(summaries), encoding="utf-8"
        )
        item = self._paginate()["items"][0]
        self.assertLessEqual(len(item["summary"]), SUMMARY_PREVIEW_CHARS + 1)
        self.assertIn("…", item["summary"])

    def test_absolute_path_in_summary_not_leaked(self):
        summaries = [_summary_item("a.png", 1, "路径 D:\\remote\\data\\secret.png")]
        (self.base / "image_summaries.json").write_text(
            json.dumps(summaries), encoding="utf-8"
        )
        item = self._paginate()["items"][0]
        self.assertNotIn("D:\\remote\\data\\secret.png", json.dumps(item))

    def test_fallback_walks_images_dir_when_no_summaries(self):
        _write_png(self.base / "images" / "page1_img1.png")
        _write_png(self.base / "images" / "page2_img2.png")
        (self.base / "image_captions.json").write_text(
            json.dumps({"page1_img1.png": "图1说明"}), encoding="utf-8"
        )
        items = self._paginate()["items"]
        self.assertEqual(len(items), 2)
        by_name = {i["img_name"]: i for i in items}
        self.assertEqual(by_name["page1_img1.png"]["summary"], "图1说明")
        self.assertEqual(by_name["page2_img2.png"]["page_num"], 2)

    def test_file_name_and_id_injected(self):
        summaries = [_summary_item("a.png", 1, "x")]
        (self.base / "image_summaries.json").write_text(
            json.dumps(summaries), encoding="utf-8"
        )
        item = self._paginate()["items"][0]
        self.assertEqual(item["fileId"], "file-A")
        self.assertEqual(item["fileName"], "doc.pdf")


class ThumbnailTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.base = self.root / "kb-1" / "files" / "file-A"
        _write_png(self.base / "images" / "page1_img1.png", size=800)
        self.source = self.base / "images" / "page1_img1.png"

    def tearDown(self):
        self._tmp.cleanup()

    def test_thumbnail_generated_and_bounded_width(self):
        from PIL import Image

        thumb = ensure_thumbnail(self.root, "kb-1", "file-A", "page1_img1.png")
        self.assertIsNotNone(thumb)
        self.assertTrue(thumb.exists())
        with Image.open(thumb) as im:
            self.assertLessEqual(im.width, THUMB_MAX_WIDTH)
            self.assertEqual(im.format, "JPEG")

    def test_thumbnail_reused_when_source_unchanged(self):
        first = ensure_thumbnail(self.root, "kb-1", "file-A", "page1_img1.png")
        first_stat = first.stat()
        second = ensure_thumbnail(self.root, "kb-1", "file-A", "page1_img1.png")
        self.assertEqual(first, second)
        self.assertEqual(first.stat().st_mtime_ns, first_stat.st_mtime_ns)

    def test_thumbnail_regenerated_when_source_newer(self):
        first = ensure_thumbnail(self.root, "kb-1", "file-A", "page1_img1.png")
        first_mtime = first.stat().st_mtime_ns
        time.sleep(0.02)
        os.utime(self.source, ns=(time.time_ns() + 1_000_000_000, time.time_ns() + 1_000_000_000))
        second = ensure_thumbnail(self.root, "kb-1", "file-A", "page1_img1.png")
        self.assertGreater(second.stat().st_mtime_ns, first_mtime)

    def test_etag_changes_when_source_updates(self):
        thumb = ensure_thumbnail(self.root, "kb-1", "file-A", "page1_img1.png")
        etag_before = make_image_etag(thumb, thumb=True)
        time.sleep(0.02)
        os.utime(self.source, ns=(time.time_ns() + 1_000_000_000, time.time_ns() + 1_000_000_000))
        thumb2 = ensure_thumbnail(self.root, "kb-1", "file-A", "page1_img1.png")
        etag_after = make_image_etag(thumb2, thumb=True)
        self.assertNotEqual(etag_before, etag_after, "源图更新后缩略图 ETag 必须变化")

    def test_etag_stable_while_unchanged(self):
        thumb = ensure_thumbnail(self.root, "kb-1", "file-A", "page1_img1.png")
        self.assertEqual(make_image_etag(thumb, thumb=True), make_image_etag(thumb, thumb=True))

    def test_thumb_and_orig_etag_differ(self):
        thumb = ensure_thumbnail(self.root, "kb-1", "file-A", "page1_img1.png")
        self.assertNotEqual(
            make_image_etag(self.source, thumb=False),
            make_image_etag(thumb, thumb=True),
        )

    def test_missing_file_etag_is_empty(self):
        missing = self.base / "nope.png"
        self.assertEqual(make_image_etag(missing, thumb=False), '""')

    def test_resolve_thumb_returns_thumbnail_path(self):
        path, is_thumb = resolve_image_for_serve(self.root, "kb-1", "file-A", "page1_img1.png", thumb=True)
        self.assertTrue(is_thumb)
        self.assertIn("thumbs", str(path))

    def test_resolve_orig_returns_source(self):
        path, is_thumb = resolve_image_for_serve(self.root, "kb-1", "file-A", "page1_img1.png", thumb=False)
        self.assertFalse(is_thumb)
        self.assertEqual(path.resolve(), self.source.resolve())

    def test_resolve_missing_returns_none(self):
        path, is_thumb = resolve_image_for_serve(self.root, "kb-1", "file-A", "missing.png", thumb=True)
        self.assertIsNone(path)
        self.assertFalse(is_thumb)


class ImagePathSafetyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_rejects_traversal_and_absolute(self):
        bad = [
            "../secret.png",
            "..%2Fsecret.png",
            "/etc/passwd",
            "C:\\Windows\\system32\\evil.png",
            "C:/Windows/evil.png",
            "//server/share/evil.png",
            "\\\\server\\share\\evil.png",
            "http://evil.example/x.png",
            "file:///etc/passwd",
            "a\x00b.png",
            "sub/../evil.png",
        ]
        for candidate in bad:
            self.assertIsNone(
                source_image_path(self.root, "kb-1", "file-A", candidate),
                f"应拒绝不安全图片名: {candidate!r}",
            )

    def test_accepts_plain_image_name(self):
        path = source_image_path(self.root, "kb-1", "file-A", "page1_img1.png")
        self.assertEqual(path, self.root / "kb-1" / "files" / "file-A" / "images" / "page1_img1.png")

    def test_safe_kb_id_rejects_separators(self):
        for bad in ("../etc", "a/b", "a\\b", "C:kb", "http://x", "/abs", "a\x00b", ".."):
            self.assertIsNone(safe_kb_id(bad), f"应拒绝 kbId: {bad!r}")
        self.assertEqual(safe_kb_id("钻井设计资料"), "钻井设计资料")

    def test_thumb_dir_sanitizes_ids(self):
        tdir = thumb_dir(self.root, "a/b", "c\\d")
        self.assertEqual(tdir.name, "thumbs")
        self.assertIn("__invalid__", str(tdir))


class CoerceIntTests(unittest.TestCase):
    def test_invalid_falls_back_to_default(self):
        self.assertEqual(coerce_int("abc", 24, 1, 100), 24)
        self.assertEqual(coerce_int(None, 24, 1, 100), 24)

    def test_clamps_to_bounds(self):
        self.assertEqual(coerce_int(0, 24, 1, 100), 1)
        self.assertEqual(coerce_int(999, 24, 1, 100), 100)


if __name__ == "__main__":
    unittest.main()
