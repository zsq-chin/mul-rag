"""阶段 B3：固定代理白名单与请求/响应边界策略的回归测试。

验证：
- 远端 `/api/multimodal/**` 不再存在全路径通用代理，只有“HTTP 方法 + 固定路径”
  白名单；每条接口显式定义权限、请求模型、超时、响应类型和大小限制。
- 检索接口允许任意已登录用户；创建/删除/上传/解析/索引等管理接口仅超级管理员。
- 上传按文件数、单文件大小、总大小、扩展名和 MIME 校验；流式转发不在内存整读。
- 上游错误映射（400/422→400、404、429/503、其余 5xx→502）。
- 只转发允许的响应头；拒绝 Set-Cookie/Location/Content-Type 透传；流式响应
  拒绝 application/json、text/html 等错误 Content-Type。
- JSON 响应/请求体体积上限。

本测试不 import server.routers，只测试 utils 层纯策略。
"""

import unittest

from server.utils.multimodal_remote import (
    MAX_JSON_RESPONSE_BYTES,
    MULTIMODAL_PROXY_WHITELIST,
    BODY_JSON,
    BODY_MULTIPART,
    BODY_NONE,
    PERMISSION_ADMIN,
    PERMISSION_READ,
    RESPONSE_JSON,
    RESPONSE_STREAM,
    MultimodalUploadError,
    accumulate_bounded_bytes,
    filter_multimodal_response_headers,
    map_upstream_proxy_status,
    route_spec_for,
    validate_stream_content_type,
    validate_upload_metadata,
    whitelisted_proxy_routes,
)


class WhitelistCompletenessTests(unittest.TestCase):
    def test_no_catch_all_route_spec(self):
        # B3.1：不存在 /{path:path} 通配条目，路径是固定模板
        for (method, path) in MULTIMODAL_PROXY_WHITELIST:
            self.assertNotIn("{", path)
            self.assertNotIn("}", path)
            self.assertNotIn("*", path)
        self.assertIsNone(route_spec_for("GET", "anything"))
        self.assertIsNone(route_spec_for("PUT", "kb/list"))

    def test_only_get_and_post_methods_whitelisted(self):
        # 删除通用代理后不允许任意方法透传
        for (method, _path) in MULTIMODAL_PROXY_WHITELIST:
            self.assertIn(method, {"GET", "POST"})

    def test_all_frontend_called_endpoints_present(self):
        # 管理页 multimodal.js 实际调用的接口必须在白名单内
        required = [
            ("GET", "health"),
            ("GET", "kb/list"),
            ("POST", "kb/create"),
            ("POST", "kb/delete"),
            ("GET", "kb/files"),
            ("GET", "kb/images"),
            ("GET", "kb/file/original"),
            ("GET", "kb/file/dataframe"),
            ("GET", "kb/file/content"),
            ("POST", "kb/file/delete"),
            ("POST", "kb/image/update"),
            ("POST", "kb/images/update"),
            ("GET", "file-manager/wells"),
            ("POST", "pdf/upload"),
            ("POST", "pdf/parse"),
            ("GET", "pdf/status"),
            ("GET", "pdf/images"),
            ("GET", "pdf/images_list"),
            ("GET", "pdf/image_summaries"),
            ("POST", "pdf/image_summaries/update"),
            ("GET", "pdf/chunk"),
            ("GET", "pdf/page"),
            ("POST", "index/build"),
            ("POST", "index/search"),
            ("GET", "index/chunks"),
            ("GET", "index/chunks/stats"),
            ("POST", "index/delete"),
            ("POST", "extraction/extract"),
            ("GET", "extraction/status"),
            ("GET", "extraction/content"),
            ("GET", "extraction/check_filename"),
            ("POST", "extraction/update_result"),
            ("GET", "extraction/image"),
            ("GET", "preprocess/methods"),
            ("POST", "preprocess/upload"),
            ("POST", "preprocess/workbench/run"),
            ("POST", "preprocess/workbench/grouped/run"),
            ("POST", "preprocess/workbench/store"),
            ("GET", "preprocess/workbench/dataframe"),
            ("GET", "preprocess/workbench/download"),
            ("GET", "preprocess/workbench/artifact/download"),
            ("POST", "preprocess/run"),
            ("GET", "preprocess/report"),
            ("GET", "preprocess/dataframe"),
            ("GET", "structured-db/supported"),
            ("GET", "structured-db/connections"),
            ("POST", "structured-db/connect"),
            ("POST", "structured-db/disconnect"),
            ("GET", "structured-db/schema"),
            ("GET", "structured-db/table"),
            ("POST", "structured-db/query"),
            ("POST", "query"),
        ]
        for item in required:
            self.assertIsNotNone(
                route_spec_for(*item),
                f"白名单缺少前端实际调用的接口: {item}",
            )

    def test_dead_code_chat_endpoints_not_whitelisted(self):
        # B3：multimodalChat('/chat') / clearMultimodalChat('/chat/clear') 是死代码，
        # 不得重新开放远端问答入口
        self.assertIsNone(route_spec_for("POST", "chat"))
        self.assertIsNone(route_spec_for("POST", "chat/clear"))


