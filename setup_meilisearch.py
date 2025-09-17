#!/usr/bin/env python3
# setup_meilisearch.py - 初始化Meilisearch的脚本

import time
from meilisearch_client import meilisearch_client


def setup_meilisearch():
    """初始化Meilisearch设置"""
    print("正在初始化Meilisearch...")

    try:
        # 检查连接
        health = meilisearch_client.client.health()
        print(f"Meilisearch状态: {health}")

        # 获取索引信息
        try:
            index_info = meilisearch_client.index.get_stats()
            print(f"索引统计: {index_info}")
        except Exception as e:
            print(f"获取索引信息失败: {e}")
            print("这是正常的，如果这是第一次运行")

        # 等待设置完成
        print("等待索引设置完成...")
        time.sleep(2)

        print("Meilisearch初始化完成！")
        print("索引名称: vibsea_pages")
        print("可搜索字段: title, description, text, keywords")
        print("可筛选字段: domain, last_update, language, https_available, prosperity")
        print("可排序字段: prosperity, last_update, relevance_score")

        return True

    except Exception as e:
        print(f"初始化Meilisearch失败: {e}")
        print("请确保Meilisearch服务正在运行")
        print("启动命令: meilisearch --http-addr 0.0.0.0:7700")
        return False


def test_search():
    """测试搜索功能"""
    print("\n测试搜索功能...")

    # 添加测试文档
    test_doc = {
        'url': 'https://test.example.com/',
        'title': '测试文档',
        'description': '这是一个测试文档',
        'text': '测试内容，用于验证搜索功能',
        'keywords': ['测试', 'search', 'test'],
        'domain': 'test.example.com',
        'language': 'zh',
        'https_available': True,
        'prosperity': 1.0
    }

    try:
        # 添加测试文档
        task = meilisearch_client.batch_add_documents([test_doc])
        print(f"添加测试文档: {task}")

        # 等待索引完成
        print("等待索引完成...")
        # 等待索引任务完成
        meilisearch_client.client.wait_for_task(task.task_uid)
        result = meilisearch_client.client.wait_for_task(task.task_uid)
        print(result)

        # 执行测试搜索
        results = meilisearch_client.search("测试", limit=5)
        print(f"搜索结果: {len(results.get('hits', []))} 个结果")

        if results.get('hits'):
            print("搜索功能正常！")
            # 清理测试文档
            meilisearch_client.delete_document('https://test.example.com/')
            print("已清理测试文档")
        else:
            print("搜索功能可能有问题，没有找到测试文档")

    except Exception as e:
        print(f"搜索测试失败: {e}")


if __name__ == '__main__':
    if setup_meilisearch():
        test_search()
    else:
        exit(1)