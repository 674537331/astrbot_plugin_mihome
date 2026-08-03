# 米家插件维护工具

这个目录保存发布前可重复执行的检查流程，用于后续升级 AstrBot、`mijiaAPI`
或插件版本时快速复核项目状态。所有脚本只使用 Python 标准库，默认只读取仓库
内容；不读取米家账号、二维码、Cookie、Token 或 AstrBot 数据目录。

## 环境要求

- Python 3.10 或更高版本；
- 在仓库根目录或任意目录执行均可；
- `Node.js` 和 `Ruff` 是可选工具，未安装时对应检查会明确显示为跳过；
- 上游检查需要访问 PyPI 和 GitHub，其他检查默认离线运行。

## 一键项目检查

```bash
python tools/maintenance/check_project.py
```

默认检查：

- 必需文件是否齐全；
- Python 和 JSON 语法；
- `metadata.yaml`、`requirements.txt` 与 `CHANGELOG.md` 的版本关系；
- 依赖是否使用明确版本；
- 仓库中是否混入常见认证状态文件；
- 前端 JavaScript 语法（系统可找到 `node` 时）。

发布前建议执行完整检查：

```bash
python -m pip install -r requirements.txt
python tools/maintenance/check_project.py --all
```

Ruff 检查显式固定为核心错误规则 `E4,E7,E9,F`，避免 Ruff 版本升级改变
默认规则后把历史风格差异误判为发布阻断。

`--all` 会额外运行单元测试与 Ruff。也可以分别使用 `--tests` 或 `--ruff`。
单元测试会确认当前环境安装的 `mijiaAPI` 与固定版本一致，因此应先在隔离的测试
环境中安装项目依赖；旧依赖导致检查失败是预期的保护行为。
如果自动化环境要求缺少 Node.js 或 Ruff 时直接失败，可增加
`--require-optional-tools`。

若工具已安装但不在子进程的 `PATH` 中，可通过 `MIHOME_NODE` 与
`MIHOME_RUFF` 指定可执行文件路径。例如 PowerShell：

```powershell
$env:MIHOME_NODE = "C:\path\to\node.exe"
$env:MIHOME_RUFF = "C:\path\to\ruff.exe"
python tools/maintenance/check_project.py --all --require-optional-tools
```

## 核对上游 mijiaAPI

```bash
python tools/maintenance/check_upstream.py
```

脚本会读取 `requirements.txt` 中固定的 `mijiaAPI` 版本，并核对：

- PyPI 当前正式版本；
- GitHub 默认分支最新提交；
- GitHub 最新标签；
- 项目固定版本是否落后于 PyPI。

它只访问固定的公开接口，不读取 GitHub Token。网络不可用或请求被限流时会返回
非零状态，便于 CI 和人工检查发现未完成的上游核对。机器可读输出：

```bash
python tools/maintenance/check_upstream.py --json
```

## 性能与占用基线

```bash
python tools/maintenance/performance_baseline.py
```

默认输出源码文件数、体积、有效行数、最大文件、Python 静态解析耗时与峰值内存，
并检查异步函数中直接调用常见阻塞 API 的位置。这是一份稳定、离线、无账号依赖
的维护基线，适合比较版本之间的代码体积和静态分析开销；它不等同于真实设备网络
延迟或 AstrBot 整体内存占用。

需要分析测试代码时：

```bash
python tools/maintenance/performance_baseline.py --include-tests
```

在依赖完整的隔离环境中，还可以显式测量模块冷启动导入：

```bash
python tools/maintenance/performance_baseline.py --import-module device_profiles
```

模块导入可能执行该模块自身的顶层代码，因此这个选项不默认启用，也不应对来源
不明的模块使用。

## 推荐发布流程

```bash
python tools/maintenance/check_project.py --all
python tools/maintenance/check_upstream.py
python tools/maintenance/performance_baseline.py --include-tests
```

然后人工检查：

1. GitHub 未关闭 Issue、Pull Request 和 Actions；
2. AstrBot 最新插件开发与发布规范；
3. WebUI 桌面端、窄屏端和插件设置同步；
4. `CHANGELOG.md`、`README.md`、`metadata.yaml`、PR 与 Release 的版本和描述；
5. 登录、退出登录、设备读取、场景执行和控制白名单的真实环境回归。

## 维护约定

- 脚本中不加入私人绝对路径，使用脚本位置自动寻找仓库根目录；
- 不提交运行生成的认证文件、状态文件、日志或性能快照；
- 需要保存一次审计结论时，新增带日期的 Markdown 记录，并注明提交号与执行命令；
- 新检查优先保持只读；确需写文件时必须增加显式参数并在说明中标明输出位置；
- 上游 API 或项目目录结构变化后，同步更新脚本和本说明。
