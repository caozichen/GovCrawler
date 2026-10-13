# 爬虫管理平台（Crawler Management Platform）

本机运行的爬虫管理平台。前端使用 Vue 3 + Vite，本地控制层使用 Node.js，仅监听 `127.0.0.1`。

## 功能介绍

- **任务管理**：新增、编辑、删除和校验爬虫任务，支持单任务运行与批量运行。
- **分类管理**：使用带颜色的标签区分任务，支持分类增删改，并可按分类筛选任务和运行记录。
- **运行控制**：任务统一进入队列，支持最大并发数、超时限制、停止运行和防止同一任务重复排队。
- **结果追踪**：展示运行状态、采集数据量和输出目录，可直接查看 stdout、stderr 日志。
- **环境设置**：配置 Python 解释器、输出根目录、默认超时和最大并发数，并提供环境检查与路径选择。
- **本地使用**：提供本地登录、明暗主题和响应式界面；API 仅监听本机地址。
- **界面品牌**：统一显示“爬虫管理平台 / Crawler Management Platform”，使用科技风数据核心标志，导航入口为“爬虫管理”。
- **登录背景**：使用 `public/login-background.jpeg` 中的 2560×1440 原始 JPEG，构建时原样复制，按比例铺满屏幕；图片未经压缩或重新编码，登录页支持日夜主题切换。

> 定时调度目前未开放，任务仅支持手动触发或批量运行。

## 支持的脚本

当前内置 24 个 Python 脚本。社区工作者专用脚本首次回溯730天，随后按历史水位增量采集；两个“最近 10 条”任务按条数采集，其余招聘公告脚本默认采集近一个自然月内符合标题规则的内容。

### 市级单位

| 采集目标 | 脚本 |
| --- | --- |
| 北京市事业单位公开招聘 | [`beijing_hr_recruitment.py`](crawlers/beijing_hr_recruitment.py) |
| 北京市经济和信息化局 | [`beijing_economy_information_recruitment_announcements.py`](crawlers/beijing_economy_information_recruitment_announcements.py) |
| 北京市文物局 | [`beijing_culture_tourism_recruitment_announcements.py`](crawlers/beijing_culture_tourism_recruitment_announcements.py) |
| 北京市知识产权局 | [`beijing_intellectual_property_recruitment_announcements.py`](crawlers/beijing_intellectual_property_recruitment_announcements.py) |
| 北京市规划和自然资源委员会 | [`beijing_planning_natural_resources_recruitment_announcements.py`](crawlers/beijing_planning_natural_resources_recruitment_announcements.py) |
| 北京市统计局 | [`beijing_statistics_recruitment_announcements.py`](crawlers/beijing_statistics_recruitment_announcements.py) |
| 北京市市场监督管理局 | [`beijing_market_regulation_recruitment_announcements.py`](crawlers/beijing_market_regulation_recruitment_announcements.py) |
| 北京市园林绿化局 | [`beijing_landscaping_recruitment_announcements.py`](crawlers/beijing_landscaping_recruitment_announcements.py) |
| 北京市生态环境局 | [`beijing_ecology_environment_recruitment_announcements.py`](crawlers/beijing_ecology_environment_recruitment_announcements.py) |
| 北京市体育局 | [`beijing_sports_recruitment_announcements.py`](crawlers/beijing_sports_recruitment_announcements.py) |
| 北京市民政局 | [`beijing_civil_affairs_recruitment_announcements.py`](crawlers/beijing_civil_affairs_recruitment_announcements.py) |
| 北京市交通委员会 | [`beijing_transport_recruitment_announcements.py`](crawlers/beijing_transport_recruitment_announcements.py) |

### 区级单位

| 采集目标 | 脚本 |
| --- | --- |
| 朝阳区公开招聘（最近 10 条） | [`beijing_chaoyang_recruitment.py`](crawlers/beijing_chaoyang_recruitment.py) |
| 东城区事业单位招聘（最近 10 条） | [`dongcheng_public_institutions.py`](crawlers/dongcheng_public_institutions.py) |
| 东城区社区工作者招聘（跨年增量） | [`dongcheng_community_recruitment.py`](crawlers/dongcheng_community_recruitment.py) |
| 朝阳区社区工作者招聘（跨年增量） | [`chaoyang_community_recruitment.py`](crawlers/chaoyang_community_recruitment.py) |
| 西城区招聘公告 | [`xicheng_recruitment_announcements.py`](crawlers/xicheng_recruitment_announcements.py) |
| 通州区招聘公告 | [`tongzhou_recruitment_announcements.py`](crawlers/tongzhou_recruitment_announcements.py) |
| 大兴区招聘公告 | [`daxing_recruitment_announcements.py`](crawlers/daxing_recruitment_announcements.py) |
| 海淀区招聘公告 | [`haidian_recruitment_announcements.py`](crawlers/haidian_recruitment_announcements.py) |
| 北京经济技术开发区招聘公告 | [`beijing_etda_recruitment_announcements.py`](crawlers/beijing_etda_recruitment_announcements.py) |

### 机关单位与示例

| 采集目标 | 脚本 |
| --- | --- |
| 北京市人民政府门户网站招聘公告 | [`beijing_portal_recruitment_announcements.py`](crawlers/beijing_portal_recruitment_announcements.py) |
| 首都之窗事业单位招聘公告 | [`beijing_government_public_institution_announcements.py`](crawlers/beijing_government_public_institution_announcements.py) |
| 标准调用协议示例 | [`example.py`](crawlers/example.py) |

### 社区工作者专用任务

