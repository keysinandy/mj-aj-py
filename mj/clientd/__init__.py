"""Clientd:桌面客户端的 Python sidecar 服务层。

本地 HTTP(控制面)+ WebSocket(数据面),托管本地竞技场/回放/线上
会话控制。纯标准库 + websockets + numpy;torch 仅策略推理路径按需加载。
"""