import hashlib
import time
from typing import List, Dict, Any, Optional

import meilisearch
from 配置 import meilisearch_host, meilisearch_key


class MeilisearchClient:
    def __init__(self):
        self.client = meilisearch.Client(meilisearch_host, meilisearch_key)
        self.index = self.client.index('vibsea_pages')
        self._setup_index()

    def _setup_index(self):
        """设置索引配置"""
        try:
            # 设置可搜索字段
            self.index.update_searchable_attributes([
                'title', 'description', 'text', 'keywords'
            ])

            # 设置可筛选字段
            self.index.update_filterable_attributes([
                'domain', 'last_update', 'language', 'https_available', 'prosperity'
            ])

            # 设置排序字段
            self.index.update_sortable_attributes([
                'prosperity', 'last_update', 'relevance_score'
            ])

        except Exception as e:
            print(f"设置索引配置失败: {e}")


    def _make_id_from_url(self, url: str) -> str:
        return hashlib.md5(url.encode('utf-8')).hexdigest()


    def add_document(self, url: str, title: str, description: str, text: str,
                     keywords: List[str], domain: str, language: str = 'zh',
                     https_available: bool = True, prosperity: float = 0.0):
        """添加文档到索引"""
        doc_id = self._make_id_from_url(url)

        doc = {
            'id': doc_id,
            'url': url,
            'title': title,
            'description': description,
            'text': text,
            'keywords': keywords,
            'domain': domain,
            'language': language,
            'https_available': https_available,
            'prosperity': prosperity,
            'last_update': int(time.time()),
            'relevance_score': self._calculate_relevance_score(title, description, text, prosperity)
        }

        try:
            self.index.add_documents([doc])
            return True
        except Exception as e:
            print(f"添加文档失败 {url}: {e}")
            return False

    def batch_add_documents(self, documents: List[Dict[str, Any]]):
        """批量添加文档"""
        try:
            # 为每个文档添加relevance_score
            for doc in documents:
                doc_id = self._make_id_from_url(doc.get('url', ''))
                doc['id'] = doc_id

                if 'relevance_score' not in doc:
                    doc['relevance_score'] = self._calculate_relevance_score(
                        doc.get('title', ''),
                        doc.get('description', ''),
                        doc.get('text', ''),
                        doc.get('prosperity', 0.0)
                    )
                if 'last_update' not in doc:
                    doc['last_update'] = int(time.time())

            task = self.index.add_documents(documents)
            return task
        except Exception as e:
            print(f"批量添加文档失败: {e}")
            return None

    def search(self, query: str, limit: int = 10, offset: int = 0,
               site_filter: Optional[str] = None) -> Dict[str, Any]:
        """搜索文档"""
        search_params = {
            # 'limit': limit,
            # 'offset': offset,
            # 'attributesToRetrieve': ['url', 'title', 'description', 'text', 'domain', 'prosperity', 'language'],
            # 'sort': ['prosperity:desc', 'relevance_score:desc'],
        }

        # 添加站点过滤
        if site_filter:
            search_params['filter'] = f'domain = "{site_filter}" OR domain CONTAINS "{site_filter}"'

        try:
            results = self.index.search(query, **search_params)
            return results
        except Exception as e:
            print(f"搜索失败: {e}")
            return {'hits': [], 'estimatedTotalHits': 0}

    def update_prosperity(self, url: str, prosperity: float):
        """更新文档的繁荣度"""
        try:
            doc = {'id': self._make_id_from_url(url), 'prosperity': prosperity}
            self.index.update_documents([doc])
            return True
        except Exception as e:
            print(f"更新繁荣度失败 {url}: {e}")
            return False

    def delete_document(self, url: str):
        """删除文档"""
        try:
            self.index.delete_document(self._make_id_from_url(url))
            return True
        except Exception as e:
            print(f"删除文档失败 {url}: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """获取索引统计信息"""
        try:
            return self.index.get_stats()
        except Exception as e:
            print(f"获取统计信息失败: {e}")
            return {}

    def _calculate_relevance_score(self, title: str, description: str, text: str, prosperity: float) -> float:
        """计算相关性分数"""
        score = 0.0

        # 标题权重
        if title:
            score += len(title.split()) * 0.3

        # 描述权重
        if description:
            score += len(description.split()) * 0.2

        # 文本权重
        if text:
            score += min(len(text.split()) * 0.1, 50)  # 限制文本权重上限

        # 繁荣度权重
        score += prosperity * 10

        return score


# 全局客户端实例
meilisearch_client = MeilisearchClient()