东城和朝阳两个社区任务会自动加入“区级单位”分类，旧版控制台重启后会补齐任务，保留已有任务配置。两个任务支持手动和批量运行：

- 东城扫描“通知公告”和事业单位招录栏目，通过官网搜索补充发现归档公告；朝阳扫描“其他招考”栏目，不限定最新 10 条。
- 首次默认回溯 730 天，后续从各来源上次完整扫描日期前 7 天开始增量补采；正文和附件变化记为更新，失败的详情和附件在下次运行时重试。
- 区分招聘、报名变更、考试、成绩及录用信息，保存报名时间证据；考试和录用通知不算新报名机会。时间或关联关系不明确时标记待确认。
- `summary.json` 保存新增/更新公告数、采集覆盖情况和报名状态分布，`catalog.json` 保存已登记招聘计划的状态快照。支持岗位附件下载，岗位数量和个人资格筛选尚未实现。

在任务的“额外参数（每行一个）”中填写以下内容，可补采指定历史范围：

```text
--mode=backfill
--from-date=2025-01-01
--until-date=2025-12-31
```

完整参数见[爬虫使用说明](crawlers/README.md)，实现范围见[社区招聘采集设计](docs/community-recruitment-crawler-design.md)。

## 重点功能逻辑

### 分类

- 默认提供“市级单位”“区级单位”“机关单位”三个分类，默认颜色分别为 `#3B82F6`、`#22A06B`、`#D98620`。
- 分类、任务和运行记录持久化在 `.crawler-console/state.json`；旧数据启动时会自动补充分类。
- 内置任务清单随 Git 分发；首次启动会注册全部 24 个脚本，旧版状态会自动补齐缺少的内置任务。
- 内置脚本使用 `crawlers/文件名.py` 相对路径，换到 Windows 或 macOS 后会按当前项目目录解析。
- 修改任务分类时会同步该任务的历史运行记录；删除分类时会将关联数据迁移到其他分类，系统始终至少保留一个分类。

### 任务执行

- Node.js 使用参数数组启动 Python 进程，不拼接 Shell 命令；脚本路径、网址、输出目录和运行编号均作为独立参数传入。
- 每次运行创建独立的时间戳目录。同一任务不能重复排队，全局并发数由环境设置控制。
- 支持主动停止与超时终止；控制程序重启后，未完成的任务统一恢复为“已中断”。

### 采集与结果

- 招聘公告脚本通常按发布日期和标题过滤，再抓取正文及附件；链接会校验目标域名、栏目路径和文件类型。
- 附件下载支持去重和大小限制；单条文章或附件失败会写入 `errors.jsonl`，不会中断其余内容。
- 结构化结果写入 `records.jsonl`，运行日志写入 `stdout.log` 和 `stderr.log`，其他原始页面及附件由脚本按需保存。

## 默认登录

- 用户名：`admin`
- 密码：`admin123`

可通过环境变量 `PA_ADMIN_USER` 和 `PA_ADMIN_PASSWORD` 覆盖。

## 使用方法

### 1. 准备环境

- Node.js 与 npm（建议使用当前 LTS 版本）
- Python 3；当前爬虫仅使用 Python 标准库，无需安装额外依赖

克隆项目并进入目录：

```powershell
git clone https://github.com/caozichen/GovCrawler.git
cd GovCrawler
```

### 2. 启动并登录

按下方“开发运行”安装依赖并启动服务，然后访问 `http://127.0.0.1:10086`，使用默认账号或环境变量配置的账号登录。

Windows 本地运行可先执行 `npm.cmd run build`，再运行 `start-local.cmd`；服务地址为 `http://127.0.0.1:4318`。

### 3. 完成基础配置

进入“系统设置”，确认 Python 解释器和输出根目录，并按需要调整默认超时与最大并发数。首次使用建议先执行环境检查。

### 4. 配置并运行任务

在“爬虫管理”中新增或编辑任务，选择分类，填写目标网址与 `.py` 脚本路径。保存后可运行单个任务，也可运行全部任务；定时调度暂不可用。

分类可通过搜索栏右侧的分类配置入口维护名称和颜色，任务列表与运行记录均支持分类筛选。

### 5. 查看结果

在“运行历史”中查看状态、数据量、分类和日志。每次执行的结构化结果、原始内容及附件均保存在对应的输出目录中。

## 开发运行

```sh
npm install
npm run dev
```

Windows 下可将上述命令的 `npm` 替换为 `npm.cmd`。

开发前端和构建预览的端口固定为 `10086`，端口占用时会报错。开发 API 使用 `4318`，前端将 `/api` 请求代理到该服务。

| 运行方式 | 访问地址 |
| --- | --- |
| `npm run dev` | `http://127.0.0.1:10086` |
| `npm run build` 后执行 `npx vite preview`（API 服务需运行） | `http://127.0.0.1:10086` |
| `npm run build` 后执行 `npm start`；Windows 也可用 `start-local.cmd` | `http://127.0.0.1:4318` |

## 爬虫协议

控制台使用参数数组执行：

```text
python crawler.py --url <url> --output-dir <timestamp-directory> --run-id <run-id>
```

爬虫成功时返回退出码 `0`，推荐将结构化数据写到 `records.jsonl`，过程日志写到 stdout/stderr。

每次执行会直接在配置的输出根目录创建 `YYYY-MM-DD_HH-mm-ss-SSS` 目录。默认输出根目录就是项目目录。

## 测试

```powershell
npm.cmd test
npm.cmd run build
python -m unittest discover -s crawlers/tests -p "test_*.py"
```
