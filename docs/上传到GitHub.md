# 把 litpush 上传到 GitHub（从零开始）

假设你已经在本地把项目整理好了（本目录）。下面从"装 Git"一路走到"仓库公开可见"，
每一步都给出可直接粘贴的命令和预期结果。

> 全程约 10 分钟。命令以 Windows PowerShell 为例，macOS/Linux 只差路径写法。

---

## 0. 先做一次"脱敏检查"

上传前务必确认这三类东西没有被带进仓库：

| 类型 | 例子 | 处理 |
| --- | --- | --- |
| 个人路径 | `D:\Users\你的名字\...`、`C:\Users\xxx\` | 配置里改占位路径 |
| 密钥 | OpenAlex key、LLM key | 只放环境变量，或 `.gitignore` 掉 `config/config.toml` |
| 运行期数据 | 去重库、缓存、日志、真实摘要 | 已在 `.gitignore` 中排除 |

在项目根目录跑一遍（有输出就说明还有残留）：

```powershell
# 找绝对路径（Windows 盘符）
Select-String -Path *.md,config\*,litpush\*,scripts\* -Pattern '[A-Z]:\\\\' -Encoding UTF8

# 找可能泄露的 key（常见前缀）
Select-String -Path *.md,config\*,litpush\*,scripts\* -Pattern 'sk-[A-Za-z0-9]{10,}|api[_-]?key\s*=\s*"[^"]{10,}"' -Encoding UTF8

