import json
import os
import unittest
from unittest.mock import Mock, patch

from server.utils.multimodal_remote import (
    build_multimodal_remote_url,
    filter_multimodal_proxy_headers,
    format_multimodal_context,
    normalize_multimodal_kbs,
    normalize_multimodal_results,
    normalize_multimodal_image_page,
    pick_first_kb_id,
    search_multimodal_remote,
)


class MultimodalRemoteTests(unittest.TestCase):
    def test_referenced_images_and_image_path_are_deduplicated(self):
        payload = {
            "results": [
                {
                    "chunk_text": "![Well structure](./images/well-structure.png)",
                    "source": json.dumps(
                        {
                            "file_id": "file-1",
                            "image_path": "well-structure.png",
                            "referenced_images": [
                                "well-structure.png",
                                {"image_path": "casing-program.png", "caption": "Casing program"},
                            ],
                        }
                    ),
                }
            ]
        }

        result = normalize_multimodal_results(payload, kb_id="kb-1")[0]

        self.assertEqual(
            [image["path"] for image in result["images"]],
            ["well-structure.png", "casing-program.png"],
        )
        self.assertEqual(result["images"][1]["alt"], "Casing program")

    def test_unsafe_image_paths_are_rejected(self):
        payload = {
            "results": [
                {
                    "fileId": "file-1",
                    "text": "safe",
                    "images": [
                        "../secret.png",
                        "/absolute.png",
                        "https://evil.example/tracker.png",
                        "images/safe%00.png",
                        "images/safe.png",
                    ],
                }
            ]
        }

        result = normalize_multimodal_results(payload, kb_id="kb-1")[0]

        self.assertEqual([image["path"] for image in result["images"]], ["safe.png"])

    def test_complex_html_table_is_preserved_as_table_content(self):
        payload = {
            "results": [
                {
                    "content": '<table><tr><td rowspan="2">A</td></tr></table>',
                    "source": {"file_id": "file-1"},
                }
            ]
        }

        result = normalize_multimodal_results(payload, kb_id="kb-1")[0]

        self.assertEqual(result["contentType"], "table")
        self.assertIn("rowspan", result["text"])

    def test_unpaged_image_catalog_is_sliced_on_the_server(self):
        page = normalize_multimodal_image_page(
            {"images": [{"name": f"image-{index}.png"} for index in range(5)]},
            page=2,
            page_size=2,
        )

        self.assertEqual([item["name"] for item in page["items"]], ["image-2.png", "image-3.png"])
        self.assertEqual(page["total"], 5)
        self.assertEqual(page["page"], 2)
        self.assertEqual(page["pageSize"], 2)

    def test_oversized_remote_page_is_still_limited_on_the_server(self):
        page = normalize_multimodal_image_page(
            {
                "items": [{"name": f"image-{index}.png"} for index in range(5)],
                "page": 2,
                "pageSize": 2,
                "total": 5,
            },
            page=2,
            page_size=2,
        )

        self.assertEqual([item["name"] for item in page["items"]], ["image-2.png", "image-3.png"])
        self.assertEqual(page["total"], 5)

    def test_build_remote_url_joins_configured_base(self):
        url = build_multimodal_remote_url("pdf/images", "http://remote.example/api/v1/")

        self.assertEqual(url, "http://remote.example/api/v1/pdf/images")

    def test_build_remote_url_rejects_absolute_urls(self):
        with self.assertRaises(ValueError):
            build_multimodal_remote_url("http://evil.example/api/v1/kb/list", "http://remote.example/api/v1")

    def test_proxy_header_filter_drops_host_auth_and_hop_headers(self):
        filtered = filter_multimodal_proxy_headers(
            {
                "Host": "current-app",
                "Authorization": "Bearer app-token",
                "Connection": "keep-alive",
                "Content-Type": "application/json",
                "X-Trace-Id": "abc",
            }
        )

        self.assertEqual(filtered, {"Content-Type": "application/json", "X-Trace-Id": "abc"})

    def test_normalize_results_accepts_index_search_shape(self):
        payload = {
            "ok": True,
            "results": [
                {
                    "id": 7,
                    "fileId": "file-1",
                    "source": '{"fileName":"fracturing.pdf","page":3,"type":"text"}',
                    "chunk_text": "Fracturing pump pressure and sand ratio details.",
                    "score": 0.83,
                }
            ],
        }

        results = normalize_multimodal_results(payload)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], 7)
        self.assertEqual(results[0]["fileId"], "file-1")
        self.assertEqual(results[0]["fileName"], "fracturing.pdf")
        self.assertEqual(results[0]["page"], 3)
        self.assertEqual(results[0]["text"], "Fracturing pump pressure and sand ratio details.")
        self.assertEqual(results[0]["score"], 0.83)

    def test_format_context_uses_normalized_sources(self):
        results = [
            {
                "fileName": "fracturing.pdf",
                "page": 3,
                "score": 0.83,
                "text": "Pump pressure rose after adding proppant.",
            }
        ]

        context = format_multimodal_context(results)

        self.assertIn("多模态知识库检索结果", context)
        self.assertIn("[1] fracturing.pdf p.3 score=0.83", context)
        self.assertIn("Pump pressure rose after adding proppant.", context)

    def test_pick_first_kb_id_accepts_kb_list_shape(self):
        payload = {
            "kbs": [
                {"kbId": "kb-frac", "kbName": "Fracturing"},
                {"kbId": "kb-drill", "kbName": "Drilling"},
            ]
        }

        self.assertEqual(pick_first_kb_id(payload), "kb-frac")

    def test_normalize_multimodal_kbs_accepts_remote_list_shape(self):
        payload = {
            "kbs": [
                {
                    "kbId": "kb-frac",
                    "kbName": "Fracturing",
                    "fileCount": 3,
                    "vectorStoreType": "faiss",
                    "embedModel": "bge-m3:latest",
                },
                {
                    "id": "kb-drill",
                    "name": "Drilling",
                },
            ]
        }

        kbs = normalize_multimodal_kbs(payload)

        self.assertEqual(
            kbs[0],
            {
                "kbId": "kb-frac",
                "kbName": "Fracturing",
                "fileCount": 3,
                "vectorStoreType": "faiss",
                "embedModel": "bge-m3:latest",
            },
        )
        self.assertEqual(kbs[1]["kbId"], "kb-drill")
        self.assertEqual(kbs[1]["kbName"], "Drilling")

    def test_normalize_results_extracts_markdown_images(self):
        payload = {
            "ok": True,
            "results": [
                {
                    "entity_key": "well-design-file",
                    "source": '{"file_id":"well-design-file"}',
                    "chunk_text": "井身结构示意图\\n![Image: 图 4-1](./images/图 4-1.png)",
                    "score": 0.75,
                }
            ],
        }

        results = normalize_multimodal_results(payload, kb_id="钻井设计资料")

        self.assertEqual(results[0]["fileId"], "well-design-file")
        self.assertEqual(results[0]["images"][0]["name"], "图 4-1.png")
        self.assertIn("/api/chat/multimodal/image?", results[0]["images"][0]["url"])
        self.assertIn("kbId=", results[0]["images"][0]["url"])
        self.assertIn("fileId=", results[0]["images"][0]["url"])
        self.assertIn("imagePath=", results[0]["images"][0]["url"])

    def test_markdown_image_with_leading_slash_is_extracted(self):
        payload = {
            "ok": True,
            "results": [
                {
                    "fileId": "file-1",
                    "text": "![图 4-2 2# 井含水习投影](/images/图 4-2 2# 井含水习投影.png)\n\nSome description",
                }
            ],
        }

        results = normalize_multimodal_results(payload, kb_id="kb-1")

        self.assertEqual(len(results[0]["images"]), 1)
        self.assertEqual(results[0]["images"][0]["name"], "图 4-2 2# 井含水习投影.png")
        self.assertIn("imagePath=", results[0]["images"][0]["url"])

    @patch.dict(
        os.environ,
        {"MULTIMODAL_REMOTE_BASE_URL": "https://remote.example/api/v1"},
        clear=False,
    )
    @patch("server.utils.multimodal_remote.requests.post")
    def test_search_remote_preserves_kb_on_http_error(self, mock_post):
        response = Mock()
        response.ok = False
        response.status_code = 400
        response.json.return_value = {
            "error": {
                "code": "INDEX_NOT_FOUND",
                "message": "请先构建索引",
            }
        }
        response.text = '{"error":{"code":"INDEX_NOT_FOUND","message":"请先构建索引"}}'
        mock_post.return_value = response

        result = search_multimodal_remote(
            "井身结构示意图",
            {
                "multimodal_kb_id": "钻井井史报告",
                "multimodal_kb_name": "钻井井史报告",
            },
        )

        self.assertEqual(result["kb_id"], "钻井井史报告")
        self.assertEqual(result["kb_name"], "钻井井史报告")
        self.assertEqual(result["status"], "error")
        self.assertIn("请先构建索引", result["message"])
        self.assertEqual(result["results"], [])


if __name__ == "__main__":
    unittest.main()
