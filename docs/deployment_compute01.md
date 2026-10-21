# 在 compute01 上运行单页 Demo（Docker）

基线：`2a72986`。目标机：`compute01`（`10.0.102.61`）。

## 当前部署

| 项 | 值 |
| --- | --- |
| 容器 | `llm-capacity-ui` |
| 镜像 | `llm-capacity-ui:v1.1`（`python:3.12-slim`，非 root，UID 10001） |
| 端口 | 宿主 `18501` → 容器 `8501` |
| 持久化 | `/opt/llm-capacity-ui/runs` → `/app/data/exports/system_demo` |
| 重启策略 | `unless-stopped` |
| 资源限制 | `--memory 2g`，不使用 GPU |
| 访问地址 | <http://10.0.102.61:18501>（局域网） |

容器内只跑离线归档路径：不联网、不调用 AISimulate、不写实测证据。
本地真实定价库 `data/local.db` **不进入同步目录、不进入镜像**（`.dockerignore` 与 rsync 双重排除）；
容器每次运行自己生成 `demo.db`（合成示例数据）。
每次生成在挂载目录下新建 `<UTC 时间戳>` 子目录，不覆盖历史结果。

## 构建与启动

```bash
# 1) 同步代码（在开发机执行；排除虚拟环境与内部产物）
rsync -az --delete \
  --exclude '.venv/' --exclude '.git/' --exclude '__pycache__/' \
  --exclude '.pytest_cache/' --exclude 'data/exports/' --exclude 'data/private/' \
  --exclude 'data/local.db*' --exclude '*.db' --exclude '*.db-journal' \
  --exclude 'data/raw/*.xlsx' --exclude 'data/raw/*.xls' --exclude 'docs/ui/' \
  ./ root@10.0.102.61:/opt/llm-capacity-ui/src/

# 2) 构建（在 compute01 上）
cd /opt/llm-capacity-ui/src
docker build -t llm-capacity-ui:v1.1 .

# 3) 启动
mkdir -p /opt/llm-capacity-ui/runs
chown 10001:10001 /opt/llm-capacity-ui/runs
docker rm -f llm-capacity-ui 2>/dev/null || true
docker run -d --name llm-capacity-ui --restart unless-stopped --memory 2g \
  -p 18501:8501 \
  -v /opt/llm-capacity-ui/runs:/app/data/exports/system_demo \
  llm-capacity-ui:v1.1
```

## 验证

```bash
curl -fsS http://127.0.0.1:18501/_stcore/health     # 期望：ok
docker inspect llm-capacity-ui --format '{{.State.Health.Status}}'
ls -lt /opt/llm-capacity-ui/runs | head
```

本次实测：容器 `healthy`，内存约 105 MiB，CPU <1%；
从开发机访问 `http://10.0.102.61:18501` 返回 200，并成功跑完一次离线生成。

## 关于“公网可访问”

`compute01` 只有内网地址 `10.0.102.61`，没有直接分配公网 IP。
公网 `112.29.111.158:8080/8443` 由已有的 nginx 代理（yolo-demo）占用，
且裸 IP 访问返回 403；`18501` 没有做端口映射。

因此当前可用地址是局域网地址。要让公网访问，需要在网关/防火墙上
把 `18501` 映射到 `10.0.102.61:18501`，或在既有 nginx 上新增一条经过
审批的反向代理路由。**未修改现有 nginx 配置**，以免影响 yolo-demo。

该 Demo 没有登录与访问控制，只适合内网演示；不要在未加鉴权的情况下直接暴露到公网。

## 更新流程

```bash
rsync ...                       # 同上
cd /opt/llm-capacity-ui/src
docker build -t llm-capacity-ui:v1.1 .
docker rm -f llm-capacity-ui
docker run ...                  # 同上
```

历史运行目录在 `/opt/llm-capacity-ui/runs`，重建容器不会删除。
