"""阶段 A2：冻结并版本化远端多模态接口契约的契约测试。

默认模式完全离线：只读提交到仓库的契约快照
（server/contracts/remote_contract_snapshot.json），验证 DTO 与规范化函数
能够解析快照中冻结的响应形状。

显式开启 RUN_REMOTE_MULTIMODAL_TESTS=1 时，额外做真实远端集成校验：
- 下载远端 /openapi.json，比对 md5 与快照；不一致时报告「远端接口版本不兼容」；
- 请求 /api/v1/kb/list，确认稳定 kbId=钻井设计资料 存在。

本模块不 import 任何 FastAPI 路由，符合测试隔离约束。
"""

import hashlib
import json
import os
import unittest
from pathlib import Path

from pydantic import ValidationError

from server.schemas.multimodal import (
    RemoteImagePage,
    RemoteKbListResponse,
    RemoteSearchRequest,
    RemoteSearchResponse,
)
from server.utils.multimodal_remote import (
    normalize_multimodal_image_page,
    normalize_multimodal_kbs,
    normalize_multimodal_results,
)

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = ROOT / "server" / "contracts" / "remote_contract_snapshot.json"

# 快照冻结的远端健康接口（B3 白名单依赖）
SAVED_OPENAPI_PATH = ROOT / "saves" / "remote_openapi.json"


def _load_snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


class ContractSnapshotTests(unittest.TestCase):
    """快照元数据与形状完整性（离线）。"""

    def setUp(self):
        self.snapshot = _load_snapshot()

    def test_snapshot_file_exists_with_frozen_metadata(self):
        self.assertTrue(SNAPSHOT_PATH.exists())
        self.assertEqual(self.snapshot["schema"], "sage-remote-multimodal-contract/v1")
        self.assertEqual(self.snapshot["remote"]["openapi_version"], "1.0.0")
        self.assertEqual(self.snapshot["remote"]["openapi_md5"], "5397c63053743950c0e9ccbc3d3951f0")
        self.assertEqual(self.snapshot["remote"]["paths_count"], 55)

    def test_target_kb_is_frozen(self):
        kb = self.snapshot["target_kb"]
        self.assertEqual(kb["kbId"], "钻井设计资料")
        self.assertEqual(kb["kbName"], "钻井设计资料")

    def test_snapshot_covers_required_endpoints(self):
        endpoints = set(self.snapshot["endpoints"].keys())
        self.assertTrue(
            {
                "health",
                "kb_list",
                "index_search",
                "kb_images",
                "pdf_images",
                "kb_files",
            }.issubset(endpoints)
        )

    def test_saved_openapi_md5_matches_snapshot(self):
        """保存的远端 OpenAPI 快照哈希必须与契约快照一致（离线校验）。"""
        if not SAVED_OPENAPI_PATH.exists():
            self.skipTest("saves/remote_openapi.json 不在本机")
        digest = hashlib.md5(SAVED_OPENAPI_PATH.read_bytes()).hexdigest()
        self.assertEqual(
            digest,
            self.snapshot["remote"]["openapi_md5"],
            "saves/remote_openapi.json 与冻结契约不一致，说明远端接口已变化",
        )


class KbListContractTests(unittest.TestCase):
    def test_kb_list_dto_parses_frozen_shape(self):
        payload = {
            "kbs": [
                {
                    "kbId": "钻井设计资料",
                    "kbName": "钻井设计资料",
                    "vectorStoreType": "faiss",
                    "embedModel": "bge-m3:latest",
                    "fileCount": 22,
                    "createdAt": 1767870010,
                },
                {"kbId": "kb-2", "kbName": "第二库"},
            ]
        }
        parsed = RemoteKbListResponse.model_validate(payload)
        self.assertEqual(len(parsed.kbs), 2)
        self.assertEqual(parsed.kbs[0].kbId, "钻井设计资料")
        self.assertEqual(parsed.kbs[0].fileCount, 22)
        # 缺失可选字段时回落默认值
        self.assertEqual(parsed.kbs[1].fileCount, 0)

    def test_kb_list_dto_rejects_missing_kb_id(self):
        with self.assertRaises(ValidationError):
            RemoteKbListResponse.model_validate({"kbs": [{"kbName": "无 id"}]})

    def test_normalize_multimodal_kbs_accepts_frozen_shape(self):
        payload = {
            "kbs": [
                {
                    "kbId": "钻井设计资料",
                    "kbName": "钻井设计资料",
                    "vectorStoreType": "faiss",
                    "embedModel": "bge-m3:latest",
                    "fileCount": 22,
                }
            ]
        }
        normalized = normalize_multimodal_kbs(payload)
        self.assertEqual(normalized[0]["kbId"], "钻井设计资料")
        self.assertEqual(normalized[0]["fileCount"], 22)
        self.assertEqual(normalized[0]["vectorStoreType"], "faiss")


