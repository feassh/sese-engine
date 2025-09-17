import math
import json
import time
import random
import socket
import hashlib
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, Iterable, Callable, Dict, Hashable, Any

import tldextract

from 打点 import tqdm, tqdm面板, timing_tick, 直方图打点
import 分析
import 信息
from 文 import shrink_url, get_desc
from 网站 import map_website, Website
from 配置 import 爬取线程数, 爬取集中度, 单网页最多关键词, 入口, 最大epoch, 预期繁荣网站比例, meilisearch_batch_size
from utils import tqdm_exception_logger, 坏, 检测语言, netloc, html结构特征
from meilisearch_client import meilisearch_client

_tqdm_panel = tqdm面板(
    ['访问url数', '访问成功url数', '获取域名基本信息次数', '获取词数', '获取词数(英文)', '待索引文档数', '索引成功次数',
     '索引失败次数', '爬取线程数', '当前epoch进度'])
_prosperity_tracking = 直方图打点('访问url繁荣',
                                  [0, 0.1, 0.3, 0.7, 1.5, 3.1, 6.3, 12, 25, 50, 100, 200, 400, 800, 1600, float("inf")])
_url_distribution_tracking = 直方图打点('url域名分布',
                                        [1, 2, 3, 5, 7, 11, 17, 25, 38, 57, 86, 129, 194, 291, 437, 656, float("inf")])
# prometheus_client.start_http_server(14950)

_prosperity_data = 信息.prosperity_data()
_queue_waiting_index = []
_threading_lock = threading.Lock()


def batch_index_doc():
    """批量将文档添加到Meilisearch"""
    global _queue_waiting_index
    with _threading_lock:
        if len(_queue_waiting_index) >= meilisearch_batch_size:
            documents = _queue_waiting_index[:meilisearch_batch_size]
            _queue_waiting_index = _queue_waiting_index[meilisearch_batch_size:]

            try:
                task = meilisearch_client.batch_add_documents(documents)
                if task:
                    _tqdm_panel['索引成功次数'].update(len(documents))
                else:
                    _tqdm_panel['索引失败次数'].update(len(documents))
            except Exception as e:
                tqdm_exception_logger(e)
                _tqdm_panel['索引失败次数'].update(len(documents))


def add_to_index(url: str, title: str, description: str, text: str, keywords: List[Tuple[str, float]]):
    """添加文档到待索引队列"""
    global _queue_waiting_index

    domain = netloc(url)

    # 从网站信息获取语言
    website_info = map_website[domain]
    language = 'zh'  # 默认中文
    if website_info.lang_type:
        language = max(website_info.lang_type.items(), key=lambda x: x[1])[0]

    # 获取繁荣度
    prosperity = 信息.prosperity(url)

    document = {
        'url': url,
        'title': title,
        'description': description,
        'text': text,
        'keywords': [kw[0] for kw in keywords],  # 只保留关键词，不保留权重
        'domain': domain,
        'language': language,
        'https_available': url.startswith('https://'),
        'prosperity': prosperity
    }

    with _threading_lock:
        _queue_waiting_index.append(document)
        _tqdm_panel['待索引文档数'].n = len(_queue_waiting_index)
        _tqdm_panel['待索引文档数'].refresh()

    # 检查是否需要批量处理
    if len(_queue_waiting_index) >= meilisearch_batch_size:
        batch_index_doc()


@timing_tick
def desc(url: str) -> Tuple[str, str, str, List[str], str, Dict[str, str], str, str]:
    r = get_desc(url, timeout=10)
    if len(url) >= 250:
        return r
    title, description, text, href, raw_url, redirect_data, raw, server_type = r
    if redirect_data:
        for k, v in redirect_data.items():
            b = netloc(k)
            info = map_website[b]
            info.redirect[k] = v
            if len(info.redirect) > 50:
                info.redirect = dict(
                    sorted(info.redirect.items(), key=lambda x: random.random() - (x[0] == f'https://{b}/'))[:40])
            map_website[b] = info

    # 直接处理关键词并添加到索引，移除原来的队列发送
    l = 分析.龙(title, description, text)
    if l:
        l = sorted(l, key=lambda x: x[1], reverse=True)[:单网页最多关键词]
        _tqdm_panel['获取词数'].update(len(l))
        _tqdm_panel['获取词数(英文)'].update(len([x for x in l if x[0].isascii()]))

        # 添加到Meilisearch索引
        add_to_index(raw_url, title, description[:256], text[:256], l)

    return r


