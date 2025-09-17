from dataclasses import dataclass, field, asdict

from 存储 import 融合之门
from 配置 import 存储位置
from typing import MutableMapping, Dict, List, Any, Optional


_website_info = 融合之门(存储位置/'网站之门')


@dataclass
class Website:
    visit_count: int = 0
    last_visit_time: int = 0
    feature: Any = None
    success_rate: Optional[float] = None
    structure: Optional[str] = None
    ip: Optional[List[str]] = None
    quality: Optional[float] = None
    https_valid: Optional[bool] = None
    keywords: Optional[List[str]] = None
    link: List[str] = field(default_factory=list)
    lang_type: Dict[str, float] = field(default_factory=dict)
    redirect: Dict[str, str] = field(default_factory=dict)
    server_type: List[str] = field(default_factory=list)


class _MapWebsite(MutableMapping[str, Website]):
    def __getitem__(self, k: str):
        d = _website_info.get(k) or {}
        d = {k: v for k, v in d.items() if k in Website.__dataclass_fields__ and v is not None}
        return Website(**d)

    def __setitem__(self, k: str, v:Website):
        _website_info[k] = asdict(v)

    __iter__ = __len__ = __delitem__ = lambda: 0/0


map_website = _MapWebsite()
