# 在 compute01 上运行单页 Demo（Docker）

目标机：`compute01`（`10.0.102.61`）。本文件对应 U 阶段 Demo 发布版本。

## 当前部署

| 项 | 值 |
| --- | --- |
| 源码基线 | `b40a637` + U 阶段提交（见 `git log`） |
| 镜像 | `llm-capacity-ui:v1.3` |
| 容器 | `llm-capacity-ui` |
| 端口 | 宿主 `18501` → 容器 `8501` |
| 持久化 | `/opt/llm-capacity-ui/runs` → `/app/data/exports/system_demo` |
| 重启策略 | `unless-stopped` |
| 资源限制 | `--memory 2g`，不使用 GPU |
| 运行用户 | UID 10001（非 root） |
| 访问地址 | <http://10.0.102.61:18501>（局域网） |

容器只跑离线归档路径：不联网、不调用 AISimulate、不写实测证据。
每次生成在挂载目录下新建 `<UTC 时间戳>` 子目录，不覆盖历史结果。

## 构建

```bash
# 1) 同步代码（开发机执行；排除虚拟环境、内部产物与本地数据库）
rsync -az --delete \
  --exclude '.venv/' --exclude '.git/' --exclude '__pycache__/' \
  --exclude '.pytest_cache/' --exclude 'data/exports/' --exclude 'data/private/' \
  --exclude 'data/local.db*' --exclude '*.db' --exclude '*.db-journal' \
  --exclude 'data/raw/*.xlsx' --exclude 'data/raw/*.xls' --exclude 'docs/ui/' \
  ./ root@10.0.102.61:/opt/llm-capacity-ui/src/

# 2) 构建（compute01）
cd /opt/llm-capacity-ui/src
docker build -t llm-capacity-ui:v1.3 .
```

依赖可复现性：

- `pyproject.toml` 的核心依赖全部是精确版本；
- `requirements-app.txt` 固定 `streamlit==1.66.0`；
- 基线镜像 `python:3.12-slim` 在本次构建时解析为 Python 3.12.15；
  如需完全可复现，可在 Dockerfile 中把 `FROM` 固定到该次构建的镜像 digest。

## 启动

```bash
mkdir -p /opt/llm-capacity-ui/runs
chown 10001:10001 /opt/llm-capacity-ui/runs
docker rm -f llm-capacity-ui 2>/dev/null || true
docker run -d --name llm-capacity-ui --restart unless-stopped --memory 2g \
  -p 18501:8501 \
  -v /opt/llm-capacity-ui/runs:/app/data/exports/system_demo \
  llm-capacity-ui:v1.3
```

## 健康检查

```bash
curl -fsS http://127.0.0.1:18501/_stcore/health     # 期望：ok
docker inspect llm-capacity-ui --format '{{.State.Health.Status}}'
docker logs --tail 20 llm-capacity-ui
ls -lt /opt/llm-capacity-ui/runs | head
```

## 更新

```bash
# 同步新代码后重新构建一个递增 tag，保留旧镜像用于回滚
rsync ...                       # 同上
cd /opt/llm-capacity-ui/src
docker build -t llm-capacity-ui:v1.4 .
docker rm -f llm-capacity-ui
docker run -d ... llm-capacity-ui:v1.4   # 启动参数同上
```

## 回滚

```bash
docker rm -f llm-capacity-ui
docker run -d --name llm-capacity-ui --restart unless-stopped --memory 2g \
  -p 18501:8501 \
  -v /opt/llm-capacity-ui/runs:/app/data/exports/system_demo \
  llm-capacity-ui:v1.2
```

回滚只切换镜像；`/opt/llm-capacity-ui/runs` 中的历史报告不会被删除。
构建新镜像时不要使用 `docker system prune -a` 清理旧 tag。

## 数据边界

- `data/local.db`（本地真实定价库）不进入同步目录、不进入镜像；
- `data/exports/`、`data/private/`、`data/raw/*.xlsx` 同样排除；
- 容器每次运行自己生成 `demo.db`（合成示例数据），位于挂载目录；
- 镜像内验证：`docker run --rm --entrypoint sh <image> -c 'find /app -name "*.db"'`
  应无输出。

## 访问控制

该 Demo **没有登录与鉴权**，只适合受控内网演示：

- 当前只监听局域网地址；`compute01` 没有直接分配公网 IP；
- 公网 `8080/8443` 由既有 nginx（yolo-demo）占用，未改动；
- 未配置 `18501` 的公网映射。若确需公网访问，必须先加访问控制
  （网关白名单、反向代理鉴权或 VPN），不要直接端口映射。

## 部署记录

| 项 | 值 |
| --- | --- |
| 源码提交 | `e56b464`（U 阶段） |
| 镜像 | `llm-capacity-ui:v1.3` |
| 镜像 ID | `sha256:633dbcb2aab329badaeb33d19d4a0e11e5e109d07509a45f32ff587cacf35d40` |
| 构建时间 | 2026-10-21 14:39:36（UTC+8） |
| 运行时 | Python 3.12.15 / Streamlit 1.66.0 |
| 健康检查 | `healthy`，`/_stcore/health` = `ok` |
| 远端实跑 | 宽松 SLA 预设生成成功，显示 `TP8×PP1×DP1` 与身份推导说明 |
| 回滚镜像 | `llm-capacity-ui:v1.2`（`sha256:fc84e123e27b5463a128b5f30d4c3f846e87bb61e5beb2dd0590da64c483521a`） |

镜像内未发现 `*.db`；运行产物在 `/opt/llm-capacity-ui/runs`。
