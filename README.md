# Incident-to-Resolution Agent MVP

面向研发运维场景的可恢复故障处置 Agent。本版本只使用本地模拟运维数据，不连接真实生产系统，完成以下闭环：

```text
创建故障 → 分类与规划 → 查询日志/指标/Runbook → 诊断
→ 人工审批 → 模拟修复 → 恢复验证 → 生成总结
```

## MVP 特性

- 显式、有边界的 Agent 状态机：`PLANNING → INVESTIGATING → WAITING_APPROVAL → EXECUTING → VERIFYING`
- SQLite 持久化 incident 状态和事件日志，进程重启后可恢复查询
- 三类内置故障场景：数据库连接池、上游超时、发布回归
- 工具调用记录、失败重试、错误状态和 trace_id
- 所有修复动作必须经过人工审批
- 修复动作使用 idempotency key，并且只修改本地模拟状态
- 每个诊断结论都绑定日志、指标或 Runbook 证据
- 不需要模型 API Key 即可运行；配置 `AGENT_LLM_BASE_URL`、`AGENT_LLM_API_KEY` 和 `AGENT_LLM_MODEL` 后可使用 OpenAI-compatible Planner，模型不可用时自动回退到规则 Planner

## 快速启动

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload
```

可选的大模型配置：

```powershell
$env:AGENT_LLM_BASE_URL = "https://your-provider.example/v1"
$env:AGENT_LLM_API_KEY = "your-api-key"
$env:AGENT_LLM_MODEL = "your-model"
uvicorn app.main:app --reload
```

打开 <http://127.0.0.1:8000/docs> 查看交互式 API 文档。

## 演示请求

```powershell
$body = @{
  title = "checkout 数据库连接池耗尽"
  description = "线上 checkout 服务报数据库连接池耗尽，接口大量超时"
  service = "checkout-api"
  tenant_id = "demo"
} | ConvertTo-Json

$created = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/incidents `
  -ContentType "application/json" `
  -Body $body

$created.incident.id
$created.incident.status
$created.incident.evidence

$approval = @{ approved = $true; comment = "同意执行模拟修复" } | ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/incidents/$($created.incident.id)/approve" `
  -ContentType "application/json" `
  -Body $approval
```

查看事件轨迹：

```text
GET /incidents/{incident_id}/events
```

模拟一次工具瞬时失败并验证重试：

```json
{
  "title": "发布后错误率回归",
  "description": "新版本 release 后错误率上涨",
  "metadata": {"fail_tools": ["query_logs"]}
}
```

## 测试

```powershell
pytest -q
```

## 目录结构

```text
app/
  main.py          FastAPI API
  models.py        状态、事件、请求模型
  orchestrator.py  有边界的 Agent 编排和状态流转
  tools.py         本地安全模拟工具和故障场景
  store.py         SQLite 持久化边界
tests/
  test_mvp.py      核心链路、审批、重试、恢复测试
```

## 后续演进

1. 用 PostgreSQL Checkpointer 替换 SQLite Store。
2. 用真实 Log/Metrics/Ticket Provider 替换 `SimulatedOpsTools`。
3. 将当前显式状态机迁移到 LangGraph，或继续保留它作为可测试的编排层；模型 Planner 已通过适配器接入，保留当前状态边界、审批策略和工具接口。
4. 增加 SSE 事件流、RBAC、RAG、MCP 工具适配器和离线评测集。

