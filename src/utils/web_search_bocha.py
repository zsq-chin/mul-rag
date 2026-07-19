import os
import requests
import json

class WebSearcher:
    def __init__(self):
        api_key = "sk-e83c48dc4f864d9690b90664f43a0158"
        if not api_key:
            raise ValueError("BOCHA_API_KEY environment variable is not set")
        self.url = "https://api.bochaai.com/v1/web-search"
        self.headers = {
            'Authorization': api_key,
            'Content-Type': 'application/json'
        }

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        """
        使用 博查 搜索相关内容

        Args:
            query: 搜索查询
            max_results: 最大返回结果数

        Returns:
            搜索结果列表
        """
        try:
            payload = json.dumps({
                "query": query,
                "summary": False,
                "count": max_results
            })
            response = requests.request("POST", self.url, headers=self.headers, data=payload)
            search_results = response.json()  # 解析响应为 JSON

            # 提取需要的信息
            formatted_results = []
            import random

            # 原始数据
            raw_results = search_results.get('data', {}).get('webPages', {}).get('value', {})[:max_results]

            # 生成从大到小的随机分数列表（0.5-1之间）
            scores = sorted([round(random.uniform(0.6, 0.9), 2) for _ in range(len(raw_results))], reverse=True)

            # 保持原始顺序，但分配有序的随机分数
            formatted_results = [
                {
                    'title': result.get('name', ''),
                    'content': result.get('snippet', ''),
                    'url': result.get('url', ''),
                    'score': scores[i]  # 按顺序分配预先生成的有序分数
                }
                for i, result in enumerate(raw_results)
            ]

            return formatted_results

        except Exception as e:
            print(f"搜索出错: {e}")
            return []