def reload(b: str, x: Website):
    try:
        if x.quality is None or x.feature is None or x.keywords is None or x.https_valid is None or x.structure is None:
            x.quality, x.feature, x.keywords, https_valid, x.structure, server_type = domain_info(b)
            if not x.https_valid:
                x.https_valid = https_valid
            if server_type and not x.server_type:
                x.server_type = [server_type]
    except Exception as e:
        tqdm_exception_logger(e)
    try:
        if x.ip is None:
            x.ip = [i[4][0] for i in socket.getaddrinfo(b, 443, 0, 0, socket.SOL_TCP)][:3]
    except Exception as e:
        tqdm_exception_logger(e)


def domain_info(domain: str) -> tuple[float, tuple[int, str, int], list[str], bool, str, Any]:
    _tqdm_panel['获取域名基本信息次数'].update(1)
    try:
        title, description, text, href, raw_url, redirect_data, raw, server_type = desc(f'https://{domain}/')
        https_valid = True
    except Exception:
        title, description, text, href, raw_url, redirect_data, raw, server_type = desc(f'http://{domain}/')
        https_valid = False
    s = 1.0
    if not title:
        s *= 0.2
    if not description:
        s *= 0.7
    if not https_valid:
        s *= 0.8
    if 'm' in domain.split('.'):
        s *= 0.6
    z = ' '.join([title, description, text])
    e = z.encode('utf8')
    feature = len(z), hashlib.md5(e).hexdigest(), sum([*e])
    structure = html结构特征(raw)
    keywords = [x[0] for x in sorted(分析.龙('', '', text), key=lambda x: -x[1])[:40]]
    return s, feature, keywords, https_valid, structure, server_type


def 超吸(url: str) -> List[str]:
    _tqdm_panel['访问url数'].update(1)
    _tqdm_panel['当前epoch进度'].update(1)
    try:
        try:
            _prosperity_tracking.observe(_prosperity_data.get(netloc(url), 0))
        except Exception as e:
            tqdm_exception_logger(e)
        try:
            title, description, text, href, raw_url, redirect_data, raw, server_type = desc(url)
        except Exception as e:
            b = netloc(url)
            info = map_website[b]
            if info.ip is None:
                infoip = [i[4][0] for i in socket.getaddrinfo(b, 443, 0, 0, socket.SOL_TCP)][:3]
                info.ip = infoip
            if info.success_rate is None:
                info.success_rate = 0
            info.success_rate *= 0.99
            map_website[b] = info
            raise e
        else:
            _tqdm_panel['访问成功url数'].update(1)
            b = netloc(raw_url)
            _shrink_url = shrink_url(raw_url)

            info = map_website[b]
            info.visit_count += 1
            info.last_visit_time = int(time.time())
            if info.success_rate is None:
                info.success_rate = 1
            if raw_url.startswith('https://'):
                info.https_valid = True
            info.success_rate = info.success_rate * 0.99 + 0.01
            reload(b, info)
            try:
                if server_type and server_type not in info.server_type:
                    info.server_type.append(server_type)
                    info.server_type = info.server_type[-5:]
                if info.visit_count < 10 or random.random() < 0.1:
                    lang_type = 检测语言(' '.join((title, description, text)))
                    update_intensity = min(0.2, 1 / (info.visit_count ** 0.5))
                    td = {k: v * (1 - update_intensity) for k, v in info.lang_type.items()}
                    td[lang_type] = td.get(lang_type, 0) + update_intensity
                    info.lang_type = td
                    external_href = [h for h in href if shrink_url(h) != _shrink_url]
                    info.link += random.sample(external_href, min(10, len(external_href)))
                    if len(info.link) > 250:
                        info.link = random.sample(info.link, 200)
            except Exception as e:
                tqdm_exception_logger(e)
            map_website[b] = info

            if _shrink_url != b:
                _map_website = map_website[_shrink_url]
                _map_website.visit_count += 0.2
                reload(_shrink_url, _map_website)
                map_website[_shrink_url] = _map_website
            if len(href) > 100:
                external_href = [h for h in href if shrink_url(h) != _shrink_url]
                href = random.sample(href, 100)
                if external_href:
                    href += random.sample(external_href, min(len(external_href), 3))
            return href
    except Exception as e:
        tqdm_exception_logger(e)
        time.sleep(0.2)
        return []


def purification(hash_f: Callable[[str], Hashable], a: Iterable[str], k: float) -> List[str]:
    d = {}
    a = [*a]
    random.shuffle(a)
    for url in a:
        d.setdefault(hash_f(url), []).append(url)
    upper_limit = 10
    if len(d) > 1:
        upper_limit = max(10, int(sum(sorted([int(len(v) ** k) for v in d.values()])[:-1]) * 0.6))
    res = []
    for v in d.values():
        sn = 1 + min(upper_limit, int(len(v) ** k))
        res += v[:sn]
    random.shuffle(res)
    return res


