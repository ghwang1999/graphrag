# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License

"""The GraphRAG package."""
# --- BEGIN FINAL CONFIG (PROTOCOL FIX + EXPLICIT ROUTING) ---
import os
import ssl
import json
import httpx
import logging

# ==========================================
# 1. 配置常量
# ==========================================
# 华为代理地址
PROXY_URL = "http://w00937947:HWNaruto0512%3F@proxycn2.huawei.com:8080"
# 内网 Embedding 服务 IP
INTERNAL_HOST = "10.137.19.4"

# 清除环境变量中的代理设置，防止干扰
if "HTTP_PROXY" in os.environ: del os.environ["HTTP_PROXY"]
if "HTTPS_PROXY" in os.environ: del os.environ["HTTPS_PROXY"]
if "NO_PROXY" in os.environ: del os.environ["NO_PROXY"]

print(f">>> [GraphRAG Config] Proxy: Huawei, Bypass: {INTERNAL_HOST}")


# ==========================================
# 2. SSL 验证禁用 (全局)
# ==========================================
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context


# ==========================================
# 3. HTTPX Init 拦截 (核心路由逻辑)
# ==========================================
_orig_httpx_init = httpx.AsyncClient.__init__

def _new_httpx_init(self, *args, **kwargs):
    # 1. 基础设置
    kwargs["verify"] = False
    if "transport" in kwargs: del kwargs["transport"]
    if "proxies" in kwargs: del kwargs["proxies"]
    if "mounts" in kwargs: del kwargs["mounts"]

    # 2. 创建传输通道 (Transport)
    # 通道 A: 代理通道 (用于外网)
    try:
        tr_proxy = httpx.AsyncHTTPTransport(proxy=PROXY_URL, verify=False)
    except TypeError:
        tr_proxy = httpx.AsyncHTTPTransport(verify=False)

    # 通道 B: 直连通道 (用于内网)
    tr_direct = httpx.AsyncHTTPTransport(verify=False)

    # 3. 配置路由表
    mounts = {
        "http://": tr_proxy,
        "https://": tr_proxy,
        f"http://{INTERNAL_HOST}": tr_direct,
        f"https://{INTERNAL_HOST}": tr_direct,
    }
    
    kwargs["mounts"] = mounts
    _orig_httpx_init(self, *args, **kwargs)

httpx.AsyncClient.__init__ = _new_httpx_init

# 同步 Client
_orig_httpx_sync_init = httpx.Client.__init__
def _new_httpx_sync_init(self, *args, **kwargs):
    kwargs["verify"] = False
    if "transport" in kwargs: del kwargs["transport"]
    if "proxies" in kwargs: del kwargs["proxies"]
    
    tr_proxy = httpx.HTTPTransport(proxy=PROXY_URL, verify=False)
    tr_direct = httpx.HTTPTransport(verify=False)
    
    mounts = {
        "http://": tr_proxy,
        "https://": tr_proxy,
        f"http://{INTERNAL_HOST}": tr_direct,
        f"https://{INTERNAL_HOST}": tr_direct,
    }
    kwargs["mounts"] = mounts
    _orig_httpx_sync_init(self, *args, **kwargs)
httpx.Client.__init__ = _new_httpx_sync_init


# ==========================================
# 4. HTTPX Send 拦截 (协议级参数清洗)
# ==========================================

_orig_async_send = httpx.AsyncClient.send
_orig_sync_send = httpx.Client.send

def _clean_request_content(request):
    try:
        # 仅针对内网 Embedding POST 请求
        if "textembeddingservice" in str(request.url) and request.method == "POST":
            body_bytes = request.content
            if not body_bytes: return
            try:
                data = json.loads(body_bytes)
            except json.JSONDecodeError: return
            
            if isinstance(data, dict):
                changed = False
                if "encoding_format" in data:
                    del data["encoding_format"]; changed = True
                if "user" in data:
                    del data["user"]; changed = True
                
                if changed:
                    new_body = json.dumps(data).encode("utf-8")
                    
                    # [关键修复] 必须同时更新以下三项，缺一不可！
                    
                    # 1. 更新内容缓存
                    request._content = new_body 
                    
                    # 2. 更新 Content-Length 头
                    request.headers["Content-Length"] = str(len(new_body))
                    
                    # 3. [新增] 更新数据流迭代器
                    # 之前的报错就是因为缺少这一行，导致发送了旧的长数据
                    request.stream = httpx.ByteStream(new_body)
                    
                    # print(">>> [GraphRAG Patch] Request body cleaned and stream reset.")
    except Exception as e:
        print(f">>> [GraphRAG Patch Error] {e}")

async def _new_async_send(self, request, *args, **kwargs):
    _clean_request_content(request)
    return await _orig_async_send(self, request, *args, **kwargs)

def _new_sync_send(self, request, *args, **kwargs):
    _clean_request_content(request)
    return _orig_sync_send(self, request, *args, **kwargs)

httpx.AsyncClient.send = _new_async_send
httpx.Client.send = _new_sync_send

print(">>> [GraphRAG Patch] Loaded: Routes(Proxy/Direct), SSL(Off), StreamFixed.")
# --- END FINAL CONFIG ---