class WhitelistPermissionTests(unittest.TestCase):
    def test_search_is_read_for_logged_in_users(self):
        # B3.3：检索使用接口由已登录用户访问
        spec = route_spec_for("POST", "index/search")
        self.assertEqual(spec.permission, PERMISSION_READ)

    def test_management_routes_are_superadmin_only(self):
        # B3.3：创建/删除/上传/解析/索引等管理接口只允许超级管理员
        admin_routes = [
            ("POST", "kb/create"),
            ("POST", "kb/delete"),
            ("POST", "pdf/upload"),
            ("POST", "pdf/parse"),
            ("POST", "extraction/extract"),
            ("POST", "preprocess/upload"),
            ("POST", "index/build"),
            ("POST", "index/delete"),
            ("POST", "kb/image/update"),
            ("GET", "kb/list"),
            ("GET", "kb/images"),
            ("GET", "pdf/images"),
        ]
        for method, path in admin_routes:
            spec = route_spec_for(method, path)
            self.assertIsNotNone(spec)
            self.assertEqual(
                spec.permission, PERMISSION_ADMIN,
                f"{method} {path} 必须是超级管理员权限",
            )

    def test_read_only_route_is_exactly_search(self):
        # 普通用户唯一可用的 /api/multimodal 代理接口是检索
        read_routes = [
            (m, p) for (m, p), spec in MULTIMODAL_PROXY_WHITELIST.items()
            if spec.permission == PERMISSION_READ
        ]
        self.assertEqual(read_routes, [("POST", "index/search")])


class RouteSpecDefinitionTests(unittest.TestCase):
    def test_every_spec_defines_boundary_fields(self):
        # B3.2：每条接口显式定义权限、请求模型、大小限制、超时和响应类型
        for (method, path), spec in MULTIMODAL_PROXY_WHITELIST.items():
            self.assertEqual(spec.method, method)
            self.assertEqual(spec.path, path)
            self.assertIn(spec.body, {BODY_NONE, BODY_JSON, BODY_MULTIPART})
            self.assertIn(spec.response, {RESPONSE_JSON, RESPONSE_STREAM})
            self.assertGreater(spec.timeout_seconds, 0)
            self.assertGreater(spec.max_response_bytes, 0)

    def test_json_routes_have_bounded_response(self):
        # JSON 响应必须有体积上限
        for (method, path), spec in MULTIMODAL_PROXY_WHITELIST.items():
            if spec.response == RESPONSE_JSON:
                self.assertGreaterEqual(spec.max_response_bytes, 1024)
                self.assertLessEqual(spec.max_response_bytes, MAX_JSON_RESPONSE_BYTES * 4)

    def test_upload_routes_define_limits(self):
        upload_paths = [
            ("POST", "pdf/upload"),
            ("POST", "extraction/extract"),
            ("POST", "preprocess/upload"),
        ]
        for method, path in upload_paths:
            spec = route_spec_for(method, path)
            self.assertEqual(spec.body, BODY_MULTIPART)
            self.assertGreaterEqual(spec.max_files, 1)
            self.assertGreater(spec.max_file_bytes, 0)
            self.assertGreaterEqual(spec.max_total_bytes, spec.max_file_bytes)
            self.assertTrue(spec.allowed_extensions)


