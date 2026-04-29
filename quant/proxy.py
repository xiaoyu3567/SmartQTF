import os
from typing import Dict, Optional
from urllib import request


DEFAULT_PROXY_URL = "http://127.0.0.1:7890"
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def proxy_enabled(default: bool = False) -> bool:
    value = os.getenv("SMARTQTF_USE_PROXY")
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def proxy_url(default: str = DEFAULT_PROXY_URL) -> str:
    return os.getenv("SMARTQTF_PROXY_URL", default).strip() or default


def proxy_mapping(url: Optional[str] = None) -> Dict[str, str]:
    selected_url = url or proxy_url()
    return {
        "http": selected_url,
        "https": selected_url,
    }


def build_proxy_opener(url: Optional[str] = None):
    return request.build_opener(request.ProxyHandler(proxy_mapping(url)))


def configure_process_proxy(enabled: Optional[bool] = None, url: Optional[str] = None) -> bool:
    should_enable = proxy_enabled() if enabled is None else enabled
    if not should_enable:
        return False

    selected_url = url or proxy_url()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ[name] = selected_url
    os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
    os.environ.setdefault("no_proxy", os.environ["NO_PROXY"])
    return True
