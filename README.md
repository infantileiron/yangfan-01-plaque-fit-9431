# 镭页展陈·双语铭牌溢出预审

按实际排版空间和历史校样筛出需人工重排的双语展品铭牌。

模拟团队为 镭页展陈印前组 · 7 人。译审交稿后，印前员从排版软件逐一打印 1:1 纸样。固定框宽和字高常由不同模板继承，人工按新翻译列表全量复测。

- 同名版式有 13 类，历史溢出率差别大，长尾类别资料稀少。
- 中文字数与参考语字数无法直接比较实际可用行宽。
- 陌生语言组合常被当成普通样跳过，临到输出才补校。

输入双语译审清单的纸样参数、版式编号和历史实际溢出结果。 签发的重排与加校名单交给印前拼版员，最终依实际纸样裁定。

## 业务模块

| 模块 | 工作内容 | 核心实体 |
|---|---|---|
| 校样底稿 | CSV 校验、预览实测字段、以完整数据内容的 SHA-256 去重、追溯版本和下载原始数据 | 数据版本 |
| 预审计算 | 按版本与领域参数创建计算单，由独立进程计算；从排队、成功或失败状态追溯 | 计算单 |
| 溢出清单 | 按业务字段查看度量、比较与排序，下载计算结果 | 计算单及其结果 |
| 拼版交接 | 对结果样本作签发或暂缓，记录理由，导出选中样本并查看审计时间线 | 交接记录 |

## 算法及口径

**留一平滑目标编码 + 逻辑回归**。仅在训练纸样上拟合版式与参考语组合的平滑目标统计。训练用留一编码、验证和待校应用完整训练映射，未知类别回退训练先验。两版模型均按几何占位率解释，在独立留出纸样按 Brier 分数择优。设置重排概率阈值；新组合即使概率低仍须补纸样。双类不足或字段缺失时终止计算。

录入参数 **重排概率阈值**，默认 0.6，允许范围 0.3–0.9。概率达到阈值进入重排队列。首次出现的版式或参考语仍需补纸样。

演示集 `镭页_常设展铭牌_2026-11-25.csv` 由 `backend/src/business/demo.py` 用固定数据或固定随机种子生成。工作台保留每次运行使用的输入版本、参数、结果和签审人。业务判断由服务端执行，前端图表直接读取返回的数值。

## 一天的工作剧本

1. 08:35 译审交出镭页_常设展铭牌_2026-11-25.csv。
2. 08:45 对照样本 192 条训练、48 条独立留出和 8 条待校。
3. 09:05 分析员以 0.60 概率阈值建预审计算单。
4. 09:10 核对几何基准和类别编码模型，查看 Brier 对比。
5. 09:15 铭牌 LY-P01 的高占位版进入重排，LY-P08 的新语言组合必须补纸样。
6. 09:30 复核员签发 LY-P01，导出仅含已签审记录的拼版 CSV。
7. 10:00 拼版员按待校意见做 1:1 样张，保留预测与纸样两套记录。

## 快速启动

```sh
test -f .env || cp .env.example .env
docker compose config --quiet
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```

前端在 `http://127.0.0.1:18320`，API 文档在 `http://127.0.0.1:19320/docs`，数据库绑定 `127.0.0.1:17820`。重复运行种子命令保留已有账号和版本。测试验收后用 `docker compose down` 停机可保留数据卷。重置本地演示数据可用 `docker compose down -v --remove-orphans`。

| 角色 | 登录邮箱 | 权限 |
|---|---|---|
| 分析员 | analyst@demo.local | 导入数据、提交计算、查看及导出 |
| 复核员 | reviewer@demo.local | 查看及导出、选择样本、签发或暂缓 |
| 观察员 | viewer@demo.local | 查看及导出已有内容 |

三个演示角色密码均为 `Yangfan-demo-2026`。Compose 默认绑定回环地址。自备数据可关闭 `SEED_DEMO`，并为需登录的环境另配账号和密钥。

## 技术与配置

| 服务 | 技术与职责 |
|---|---|
| frontend | HTML/CSS/JS ES 模块，Nginx 静态页面与同源 API |
| backend | Python 3.11、FastAPI、SQLAlchemy、JWT |
| worker | Python 后台进程，使用 DB 的原子认领、租约与重试，输出 CSV |
| db | PostgreSQL 16，存账号、版本、计算单和审计 |
| redis | Redis 7，通知 worker 尽快拾取，任务状态由数据库持久化 |

`FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT` 管理回环端口，`JWT_SECRET` 为签名密钥，`DB_PASSWORD` 为应用数据库口令，`COMPOSE_PROJECT_NAME` 控制该栈的命名空间。

本地 Python 开发可用 `docker compose up -d db` 启动关系库，并另起一个仅绑定回环的 Redis。Compose 内的 Redis 通过容器网络连接。用 Python 3.11 安装 `backend/requirements.txt` 后，进入 backend 目录，设置 `DATABASE_URL`、`REDIS_URL`、`JWT_SECRET`、`ARTIFACTS_DIR`、`SEED_DEMO`，分别运行 `uvicorn src.main:app --reload`、`python -m src.worker`。ARTIFACTS_DIR 需已存在且由两进程共享。前端可使用 Compose Nginx 镜像并重建以加载修改，或用已有 Nginx 反向代理本地 Python 端口。

`backend/src/business/` 保存本题字段、生成器和算法，`backend/src/services/` 处理资源生命周期，`backend/migrations/` 是初始关系模式，`frontend/src/` 按页面、API、会话、组件、路由及样式组织。前后端枚举分别在 `backend/src/constants/workflow.py` 和 `frontend/src/constants/enums.js`。角色用于 JWT 路由与表单权限，计算状态用于队列与结果页，签审决定用于交接页、审计和签发导出。

## 许可证

批次交付保留原作者权利。单独使用与再分发条款由项目所有者确定。
