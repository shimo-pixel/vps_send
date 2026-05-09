# vps_send

在 VPS 上自建 **Headscale** 控制面，Linux 节点用 **Docker 版 Tailscale** 注册进该控制面，形成私有 tailnet。仓库内还提供可选的 **Centrifugo** 实时服务，以及 **MinIO + Flask** 的上传与下载 API。

Tailscale 客户端连接的是你的 Headscale 地址（`--login-server` / `server_url`），**不是**官方 `login.tailscale.com`。

---

## 仓库结构


| 路径                                 | 说明                                                     |
| ---------------------------------- | ------------------------------------------------------ |
| `docker/docker-compose-server.yml` | VPS：仅启动 Headscale，对外 HTTP **8080**                     |
| `docker/docker-compose-client.yml` | Linux 节点：容器名 **tailscale-node**，读 `docker/.env.client` |
| `docker/docker-compose-chat.yml`   | 可选：Centrifugo，映射 **8000**                              |
| `docker/config.json`               | Centrifugo 配置（上线前替换所有默认密钥与口令）                          |
| `docker/.env.client.example`       | 客户端环境变量模板 → 复制为 `.env.client`                          |
| `headscale/config/config.yaml`     | Headscale 主配置；`**server_url` 必须改为你的公网可达地址**            |
| `headscale/data/`                  | Headscale 运行时数据（勿提交到公开仓库）                              |
| `Tailscale/state/`                 | 各节点 Tailscale 状态目录（由客户端 Compose 挂载）                    |
| `minio-flask-api/`                 | MinIO + Flask API 的 `docker-compose.yml` 与 `app.py`    |


---

## 一、服务端：启动 Headscale

在**已克隆本仓库的 VPS**上，从仓库根目录执行：

```bash
cd docker
docker compose -f docker-compose-server.yml up -d
```

1. 编辑 `headscale/config/config.yaml`，将 `server_url` 设为客户端能访问的地址，例如 `http://你的公网IP或域名:8080`。
2. 在容器内初始化用户与预授权密钥（示例用户名为 `mynet`，可按需修改）：

```bash
docker exec headscale headscale users create mynet
docker exec headscale headscale preauthkeys create --user mynet --reusable --expiration 24h
```

记下输出的 **preauth key**，填入各节点的 `docker/.env.client` 中的 `TS_AUTHKEY`。勿将真实密钥贴进文档或公开 Git。

---

## 二、客户端：把 Tailscale 加入 Headscale

### 1. 地址保持一致

以下应指向**同一** Headscale URL（协议、主机、端口一致）：

- `headscale/config/config.yaml` 的 `server_url`
- `docker/.env.client` 里的 `TS_LOGIN_SERVER` 以及 `TS_EXTRA_ARGS` 中的 `--login-server=...`

### 2. 配置环境文件并启动容器

```bash
cd docker
cp .env.client.example .env.client
# 编辑 .env.client：TS_AUTHKEY、TS_LOGIN_SERVER、TS_EXTRA_ARGS 与上一步 server_url 一致
docker compose -f docker-compose-client.yml up -d
```

状态目录挂载为仓库下的 `Tailscale/state/`。镜像使用 `host` 网络，需本机具备 `/dev/net/tun` 等条件（见 Compose 内 `cap_add` / `privileged`）。

### 3. 手动执行 `tailscale up`（推荐掌握，便于排错与重新登录）

在客户端容器**已运行**的前提下，可在节点上执行（将 `<你的VPS_IP>` 换成与 `server_url` 一致的主机部分，或使用域名）：

```bash
docker exec -it tailscale-node tailscale up --login-server http://<你的VPS_IP>:8080 --reset
```

说明：

- `**--reset**`：丢弃该容器内已有节点状态，适合换控制面、换密钥或排错后重来。
- 若未使用预授权密钥或需要交互授权，命令会给出 **URL**，在浏览器中打开并完成 Headscale 侧的注册流程；若已配置有效的 `TS_AUTHKEY` 且容器入口脚本已自动 `up`，仍可用本条命令强制指定 `login-server` 或配合 `--reset` 重建会话。

