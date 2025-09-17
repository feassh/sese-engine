import json
import math
from pathlib import Path
from typing import Union

import yaml

from utils import 分解
from 配置 import 存储位置, 反向链接基准值


def _normalize(d):
    q = [v for k, v in d.items() if '/' not in k]
    sum_energy = sum(q)
    倍 = 反向链接基准值/sum_energy
    return {k: v*倍 for k, v in d.items()}


def prosperity_data() -> dict:
    if not (存储位置/'繁荣.json').is_file():
        return {}
    with open(存储位置/'繁荣.json', encoding='utf8') as f:
        d = json.load(f)
        if len(d) == 0:
            return {}
    d = _normalize(d)
    for k, v in d.items():
        now = k
        while True:
            now = '.'.join(now.split('.')[1:])
            if now not in d:
                break
            if d[now] < v:
                d[now] = v
    return d


def adjustment_data() -> dict:
    if not (Path('./data')/'调整.yaml').is_file():
        return {}
    with open(Path('./data')/'调整.yaml', encoding='utf8') as f:
        return yaml.safe_load(f)


def block_words() -> set:
    path = Path('./data')/'屏蔽词.json'
    if not path.is_file():
        return []
    return {*json.load(open(path, encoding='utf8'))}


_prosperity_data = prosperity_data()
def prosperity(url: str) -> Union[int, float]:
    s = 0
    for i in 分解(url):
        if t := _prosperity_data.get(i):
            l = math.log2(2+t*2) - 1
        else:
            l = 0
        if s == 0:
            if l == 0:
                return 0
            s = l
        else:
            s = l + math.log((s-l)/2+1)
    return 0.1 + s