class SearchRequestContractTests(unittest.TestCase):
    def test_search_request_accepts_frozen_shape(self):
        req = RemoteSearchRequest(kbId="钻井设计资料", query="井身结构设计")
        self.assertEqual(req.kbId, "钻井设计资料")
        self.assertEqual(req.k, 5)

    def test_search_request_top_k_clamped_to_1_20(self):
        RemoteSearchRequest(kbId="k", query="q", k=1)
        RemoteSearchRequest(kbId="k", query="q", k=20)
        with self.assertRaises(ValidationError):
            RemoteSearchRequest(kbId="k", query="q", k=0)
        with self.assertRaises(ValidationError):
            RemoteSearchRequest(kbId="k", query="q", k=21)
        with self.assertRaises(ValidationError):
            RemoteSearchRequest(kbId="k", query="q", k=-3)

    def test_search_request_null_k_falls_back_to_default(self):
        req = RemoteSearchRequest(kbId="k", query="q", k=None)
        self.assertEqual(req.k, 5)

    def test_search_request_rejects_missing_required_fields(self):
        with self.assertRaises(ValidationError):
            RemoteSearchRequest(query="q")  # 缺 kbId
        with self.assertRaises(ValidationError):
            RemoteSearchRequest(kbId="k")  # 缺 query


class SearchResponseContractTests(unittest.TestCase):
    def test_search_response_dto_parses_frozen_shape(self):
        payload = {
            "ok": True,
            "results": [
                {
                    "id": 9371,
                    "score": 0.0164,
                    "entity_key": "丰页1-2-A14HF-钻井工程设计",
                    "source": '{"kb_id": "钻井设计资料", "page": 10, "type": "image", "image_path": "图3-3 井身结构图.png"}',
                    "chunk_text": "图3-3 井身结构图",
                }
            ],
        }
        parsed = RemoteSearchResponse.model_validate(payload)
        self.assertTrue(parsed.ok)
        self.assertEqual(len(parsed.results), 1)
        self.assertEqual(parsed.results[0].entity_key, "丰页1-2-A14HF-钻井工程设计")
        self.assertIn("image_path", parsed.results[0].source)

    def test_normalize_multimodal_results_accepts_frozen_shape(self):
        payload = {
            "ok": True,
            "results": [
                {
                    "id": 9371,
                    "score": 0.0164,
                    "entity_key": "well-file",
                    "source": json.dumps(
                        {"kb_id": "钻井设计资料", "page": 10, "type": "image", "image_path": "图3-3 井身结构图.png"}
                    ),
                    "chunk_text": "图3-3 井身结构图",
                }
            ],
        }
        results = normalize_multimodal_results(payload, kb_id="钻井设计资料")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["fileId"], "well-file")
        self.assertEqual(results[0]["page"], 10)
        self.assertEqual(results[0]["images"][0]["path"], "图3-3 井身结构图.png")


class ImagePageContractTests(unittest.TestCase):
    def test_image_page_dto_parses_frozen_shape(self):
        payload = {
            "items": [
                {
                    "img_name": "4a8f4e81....png",
                    "summary": "(未生成详细摘要)",
                    "page_num": 34,
                    "source_page_num": 34,
                    "original_img_name": "original.jpg",
                    "fileId": "DXFY1-2-513HF钻井工程设计",
                    "fileName": "DXFY1-2-513HF钻井工程设计.pdf",
                }
            ],
            "page": 1,
            "pageSize": 24,
            "total": 249,
        }
        parsed = RemoteImagePage.model_validate(payload)
        self.assertEqual(parsed.total, 249)
        self.assertEqual(parsed.pageSize, 24)
        self.assertEqual(parsed.items[0].fileId, "DXFY1-2-513HF钻井工程设计")
        self.assertEqual(parsed.items[0].img_name, "4a8f4e81....png")

    def test_normalize_image_page_passes_through_server_side_pagination(self):
        payload = {
            "items": [{"img_name": f"img-{i}.png", "fileId": "f"} for i in range(24)],
            "page": 2,
            "pageSize": 24,
            "total": 249,
        }
        page = normalize_multimodal_image_page(payload, page=2, page_size=24)
        self.assertEqual(page["total"], 249)
        self.assertEqual(page["page"], 2)
        self.assertEqual(len(page["items"]), 24)


@unittest.skipUnless(
    os.environ.get("RUN_REMOTE_MULTIMODAL_TESTS") == "1",
    "真实远端集成测试需显式设置 RUN_REMOTE_MULTIMODAL_TESTS=1",
)
class LiveRemoteContractTests(unittest.TestCase):
    """真实远端契约校验（需网络 + 显式开关）。"""

    BASE_URL = os.environ.get(
        "MULTIMODAL_REMOTE_BASE_URL", "http://10.16.33.2:8002/api/v1"
    )

    def test_live_openapi_md5_matches_snapshot(self):
        import requests
        from urllib.parse import urlsplit

        # openapi.json 在服务器根路径（/openapi.json），不在 /api/v1 前缀下
        origin = f"{urlsplit(self.BASE_URL).scheme}://{urlsplit(self.BASE_URL).netloc}"
        resp = requests.get(f"{origin}/openapi.json", timeout=15)
        resp.raise_for_status()
        digest = hashlib.md5(resp.content).hexdigest()
        self.assertEqual(
            digest,
            _load_snapshot()["remote"]["openapi_md5"],
            "远端接口版本不兼容：openapi.json 哈希与冻结契约不一致，禁止静默返回空结果",
        )

    def test_live_kb_list_contains_target_kb(self):
        import requests

        resp = requests.get(f"{self.BASE_URL}/kb/list", timeout=15)
        resp.raise_for_status()
        parsed = RemoteKbListResponse.model_validate(resp.json())
        kb_ids = {item.kbId for item in parsed.kbs}
        self.assertIn("钻井设计资料", kb_ids)


if __name__ == "__main__":
    unittest.main()