class UploadValidationTests(unittest.TestCase):
    def _spec(self):
        spec = route_spec_for("POST", "pdf/upload")
        assert spec is not None
        return spec

    def test_valid_pdf_upload_passes(self):
        spec = self._spec()
        validate_upload_metadata([("钻井设计.pdf", "application/pdf", 4096)], spec)

    def test_extension_not_allowed_rejected(self):
        with self.assertRaises(MultimodalUploadError) as ctx:
            validate_upload_metadata([("payload.exe", "application/octet-stream", 10)], self._spec())
        self.assertEqual(ctx.exception.status_code, 400)

    def test_mime_not_allowed_rejected(self):
        with self.assertRaises(MultimodalUploadError) as ctx:
            validate_upload_metadata([("a.pdf", "text/html", 10)], self._spec())
        self.assertEqual(ctx.exception.status_code, 400)

    def test_octet_stream_fallback_allowed(self):
        validate_upload_metadata([("well.las", "application/octet-stream", 10)], self._spec())

    def test_per_file_size_limit_rejected(self):
        spec = self._spec()
        with self.assertRaises(MultimodalUploadError) as ctx:
            validate_upload_metadata(
                [("big.pdf", "application/pdf", spec.max_file_bytes + 1)], spec,
            )
        self.assertEqual(ctx.exception.status_code, 413)

    def test_total_size_limit_rejected(self):
        spec = self._spec()
        # 3 个单文件都不超限、但总和超过 max_total_bytes 时拒绝
        with self.assertRaises(MultimodalUploadError) as ctx:
            validate_upload_metadata(
                [("f.pdf", "application/pdf", spec.max_file_bytes)] * 3,
                spec,
            )
        self.assertEqual(ctx.exception.status_code, 413)

    def test_too_many_files_rejected(self):
        spec = self._spec()
        metas = [("f.pdf", "application/pdf", 1)] * (spec.max_files + 1)
        with self.assertRaises(MultimodalUploadError) as ctx:
            validate_upload_metadata(metas, spec)
        self.assertEqual(ctx.exception.status_code, 413)


class UpstreamErrorMappingTests(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(map_upstream_proxy_status(200), (200, ""))
        self.assertEqual(map_upstream_proxy_status(204), (204, ""))
        self.assertEqual(map_upstream_proxy_status(400)[0], 400)
        self.assertEqual(map_upstream_proxy_status(422)[0], 400)
        self.assertEqual(map_upstream_proxy_status(404)[0], 404)
        self.assertEqual(map_upstream_proxy_status(429)[0], 429)
        self.assertEqual(map_upstream_proxy_status(503)[0], 503)
        self.assertEqual(map_upstream_proxy_status(500)[0], 502)
        self.assertEqual(map_upstream_proxy_status(502)[0], 502)


class StreamContentTypeTests(unittest.TestCase):
    def test_allowed_types(self):
        for ct in ("image/png", "application/pdf", "application/octet-stream",
                   "text/csv", "text/plain; charset=utf-8"):
            self.assertTrue(validate_stream_content_type(ct), ct)

    def test_empty_type_allowed(self):
        self.assertTrue(validate_stream_content_type(None))
        self.assertTrue(validate_stream_content_type(""))

    def test_wrong_types_rejected(self):
        for ct in ("application/json", "text/html", "application/xhtml+xml"):
            self.assertFalse(validate_stream_content_type(ct), ct)


class ResponseHeaderFilterTests(unittest.TestCase):
    def test_only_allowlisted_headers_forwarded(self):
        upstream = {
            "etag": '"abc"',
            "cache-control": "public, max-age=3600",
            "content-disposition": 'attachment; filename="a.xlsx"',
            "set-cookie": "session=evil",
            "location": "http://10.16.33.2:8002/evil",
            "content-type": "application/json",
            "content-length": "999999",
            "x-internal-path": "/opt/remote/secrets",
        }
        forwarded = filter_multimodal_response_headers(upstream)
        self.assertIn("etag", forwarded)
        self.assertIn("cache-control", forwarded)
        self.assertIn("content-disposition", forwarded)
        self.assertNotIn("set-cookie", forwarded)
        self.assertNotIn("location", forwarded)
        self.assertNotIn("content-type", forwarded)
        self.assertNotIn("content-length", forwarded)
        self.assertNotIn("x-internal-path", forwarded)

    def test_none_headers_ok(self):
        self.assertEqual(filter_multimodal_response_headers(None), {})


class BoundedAccumulationTests(unittest.TestCase):
    async def _run(self, chunks, cap):
        return await accumulate_bounded_bytes(_agen(chunks), cap)

    def test_within_cap_returns_bytes(self):
        result = asyncio_run(self._run([b"ab", b"cd"], 10))
        self.assertEqual(result, b"abcd")

    def test_over_cap_returns_none(self):
        result = asyncio_run(self._run([b"a" * 64, b"b" * 64], 100))
        self.assertIsNone(result)

    def test_empty_returns_empty_bytes(self):
        result = asyncio_run(self._run([], 10))
        self.assertEqual(result, b"")


class WhitelistRouteListTests(unittest.TestCase):
    def test_whitelisted_routes_is_sorted_and_nonempty(self):
        routes = whitelisted_proxy_routes()
        self.assertGreater(len(routes), 40)
        self.assertEqual(routes, sorted(routes))


async def _agen(chunks):
    for chunk in chunks:
        yield chunk


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
