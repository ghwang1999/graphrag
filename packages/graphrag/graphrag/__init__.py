# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License

"""The GraphRAG package."""

import os
import ssl
import json
import httpx
import aiohttp
import logging

# ==========================================
# 1. SSL 验证禁用 (连接层配置)
# ==========================================

# 1.1 标准库 SSL
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# 1.2 HTTPX Client Init (强制如果不传 verify 就默认为 False)
_orig_httpx_init = httpx.AsyncClient.__init__
def _new_httpx_init(self, *args, **kwargs):
    # 1. 禁用 SSL
    kwargs["verify"] = False
    
    # 2. 强制设置底层超时时间 (connect=60s, read/write/pool=600s)
    # 这样无论上层怎么传参，底层都会等待至少 10 分钟
    kwargs["timeout"] = httpx.Timeout(600.0, connect=60.0)
    
    _orig_httpx_init(self, *args, **kwargs)
httpx.AsyncClient.__init__ = _new_httpx_init

_orig_httpx_sync_init = httpx.Client.__init__
def _new_httpx_sync_init(self, *args, **kwargs):
    kwargs["verify"] = False
    # 同步客户端也加上超时
    kwargs["timeout"] = httpx.Timeout(600.0, connect=60.0)
    _orig_httpx_sync_init(self, *args, **kwargs)
httpx.Client.__init__ = _new_httpx_sync_init

# 1.3 AIOHTTP Init (用于 LiteLLM 可能的非 OpenAI 调用)
_orig_aiohttp_connector_init = aiohttp.TCPConnector.__init__
def _new_aiohttp_connector_init(self, *args, **kwargs):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    kwargs["ssl"] = ctx
    _orig_aiohttp_connector_init(self, *args, **kwargs)
aiohttp.TCPConnector.__init__ = _new_aiohttp_connector_init


# ==========================================
# 2. HTTPX Send 拦截 (数据层清洗)
# ==========================================
# 这是 OpenAI SDK 真正发送数据的出口，拦截这里有效。

_orig_async_send = httpx.AsyncClient.send
_orig_sync_send = httpx.Client.send

def _clean_request_content(request):
    """
    检查并清洗 Request 中的非法参数 (encoding_format, user)
    """
    try:
        # 仅针对cnai Embedding 服务 URL 进行拦截
        # 检查是否是发往 textembeddingservice 的 POST 请求
        # 注意：这里确保 url 判断准确
        if "textembeddingservice" in str(request.url) and request.method == "POST":
            # 获取原始 body (bytes)
            # 注意：request.content 会读取整个流，这对于在此处修改是必要的
            body_bytes = request.content
            if not body_bytes:
                return

            # 尝试解析 JSON
            try:
                data = json.loads(body_bytes)
            except json.JSONDecodeError:
                return # 不是 JSON，不做处理

            if isinstance(data, dict):
                changed = False
                
                # 移除服务器不支持的参数
                if "encoding_format" in data:
                    del data["encoding_format"]
                    changed = True
                
                if "user" in data:
                    del data["user"]
                    changed = True
                
                # 如果有修改，重新打包 Request
                if changed:
                    new_body = json.dumps(data).encode("utf-8")
                    
                    # 1. 更新内部 content 属性
                    request._content = new_body 
                    
                    # 2. 关键：更新 Content-Length 头
                    request.headers["Content-Length"] = str(len(new_body))
                    
                    # 3. 关键
                    # 重置 stream，否则 httpx 还是会发送旧的 body
                    # httpx.ByteStream 是 httpx 处理内存字节流的标准方式
                    request.stream = httpx.ByteStream(new_body)
                    
                    # print(f">>> [Patch] Cleaned parameters for {request.url}. Size: {len(body_bytes)} -> {len(new_body)}")
    except Exception as e:
        print(f">>> [Patch Warning] Failed to clean request: {e}")

async def _new_async_send(self, request, *args, **kwargs):
    # 在发送前清洗数据
    _clean_request_content(request)
    return await _orig_async_send(self, request, *args, **kwargs)

def _new_sync_send(self, request, *args, **kwargs):
    # 在发送前清洗数据
    _clean_request_content(request)
    return _orig_sync_send(self, request, *args, **kwargs)

# 应用拦截补丁
httpx.AsyncClient.send = _new_async_send
httpx.Client.send = _new_sync_send

print(">>> [GraphRAG Patch] Loaded: SSL Disabled & HTTPX Traffic Interceptor Active.")