# 确认运行期数据不会被提交
git status --short   # 追加提交后执行，data/ 与 logs/ 不应出现
```

预期：前两条只应命中文档里的示例（例如 `C:/Users/YourName/...`），没有真实 key。

---

## 1. 安装并配置 Git

```powershell
git --version          # 没装就去 https://git-scm.com/download/win
git config --global user.name  "你的名字"
git config --global user.email "你的邮箱@example.com"
git config --global core.autocrlf true      # Windows 建议 true，避免换行符刷屏
```

> `user.email` 最好和 GitHub 账号邮箱一致，否则提交记录不会算到你头上。

---

## 2. 初始化本地仓库

在项目根目录（含 `README.md` 那一层）执行：

```powershell
git init
git add .
git status              # 先看清单，确认没有 data/、logs/、config/config.toml
git commit -m "feat: 每日文献推送工具（OpenAlex + Obsidian）"
```

预期 `git status` 里**不应该**出现：

```
data/litpush.sqlite3
logs/daily.log
config/config.toml          ← 本地配置，含你的路径和 key
```

如果出现了，检查 `.gitignore`：

```gitignore
data/*
!data/.gitkeep
logs/
__pycache__/
*.pyc
config/config.toml
.DS_Store
Thumbs.db
```

---

## 3. 在 GitHub 上建一个空仓库

1. 打开 <https://github.com/new>
2. **Repository name**：`litpush`
3. **Description**：`每日学术文献推送：OpenAlex + Obsidian，自动打分与中文卡片`
4. 选 **Public**（想先私有就选 Private，之后能改）
5. **不要**勾选 "Add a README" / "Add .gitignore" / "Choose a license"——本地已经有了，
   勾了会产生冲突
6. 点 **Create repository**

创建完成后页面会给出仓库地址，形如：

```
https://github.com/<你的用户名>/litpush.git
```

---

## 4. 配置认证（二选一）

### 方案 A：HTTPS + 个人访问令牌（PAT）——最简单

GitHub 从 2021 年起不接受账号密码，HTTPS 推送要用 PAT。

1. <https://github.com/settings/tokens> → **Generate new token**
   - 选 **Fine-grained token**（更安全）或 **Classic**
   - 权限：Classic 勾 `repo`；Fine-grained 选目标仓库 + `Contents: Read and write`
   - 有效期按需，比如 90 天
2. 复制生成的 token（只显示一次）
3. 推送时：
   - Username：你的 GitHub 用户名
   - Password：**粘贴 token**（不是登录密码）

Windows 上 Git 会把凭据存进"凭据管理器"，之后不用再输。

### 方案 B：SSH 密钥——一次配置长期免密

```powershell
ssh-keygen -t ed25519 -C "你的邮箱@example.com"
# 一路回车（默认路径 ~/.ssh/id_ed25519，可设 passphrase）
Get-Content $HOME\.ssh\id_ed25519.pub | Set-Clipboard
```

然后：GitHub → 右上角头像 → **Settings** → **SSH and GPG keys** → **New SSH key**
→ 粘贴 → 保存。

验证：

```powershell
ssh -T git@github.com
# 预期：Hi <用户名>! You've successfully authenticated...
```

用 SSH 的话，第 5 步的地址换成 `git@github.com:<用户名>/litpush.git`。

---

## 5. 关联远程仓库并推送

```powershell
git remote add origin https://github.com/<你的用户名>/litpush.git
git branch -M main
git push -u origin main
```

预期输出：

```
Enumerating objects: 42, done.
...
To https://github.com/<你的用户名>/litpush.git
 * [new branch]      main -> main
branch 'main' set up to track 'origin/main'.
```

刷新仓库页面，应该能看到 README、LICENSE、源码目录。

---

## 6. 上传后的验收清单

```powershell
git ls-files | Select-String 'data/|logs/|config/config\.toml'    # 应无输出
git log --oneline -1
```

网页上再确认：

- [ ] README 能正常显示（中文没乱码）
- [ ] `config/config.example.toml` 在，`config/config.toml` 不在
- [ ] 没有 `data/`、`logs/`、`__pycache__/`
- [ ] LICENSE 显示在仓库右侧
- [ ] About 里填了一句话简介 + topics（`obsidian`、`literature-review`、`openalex`、`python`）

---

## 7. 之后的日常更新

```powershell
git add .
git commit -m "fix: 修正期刊回溯窗口"
git push
```

发版本（可选）：

```powershell
git tag -a v1.0.0 -m "第一个可用版本"
git push origin v1.0.0
```

然后在 GitHub 的 **Releases** 里基于这个 tag 写更新说明。

---

## 8. 常见问题

**Q1：`git push` 报 `Support for password authentication was removed`**
A：用的是账号密码。改用 PAT（第 4 步方案 A），密码栏填 token。

**Q2：报 `remote origin already exists`**
A：`git remote set-url origin <新地址>`，或 `git remote remove origin` 后重新添加。

**Q3：中文文件名/内容在网页上乱码**
A：确认文件是 UTF-8（本项目源码均为 UTF-8 无 BOM）。`.gitattributes` 可加：

```
* text=auto eol=lf
*.cmd text eol=crlf
*.ps1 text eol=crlf
```

**Q4：提示文件超过 100 MB**
A：GitHub 单文件上限 100 MB。检查是不是把 `data/` 或 PDF 提交了：
`git rm -r --cached data` 后再提交。

**Q5：不小心把 key 提交上去了**
A：**立刻去服务商后台吊销该 key**（Git 历史里的东西删不干净），然后：

```powershell
git rm --cached config/config.toml
git commit -m "chore: stop tracking local config"
git push
```

如果已经推送很久，考虑用 `git filter-repo` 重写历史，或直接删库重建。

**Q6：想把仓库改成私有**
A：Settings → General → 最下方 **Danger Zone** → Change visibility。

**Q7：别人 clone 后跑不起来**
A：大概率是没生成配置。README 里应写清：

```bash
litpush.cmd init     # 生成 config/config.toml
litpush.cmd doctor   # 自检
```

---

## 9. 让仓库更容易被看懂（可选）

- 在 `README.md` 顶部放一张卡片截图（`docs/screenshot.png`），比文字有力；
- 仓库 **About** 里加 topics：`obsidian` `literature` `openalex` `arxiv` `python`；
- 加一个极简的 `.github/workflows/ci.yml` 跑 `python -m compileall litpush`
  和 `ruff check`，让"绿勾"替你证明代码能跑；
- 在 README 末尾加"Known limitations"（本项目已有），诚实比吹嘘更能获得 star。
