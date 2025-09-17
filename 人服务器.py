import re
import time
import json
import math
import heapq
import logging
import threading
import concurrent.futures
from fnmatch import fnmatch
from itertools import islice
from functools import lru_cache
from collections import Counter
from urllib.parse import unquote
from typing import List, Tuple, Optional, Iterator, Dict

import jieba
import flask
import requests
import Levenshtein
import prometheus_client
from waitress import serve

from rimo_utils.计时 import 计时

from 打点 import timing_tick
from utils import netloc
import 文
import 信息
from 网站 import map_website
from 分析 import 分
from 配置 import 使用在线摘要, 在线摘要限时, 语种权重, 连续关键词权重, 反向链接权重, 权重每日衰减, 人服务器端口
from meilisearch_client import meilisearch_client

logging.getLogger('werkzeug').setLevel(logging.ERROR)
threading.excepthook = lambda x: print(f'{x.thread} 遇到了exception: {repr(x.exc_value)}')

app = flask.Flask(__name__)
# prometheus_client.start_http_server(14953)

调整表 = 信息.adjustment_data()
屏蔽词 = 信息.block_words()


@app.route('/search')
def search():
    resp = _search()
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp


_息 = lru_cache(maxsize=4096)(lambda b, _: map_website[b])
息 = lambda b: _息(b, int(time.time())//(3600*24))


def _search():
    try:
        q = flask.request.args.get('q', '') or bytes.fromhex(flask.request.args.get('qh', '')).decode('utf8')
        kiss = []
        site = None
        for x in q.split():
            if t := re.findall('^site:(.*)', x):
                site = t[0]
            else:
                kiss += 分(x, 多=False)
            kiss = [i for i in kiss if i not in 屏蔽词]
            assert len(kiss) < 20, '太多了，不行！'
            a, b = map(int, flask.request.args.get('slice', '0:10').split(':'))
            assert 0 <= a < b and b - a <= 20, '太长了，不行！'

            with 计时(kiss):
                结果, 总数 = 查询(kiss, a, b - a, site)
            data = {
                '分词': kiss,
                '数量': {},  # Meilisearch会处理数量统计
                '结果': 结果,
                '总数': 总数,
            }
        return app.response_class(
            response=json.dumps(data, indent=2, ensure_ascii=False),
            status=200,
            mimetype='application/json',
        )
    except Exception as e:
        logging.exception(e)
        return app.response_class(
            response=json.dumps({'信息': str(e)}, indent=2, ensure_ascii=False),
            status=500,
            mimetype='application/json',
        )


@timing_tick
def 查询(keys: List[str], offset: int = 0, limit: int = 10, site: Optional[str] = None):
    """使用Meilisearch进行查询"""
    if not keys:
        return [], 0

    # 构建查询字符串
    query = ' '.join(keys)

    # 执行搜索
    search_results = meilisearch_client.search(
        query=query,
        limit=limit,
        offset=offset,
        site_filter=site
    )

    hits = search_results.get('hits', [])
    total = search_results.get('estimatedTotalHits', 0)

    # 处理搜索结果
    结果 = []
    for hit in hits:
        url = hit.get('url', '')
        title = hit.get('title', '')
        description = hit.get('description', '')
        text = hit.get('text', '')
        domain = hit.get('domain', '')
        prosperity = hit.get('prosperity', 0)

        # 计算权重分数
        权重分数 = _计算权重分数(url, domain, prosperity, keys)

        # 获取摘要信息
        if 使用在线摘要:
            摘要信息 = 缓存摘要(url)
            if 摘要信息:
                title, description, text = 摘要信息

        if not description and not text:
            description = hit.get('description', '')[:80]
            text = hit.get('text', '')[:80]

        # 生成预览
        预览描述 = 预览(keys, description) if description else ''
        预览文本 = 预览(keys, text) if text else ''

        结果.append({
            '分数': 权重分数,
            '完全分数': [权重分数] * 12,  # 为了兼容性
            '原因': {'Meilisearch相关性': hit.get('_rankingScore', 1.0)},
            '网址': unquote(url),
            '信息': {
                '标题': title,
                '描述': 预览描述 or description[:100],
                '文本': 预览文本 or text[:200],
            },
            '相关性': {k: 1.0 for k in keys},  # Meilisearch处理相关性
            '相同域名个数': 1,  # 简化处理
        })

    return 结果, total


def _计算权重分数(url: str, domain: str, prosperity: float, keys: List[str]) -> float:
    """计算权重分数"""
    基础分数 = 1.0

    # 繁荣度权重
    繁荣权重 = 1 + prosperity * 反向链接权重

    # 语种权重
    try:
        网站信息 = map_website[domain]
        if 网站信息.lang_type:
            中文度 = 网站信息.lang_type.get('zh', 0)
            语种倍数 = 1 + 中文度 * 语种权重
        else:
            语种倍数 = 1.0
    except:
        语种倍数 = 1.0

    # 时间衰减
    现在 = int(time.time())
    try:
        网站信息 = map_website[domain]
        时间 = 网站信息.last_visit_time or 1648000000
        过去天数 = (现在 - 时间) // (3600 * 24)
        过去天数 = max(0, min(180, 过去天数 - 1))
        时间倍数 = 权重每日衰减 ** 过去天数
    except:
        时间倍数 = 1.0

    # 调整表
    调整倍数 = 调整表.get(domain, 1.0)

    return 基础分数 * 繁荣权重 * 语种倍数 * 时间倍数 * 调整倍数


def 预览(keys: List[str], text: str, limit: int = 1000) -> str:
    """生成包含关键词的文本预览"""
    if not text or not keys:
        return ''

    text = text[:limit]

    # 找到包含关键词的位置
    positions = []
    text_lower = text.lower()
    for key in keys:
        key_lower = key.lower()
        pos = text_lower.find(key_lower)
        if pos != -1:
            positions.append(pos)

    if not positions:
        return text[:100] + ('...' if len(text) > 100 else '')

    # 选择最佳预览位置
    best_pos = min(positions)
    start = max(0, best_pos - 50)
    end = min(len(text), best_pos + 150)

    preview = text[start:end]
    if start > 0:
        preview = '...' + preview
    if end < len(text):
        preview = preview + '...'

    return preview


def 缓存摘要(url: str):
    """获取缓存的摘要信息"""
    if not 使用在线摘要:
        return None
    try:
        return 文.get_desc(url, 乖=False, timeout=在线摘要限时, 大小限制=60000)[:3]
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        print(f'获取「{url}」时网络不好！')
        return None
    except requests.exceptions.RequestException as e:
        print(f'获取「{url}」时遇到了{repr(e)}！')
        return None
    except Exception as e:
        logging.exception(e)
        return None


if __name__ == '__main__':
    serve(app, host='0.0.0.0', port=人服务器端口)