容器名必须为 **tailscale-node**（与本仓库 `docker-compose-client.yml` 中 `container_name` 一致）。

### 4. 在 Headscale 上确认节点

在 VPS 上：

```bash
docker exec headscale headscale nodes list
```

应能看到各节点的 Hostname 与 tailnet IP。若节点处于待批准状态，请按当前 Headscale 版本文档执行 **register / approve**；使用未过期、且绑定用户的 **preauth key** 时，多数情况下会自动出现在列表中。

---

## 三、可选：Centrifugo

```bash
cd docker
docker compose -f docker-compose-chat.yml up -d
```

默认 **http://主机:8000**。务必修改 `docker/config.json` 内的 `hmac_secret_key`、`http_api.key`、`admin.password` 等，并收紧 `allowed_origins` 与匿名发布策略；详见 [Centrifugo 文档](https://centrifugal.dev/)。

---

## 四、MinIO 与 Flask 上传 API

```bash
cd minio-flask-api
cp .env.example .env
docker compose up -d
```

- MinIO S3：默认宿主机 **9000**，控制台 **9001**（端口见 `.env.example`）。
- Flask：默认 **5000**。Compose 内已为 API 容器设置 `MINIO_ENDPOINT=minio:9000`，勿在容器场景使用 `127.0.0.1` 指向 MinIO。

接口摘要（实现见 `minio-flask-api/app.py`）：


| 方法     | 路径                | 说明                                               |
| ------ | ----------------- | ------------------------------------------------ |
| `GET`  | `/health`         | 健康检查与 MinIO 连通性                                  |
| `POST` | `/upload`         | `multipart/form-data`，字段 `file`；可选 `object_name` |
| `GET`  | `/objects/<path>` | 下载对象；`?disposition=attachment` 时为附件下载            |


生产环境建议在网关启用 HTTPS，并限制 MinIO 控制台与 API 的暴露范围。

---

## 五、安全与运维

1. **密钥**：`.env.client`、`config.json`、`headscale/data`、MinIO 根账号等均为敏感信息；泄露后应轮换 preauth key 与所有静态口令。
2. **控制面**：Headscale 的 8080 若对公网开放，建议配合防火墙、TLS 反向代理或 IP 白名单。
3. **备份**：定期备份 `headscale/data` 与 Docker 卷 `minio-data`。

---

## 六、其它协作方式（非本仓库 Compose）

在 tailnet 打通后，成员之间仍可选用任意内网可达的工具传文件或聊天（例如自行部署 **ssh-chat**、或使用 **croc** 等），与本仓库 Headscale / MinIO 组件独立，按需自行编排即可。

---

## 七、Tailscale 排错与常见误解

### 1. `.env.client` 里该填什么地址

`TS_LOGIN_SERVER` 与 `TS_EXTRA_ARGS` 中的 `--login-server` 必须与 `headscale/config/config.yaml` 的 **`server_url` 一致**，含义是：**本机上的 Tailscale 客户端要去哪里访问 Headscale 控制面**。

- 客户端与 Headscale **不在同一台机器**（例如在 WSL / 家用电脑，Headscale 在 VPS）：应填 **VPS 的公网 IP 或域名**（例如 `http://你的VPS:8080`），**不要**填仅表示本机的 `127.0.0.1`，也**不要**填 Tailscale 虚拟网 IP（`100.x`）。
- 客户端与 Headscale **同机**（控制面与 `docker-compose-client.yml` 跑在一起）：才可用 `http://127.0.0.1:8080`（与仓库内 `.env.client.example` 一致）。

### 2. 多台机器上出现相同的 Tailscale IP（`100.x`）