def 重整(url_list: List[Tuple[str, float]]) -> List[str]:
    def 计算兴趣(域名: str, 已访问次数: int) -> float:
        限制 = _prosperity_data.get(域名, 0) * 500 + 50
        b = 0.1 ** (1 / 限制)
        return b ** 已访问次数

    def 喜欢(item: Tuple[str, float]) -> float:
        url, 基本权重 = item
        b = netloc(url)
        息 = 缓存信息[b]
        if 息.lang_type:
            中文度 = 息.lang_type.get('zh', 0) / sum(息.lang_type.values())
        else:
            中文度 = 0.5
        已访问次数, 质量 = 息.visit_count, 息.quality or 1
        超b = shrink_url(url)
        兴趣 = 计算兴趣(b, 已访问次数)
        if 超b == b:
            兴趣2 = 1
        else:
            超息 = 缓存信息[超b]
            已访问次数2 = 超息.visit_count
            兴趣2 = 计算兴趣(超b, 已访问次数2)
        繁荣 = min(62, _prosperity_data.get(b, 0))
        if 繁荣 > 0:
            繁荣 += 0.5
        荣 = math.log2(2 + 繁荣) + 1
        return (0.1 + 中文度) * min(0.05 + 兴趣, 0.05 + 兴趣2) * 质量 * (1 - 坏(url)) * 基本权重 * 荣

    if len(url_list) > 10_0000:
        url_list = random.sample(url_list, 10_0000)
    urls = [url for url, w in url_list]
    domains = {netloc(url) for url in urls} | {shrink_url(url) for url in urls}
    pool = ThreadPoolExecutor(max_workers=16)
    缓存信息 = {k: v for k, v in zip(domains, pool.map(map_website.get, domains))}
    a = random.choices(url_list, weights=map(喜欢, url_list), k=min(45000, len(url_list) // 3 + 250))
    a = {url for url, w in a}
    res = purification(lambda url: tldextract.extract(url).domain, a, 爬取集中度)
    res_https = [i for i in res if i.startswith('https://')]
    res_http = [i for i in res if not i.startswith('https://')]
    if len(res_http) > len(res_https) // 4:
        res_http = random.sample(res_http, len(res_https) // 4)
    res = res_http + res_https
    res_荣 = [i for i in res if 信息.prosperity(i)]
    res_不荣 = [i for i in res if not 信息.prosperity(i)]
    n = int(len(res_荣) * (1 - 预期繁荣网站比例) / 预期繁荣网站比例)
    if len(res_不荣) > n:
        res_不荣 = random.sample(res_不荣, max(len(res_不荣) - len(res) // 10, n))
    res = res_荣 + res_不荣
    random.shuffle(res)
    return res


打点 = []


def bfs(start: str, epoch=最大epoch):
    吸过 = set()
    q = [start]
    for ep in tqdm(range(epoch), ncols=60, desc='epoch'):
        吸过 |= {*q}
        新q = []
        _tqdm_panel['爬取线程数'].n = 爬取线程数
        _tqdm_panel['爬取线程数'].total = 爬取线程数
        _tqdm_panel['爬取线程数'].refresh()
        _tqdm_panel['当前epoch进度'].update(-_tqdm_panel['当前epoch进度'].n)
        _tqdm_panel['当前epoch进度'].total = len(q)
        for href in ThreadPoolExecutor(max_workers=max(1, round(爬取线程数))).map(超吸, q):
            n = len(href)
            for url in href:
                if url not in 吸过:
                    新q.append((url, 1 / n))

        # 在每个epoch结束时，处理剩余的待索引文档
        if _queue_waiting_index:
            batch_index_doc()

        if not 新q:
            print('队列空了，坏！')
            return
        上l = len(新q)
        q = 重整(新q)

        c = Counter([netloc(x) for x in q])
        for i in _url_distribution_tracking._buckets:
            i.set(0)
        for v in c.values():
            _url_distribution_tracking.observe(v)
        超c = Counter([shrink_url(x) for x in q])
        打点.append({
            'ep': ep,
            '上次抓到的长度': 上l,
            'url个数': len(q),
            '域名个数': len(c),
            '一级域名个数': len(超c),
            '各个域名的url个数': dict(c.most_common(20)),
            '各个一级域名的url个数': dict(超c.most_common(20)),
        })
        with open('打点.json', 'w', encoding='utf8') as f:
            f.write(json.dumps(打点, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    bfs(入口)
