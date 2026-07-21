# 毛发计数 Web 应用

这是一套可直接部署的 Web 版本：

```text
浏览器 HTML / JavaScript
        ↓ HTTPS / Nginx
FastAPI JSON API
        ↓
OpenCV 计数算法 + SQLite 账户账务
```

服务器只返回目标坐标、数量、启发式评分和本次扣费信息；画框、编号、补点、删除和导出均在浏览器完成。API 不返回 Base64 标注图片，也不把上传图片写入磁盘。SQLite 只保存账号、会话、识别结果 JSON 和计费流水。

## 功能

- 拍照、上传或拖拽图片；
- 浏览器端统一 EXIF 方向；
- 超大图片自动缩小到服务端限制；
- OpenCV 自动识别和计数；
- 批量上传多张图片，逐张识别和计费；
- 自动估算毛发簇中的平行毛发数量；
- 管理员创建机构账号，并在按量计费与 SVIP 买断之间转换；
- 按量客户按服务端自动识别根数从预付余额扣费；
- SVIP 客户不检查余额、不扣费，仍完整记录识别根数；
- Cookie 登录会话和请求幂等防重复扣费；
- 管理员查看余额、累计根数、消费和最近流水；
- Canvas 绘制编号和目标框；
- 人工补点、删除和恢复；
- 下载修正后的 PNG 和 JSON，并将批量 Excel、JSON 与标注图打包为完整结果 ZIP；
- 上传文件大小、像素数量和格式校验；
- Nginx 请求限速、并发限制和安全响应头；
- Docker Compose 一键部署。

批量识别完成后，“下载完整结果包”会生成 `hair-count-batch-results.zip`。压缩包内包含 `批量结果.xlsx`、保留完整识别明细的 `批量结果.json`，以及成功图片对应的 `标注图/` 目录。Excel 同时提供“逐图结果”和按逻辑毛发逐根展开的“标记明细”；失败或尚未处理的图片仍会保留在逐图汇总中。

## 快速启动

安装 Docker Desktop 或 Docker Engine，然后执行：

```bash
cp .env.example .env
```

编辑 `.env`，至少修改首次管理员密码：

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=replace-with-a-strong-password
```

默认使用 DaoCloud 的 Docker Hub 代理和阿里云 PyPI 镜像。若网络环境可直接访问官方源，可在 `.env` 改为：

```dotenv
DOCKER_REGISTRY=docker.io
PIP_INDEX_URL=https://pypi.org/simple
```

然后启动：

```bash
docker compose up --build -d
```

打开：

```text
http://127.0.0.1:8080
```

使用 `.env` 中的管理员账号登录。首次启动会创建管理员；数据库中已有管理员后，后续启动不会覆盖账号或密码。

查看状态：

```bash
curl http://127.0.0.1:8080/healthz
```

停止：

```bash
docker compose down
```

## API

### 健康检查

```http
GET /api/health
```

### 图片计数

```http
POST /api/count
Content-Type: multipart/form-data
```

查询参数：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `threshold_offset` | `0` | 越大越严格，越小越灵敏 |
| `min_contrast` | `35` | 最低局部对比度 |
| `exclude_border` | `false` | 是否排除边缘目标 |

请求必须携带登录 Cookie 和本次操作唯一的 `Idempotency-Key`。同一个账号重复提交同一个 Key 时返回原结果且不再次扣费；重新点击识别会生成新 Key 并重新收费。

示例：

```bash
curl -c cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"username":"clinic-a","password":"your-password"}' \
  http://127.0.0.1:8080/api/auth/login

curl -X POST \
  "http://127.0.0.1:8080/api/count?threshold_offset=0&min_contrast=35&exclude_border=false" \
  -b cookies.txt \
  -H "Idempotency-Key: request-20260714-0001" \
  -F "file=@your-image.jpg"
```

响应只包含 JSON：

```json
{
  "count": 53,
  "threshold": 51.0,
  "image_width": 360,
  "image_height": 288,
  "processing_ms": 18.4,
  "items": [
    {
      "id": 1,
      "bbox": [0, 15, 13, 9],
      "center": [5.66, 18.85],
      "area": 87,
      "contrast": 77.0,
      "confidence": 0.906,
      "partial": true,
      "strand_count": 1,
      "split_confidence": 1.0
    }
  ],
  "billing": {
    "request_id": "request-20260714-0001",
    "billable_count": 53,
    "unit_price_fen": 10,
    "charged_amount_fen": 530,
    "balance_fen": 9470,
    "plan": "standard"
  }
}
```

`confidence` 是依据目标面积和局部对比度计算的启发式评分，`split_confidence` 是簇内数量的启发式评分，二者都不是机器学习概率。总数为所有 `strand_count` 之和。金额字段统一使用整数“分”。按量客户余额不足返回 `402`，不扣款也不返回识别结果。SVIP 响应的 `plan` 为 `svip`，单价和扣费均为 `0`，余额保持不变。

### 批量图片计数

```http
POST /api/count/batch
Content-Type: multipart/form-data
```

查询参数与单张计数相同。请求体包含多个 `files` 字段，最多 `MAX_BATCH_SIZE`（默认 10）张图片。每张图片独立结算和使用幂等缓存，单张失败不影响其余图片。按量客户余额不足时停止处理剩余图片；SVIP 不受余额限制。若处理期间管理员切换客户类型，每张图片按当时类型结算。

```bash
curl -X POST \
  "http://127.0.0.1:8080/api/count/batch?threshold_offset=0&min_contrast=35" \
  -b cookies.txt \
  -H "Idempotency-Key: batch-20260714-0001" \
  -F "files=@image1.jpg" \
  -F "files=@image2.jpg"
