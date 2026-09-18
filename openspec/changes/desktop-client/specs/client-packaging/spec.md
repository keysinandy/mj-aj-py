# Spec Delta

## Purpose

定义客户端分发链路的行为契约:ONNX 模型导出与逐动作对拍、三平台 PyInstaller 制品的内容约束、内核加载断言与构建可重复性,保证制品在新机器上开箱即用且不携带敏感配置。

## ADDED Requirements

### Requirement: ONNX 导出与对拍
SHALL提供模型导出工具,覆盖三种 checkpoint 来源:BC(best.pt,75 平面输入)、PPO(net+action_net,91 平面输入、oracle 段置零)、policy-v3 value model。导出件SHALL携带输入契约元数据(观测平面数、动作空间维度)。SHALL提供对拍验证:在同一批观测与合法集上,torch 原实现与 ONNX 运行时选出的动作序列完全一致;对拍未通过的导出件MUST NOT进入制品。

#### Scenario: 三种来源全部可导出
- **WHEN** 对三种合法 checkpoint 分别执行导出
- **THEN** 各自产出带契约元数据的 ONNX 文件且对拍通过

#### Scenario: 对拍拦截不一致
- **WHEN** 某导出件在对拍中出现任一动作不一致
- **THEN** 导出流程报告失败,该文件不被打包

#### Scenario: 契约校验拒载
- **WHEN** 客户端加载输入平面数与自身观测契约不符的 ONNX 模型
- **THEN** 加载被拒绝并提示契约不匹配

### Requirement: 三平台制品内容
SHALL产出三份制品:win-x64、macos-arm64、macos-x64。制品MUST NOT携带 torch,SHALL包含 ONNX 运行时、预编译的本地计算内核(mj_kernels)与内置默认模型;服务启动时SHALL断言本地内核已加载,缺失时显式失败,MUST NOT静默回退纯 Python 实现。

#### Scenario: 制品自检
- **WHEN** 在任一目标平台首次启动制品内服务
- **THEN** 内核加载断言通过,界面显示就绪

#### Scenario: 内核缺失显式失败
- **WHEN** 制品内内核扩展损坏或缺失
- **THEN** 启动报告明确错误并停止,不进入慢速静默降级

#### Scenario: 制品不含 torch
- **WHEN** 检查任一制品的依赖清单与体积构成
- **THEN** 不存在 torch 运行时,推理仅依赖 ONNX 运行时

### Requirement: 构建可重复
SHALL提供一条命令(或 CI 任务)在三平台构建矩阵上产出全部制品;制品SHALL在无 Python 开发环境的干净目标机器上可运行:本地对局、回放等离线功能不依赖网络,线上功能仅需可达平台内网。三平台的 Python 运行时版本SHALL一致。

#### Scenario: 干净机器冒烟
- **WHEN** 将制品复制到未安装开发环境的目标机器并启动
- **THEN** 客户端可完成启动自检、跑一局本地对局并打开回放

#### Scenario: CI 一键三平台
- **WHEN** 触发构建流程
- **THEN** 三个平台的制品全部产出并通过统一冒烟检查

### Requirement: 分发安全
令牌与个人配置MUST NOT进入制品;macOS 制品SHALL至少 ad-hoc 签名,并附带 Gatekeeper 首次打开的操作说明。

#### Scenario: 制品无敏感信息
- **WHEN** 审计任一制品内容
- **THEN** 不含令牌、服务器私有配置或个人对局数据

#### Scenario: macOS 首开说明
- **WHEN** 用户在 macOS 首次打开未公证制品被 Gatekeeper 拦截
- **THEN** 随附文档说明右键打开或移除隔离标记的操作步骤