同一 tailnet 内，**正常每台节点应有不同的 `100.x` 地址**。若不同物理机却看到相同 IP，常见原因是 **共用了同一份节点身份数据**：本仓库将 `../Tailscale/state` 挂载为 `/var/lib/tailscale`，若从另一台机器 **复制、同步网盘或误用同一份 `Tailscale/state`**，会表现为同一逻辑节点，IP 会相同。换 preauth key 或 `tailscale up --reset` **无法**改变已写入 state 的机器密钥。

处理：在需要独立身份的那台机器上 **停止客户端容器**，**清空该机器仓库内 `Tailscale/state/` 下的内容**（勿使用从其它节点拷来的目录），再在 Headscale 中按需删除旧节点后，用新 preauth key 重新 `docker compose up` 并完成登录。

### 3. `tailscale status` 与「谁能 ping 谁」

- 标记为 **`offline`** 的节点当前不在线，**无法 ping 通**属正常。
- **`idle`** 且带有收发包统计的节点一般表示在线；若系统 `ping` 仍不通，见下一小节。
- 当前执行命令的节点可用 `docker exec -it tailscale-node tailscale status --self` 查看。
- 若 **仅有两台特定机器互相 ping 不通**，但彼此都能 ping 其它节点：优先检查 **Headscale ACL** 是否按标签/分组限制了这两台之间的流量；其次检查 **两台机器上是否对 ICMP 做了不同策略**。

### 4. `tailscale ping` 与系统 `ping`（ICMP）

二者不是同一种探测：

- **`tailscale ping`**：由 Tailscale 在隧道内发起，**不是内核 ICMP**。若对某 `100.x` **能 `tailscale ping` 通**，通常说明 **WireGuard 隧道与路由正常**，ACL 大面上也允许这两台通信。
- **`ping`（ICMP）**：走系统 ICMP，可能被 **`iptables` / `nftables` / `ufw`、云厂商安全组** 或 **Windows 防火墙**（若从 Windows 侧发 ICMP）丢弃，从而出现 **「`tailscale ping` 正常但 `ping` 不通」**。

`tailscale ping` 输出含义简述：

- **`via 公网IP:端口`、延迟较低**：与对端 **直连 UDP**（打洞成功）。
- **`via DERP(地区)`、延迟较大**：经 **DERP 中继**，常见于对称 NAT 等导致直连困难的环境；属于路径差异，不一定表示故障。

**验证与修复（ICMP）**：当 **`tailscale ping` 对某节点正常、系统 `ping` 仍不通** 时，到 **对端或本机**（视哪一侧丢弃 ICMP）上临时排查；确认原因后再改为发行版推荐的永久规则（如 `iptables-save` / `nftables` 持久化），勿长期依赖临时插入的规则。

```bash
# 查看 INPUT 是否丢弃 ICMP（示例，按实际防火墙调整）
sudo iptables -L INPUT -n -v | head -50
# 若使用 nftables
sudo nft list ruleset | head -80
```

仅用于**验证是否为 ICMP 被拦**：在 Linux 上可临时允许从 Tailscale 网卡入站的 ICMP（网卡名一般为 `tailscale0`，以 `ip link` 为准；重启或规则刷新后可能失效）。

```bash
sudo iptables -I INPUT 1 -i tailscale0 -p icmp -j ACCEPT
```

若本机用 **nftables**，请用等价的 `nft` 规则替代上述 `iptables` 命令。从 **WSL 外 Windows 侧** 发起的 `ping` 还需核对 **Windows 防火墙** 是否拦截 ICMP。

### 5. WSL 与 Linux 服务器互通

在 **WSL** 中部署本仓库的 Tailscale 客户端（含 Docker 方式）**可以**与 tailnet 内的 **Linux 服务器** 正常通信；若不通，请在本机核对 **`/dev/net/tun`、容器网络模式、Docker 日志**，并排除 **Windows / WSL 防火墙** 与上文 **ICMP** 被拦的情况。