```

响应示例：

```json
{
  "batch_id": "batch-20260714-0001",
  "total_count": 106,
  "total_charged_fen": 1060,
  "balance_fen": 8940,
  "succeeded": 2,
  "failed": 0,
  "results": [
    { "index": 0, "filename": "image1.jpg", "result": { "count": 53, "..." : "..." } },
    { "index": 1, "filename": "image2.jpg", "result": { "count": 53, "..." : "..." } }
  ],
  "errors": []
}
```

### 账户与管理接口

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| `POST` | `/api/auth/login` | 公开 | 登录并设置安全 Cookie |
| `POST` | `/api/auth/logout` | 登录用户 | 注销当前会话 |
| `GET` | `/api/me` | 登录用户 | 当前账号、类型、余额和单价 |
| `GET/POST` | `/api/admin/accounts` | 管理员 | 查询或创建按量/SVIP 机构账号 |
| `PATCH` | `/api/admin/accounts/{id}` | 管理员 | 修改名称、类型、单价和状态 |
| `POST` | `/api/admin/accounts/{id}/balance-adjustments` | 管理员 | 充值或冲减余额 |
| `POST` | `/api/admin/accounts/{id}/password` | 管理员 | 重置密码并清理会话 |
| `GET` | `/api/admin/ledger` | 管理员 | 查询最近计费流水 |
| `GET` | `/api/admin/audit` | 管理员 | 查询管理操作审计 |

账户 `plan` 可取 `standard` 或 `svip`，创建时默认为 `standard`。升级为 SVIP 会保留并冻结原余额和单价，现有会话不失效；恢复按量后继续使用原值，也可在降级请求中同时设置新单价。SVIP 期间不能调整余额或修改单价。

## 安全与资源限制

默认配置：

- 上传上限：20 MB；
- 最大总像素：20,000,000；
- 最大单边：6000 像素；
- 单 IP：约 12 次识别请求/分钟，可突发 4 次；
- 单 IP 同时最多 2 个 API 连接；
- 后端处理并发默认 2；
- 单次处理超时 45 秒；
- 批量处理最多 10 张图片/次；
- 图片全程在内存中处理，不落盘；
- 密码使用 PBKDF2-SHA256 加盐哈希；会话随机令牌仅以 SHA-256 摘要保存；
- 账号停用和密码重置会使原有会话失效；
- SQLite 位于 Docker 命名卷 `hair_data`，容器重启或重建后保留账务数据。

生产环境建议把 Nginx 放在 HTTPS 网关之后，并设置 `SECURE_COOKIES=true`。不要继续使用 `.env.example` 中的示例管理员密码。

## 目录结构

```text
hair-counter-web/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── counter.py
│   │   ├── database.py
│   │   ├── image_io.py
│   │   ├── config.py
│   │   └── schemas.py
│   ├── Dockerfile
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   └── Dockerfile
├── deploy/nginx.conf
├── docker-compose.yml
├── .env.example
├── run_dev.py
└── tests/
    ├── test_counter.py
    ├── test_billing.py
    └── test_api.py
```

## 不使用 Docker 的本地调试

安装后端依赖：

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
```

配置本地管理员和数据库：

```bash
export DATABASE_PATH=/tmp/hair-counter.db
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=replace-with-a-strong-password
```

然后在同一端口启动 API 和静态前端：

```bash
python run_dev.py
```

打开 `http://127.0.0.1:8080`。如只需调试 API：

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

接口文档位于 `http://127.0.0.1:8000/api/docs`。

## 测试

```bash
pip install -r backend/requirements-dev.txt
pytest -q
node --check frontend/app.js
node --check frontend/zip.js
docker compose config --quiet
```

## 当前算法边界

- 每个相互分离的深色小簇先定位为一个候选区域；
- 方向一致且存在灰度峰谷的毛发簇会进一步估算簇内数量；
- 严重交叉、缠绕或没有可见灰度间隙的目标仍可能被合并；
- 一个目标断成相距较远的区域可能被算作多个；
- 深色文字、划痕和污点可能被误计；
- 正式使用应固定背景、拍摄距离和光照，并保留人工修正功能。
