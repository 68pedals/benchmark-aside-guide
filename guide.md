# Browser-Use Benchmark 环境搭建与运行指南

本文档介绍如何在 Windows 环境下配置 `uv`、部署 `browser-use/benchmark` 项目，并结合 **Aside Browser** 与 **DeepSeek** 模型运行评测任务。

---

## 一、环境准备与项目安装

### 1. 安装 `uv` 工具
在 PowerShell 中执行以下命令安装 `uv`：

```powershell
powershell -ExecutionPolicy ByPass -c "irm [https://astral.sh/uv/install.ps1](https://astral.sh/uv/install.ps1) | iex"
```

> **注意**：安装完成后，请**关闭当前 PowerShell 窗口并重新打开**，以确保环境变量生效。

### 2. 克隆仓库并安装依赖
打开新的 PowerShell 窗口，依次执行以下命令拉取代码并同步虚拟环境：

```powershell
# 1. 克隆仓库并进入目录
git clone [https://github.com/browser-use/benchmark.git](https://github.com/browser-use/benchmark.git)
cd benchmark

# 2. 使用 uv 创建虚拟环境并安装所有依赖
uv sync
```

### 3. 配置环境变量（API Keys）
复制环境变量模板文件：

```powershell
cp .env.example .env
```

编辑生成的 `.env` 文件，填入 DeepSeek 的 API 配置（请将下方的占位符替换为你的实际 API Key）：

```env
OPENAI_API_KEY=你的_DeepSeek_API_Key
OPENAI_BASE_URL=[https://api.deepseek.com/v1](https://api.deepseek.com/v1)
```

---

## 二、Aside Browser 与 CLI 配置

### 4. 安装 Aside CLI
1. 下载并登录 **Aside Browser**。
2. 进入 `Settings` -> `Developer`，点击安装 **Aside CLI**。
3. 安装完成后，**重启 PowerShell** 以刷新环境变量。

<img width="1435" height="695" alt="安装 Aside CLI" src="https://github.com/user-attachments/assets/48ea81e2-0319-4b28-a50c-2bfe51e8581b" />

### 5. 终端登录 Aside
在新的 PowerShell 窗口中运行登录命令，并按提示输入你的账号邮箱，终端会生成对应的验证 Key：

```powershell
aside login
```

<img width="886" height="132" alt="终端登录获取 Key" src="https://github.com/user-attachments/assets/2117f486-904f-431c-a858-a32963c0e5e0" />

### 6. 浏览器端确认设备
回到 Aside Browser，进入 `Settings` -> `Account` -> `Devices`，找到对应设备点击 **Confirm**，输入第 5 步终端中显示的 Key 完成绑定：

<img width="1599" height="806" alt="确认设备绑定" src="https://github.com/user-attachments/assets/82cd942d-c608-47c7-b79e-57078f10878a" />

### 7. 配置默认模型
在 Aside Browser 中进入 `Settings` -> `Models` -> `Connect`：
1. 添加并连接 **DeepSeek** 模型。
2. 在 **Default Model** 下拉菜单中选择 `deepseek`。

<img width="1448" height="483" alt="连接 DeepSeek 模型" src="https://github.com/user-attachments/assets/2894bb85-741f-416c-bf10-273ea6f31e76" />
<img width="1335" height="514" alt="设置默认模型" src="https://github.com/user-attachments/assets/d59ecb5c-7d17-4148-96f2-f94f9c9e30f5" />

---

## 三、替换脚本与运行评测

### 8. 替换评测脚本
将自定义修改后的 `run_eval.py` 文件覆盖替换到 `benchmark` 根目录下。

### 9. 执行评测命令
确保当前终端位于 `benchmark` 目录下，执行以下命令启动测试：(--tasks 2 测试用例个数)

```powershell
uv run python .\run_eval.py --tasks 2 --judge-model deepseek-chat
```
