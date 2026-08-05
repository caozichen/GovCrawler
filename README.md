# PA Crawler Console

本机运行的爬虫任务控制台。前端使用 Vue 3 + Vite，本地控制层使用 Node.js，仅监听 `127.0.0.1`。

## 默认登录

- 用户名：`admin`
- 密码：`admin123`

可通过环境变量 `PA_ADMIN_USER` 和 `PA_ADMIN_PASSWORD` 覆盖。

## 开发运行

```powershell
npm.cmd install
npm.cmd run dev
```

前端地址：`http://127.0.0.1:5173`

## 爬虫协议

控制台使用参数数组执行：

```text
python crawler.py --url <url> --output-dir <timestamp-directory> --run-id <run-id>
```

爬虫成功时返回退出码 `0`，推荐将结构化数据写到 `records.jsonl`，过程日志写到 stdout/stderr。

每次执行会直接在配置的输出根目录创建 `YYYY-MM-DD_HH-mm-ss-SSS` 目录。默认输出根目录就是项目目录。
