# EndNote DOI Workflow（中文说明）

把**一串 DOI**——或者 Word 手稿里已有的 DOI 链接——变成 **一个 EndNote 可直接导入的文件**。

```bash
python scripts/workflow_cli.py prepare-doi --from-docx manuscript.docx --scan-bare-dois --outdir out
# 产出 out/manuscript.enw → EndNote: 文件 > 导入 > File
```

## 解决什么问题

上游项目只认 Word 里手写的 `(PMID: 12345678)`。但真实手稿里你手上几乎从来只有 DOI，通常挂在每条文献末尾的 `https://doi.org/10.xxxx/...`。

于是这里补上了完整的 DOI 入口：

| 能力 | 上游 | 本仓库 |
|---|---|---|
| `(PMID: …)` → ENW | 有 | 有（未改动） |
| EndNote 20 临时引文 DOCX | 有 | 有（未改动） |
| **DOI 列表 → ENW** | 无 | **有** |
| **从 Word 扫描 DOI 链接** | 无 | **有** |
| **文献对应校验** | — | **有** |
| Crossref 兜底（非 PubMed 文献） | 无 | **有** |
| 导入前可核对的审计表 | 无 | **有** |

## 三种用法

```bash
# 1. DOI 列表文件（一行一个，# 可注释）
python scripts/workflow_cli.py prepare-doi --doi-file dois.txt --outdir out

# 2. 命令行直接给（可重复、逗号分隔）
python scripts/workflow_cli.py prepare-doi --doi "10.1038/s41586-021-03819-2,10.1002/ccr3.4439" --outdir out

# 3. 从 Word 抓 DOI（只读，不改你的文档）
python scripts/workflow_cli.py prepare-doi --from-docx manuscript.docx --outdir out
```

常用参数：`--scan-bare-dois`（连正文里没标记的 DOI 也抓，默认关闭）、`--no-crossref`（只用 PubMed）、`--email`（NCBI/Crossref 礼节性标识）、`--api-key`（解除 NCBI 3 req/s 限速）。

## 导入 EndNote

1. 上面任一命令 → 得到 `.enw`
2. EndNote：**File → Import → File**
3. Import Option 选 **`EndNote Import`**，Text Translation 选 **`Unicode (UTF-8)`**
4. 一次导入全部

> 脚本从不写你的 EndNote 库，只产出导入文件。读取库取 Record Number（Phase 2）也是只读。

## 不会导错文献

这是设计上的第一优先级。DOI→PMID 万一返回了**另一篇**文章，会静默污染你的库，所以每步都有闸门：

1. **PubMed 校验**：只有当那条记录自身的 DOI == 你请求的 DOI 才采纳，否则丢弃并记为 unresolved
2. **Crossref 校验**：同样规则
3. **输入校验**：既不像 DOI 也不像 PMID 的输入直接标 `invalid`，**不发网络请求**
4. **去重**：DOI 和 PMID 指向同一篇时只写一次

**绝不猜测。** PubMed 和 Crossref 都解析不了的 DOI 会被如实报告，不会编造。

### 导入前可核对

每次运行额外产出 `*.audit.csv`（带 BOM，Excel 直接打开），逐条列出 输入 → 解析标题 → 是否校验通过：

```
input,kind,status,resolved_id,doi_match,title
10.1038/s41586-021-03819-2,doi,pubmed,34265844,yes,Highly accurate protein structure prediction with AlphaFold.
```

扫一眼标题确认对得上，再导入。

## 两级解析

1. **PubMed 优先**：DOI → PMID，元数据最全，保留 `PMID-<id>` 标注（可继续接 Word 临时引文）
2. **Crossref 兜底**：PubMed 没收录的 DOI（书籍章节、会议论文、不少工程与社科期刊），正确映射 EndNote 类型

网络抖动（429/5xx/超时）自动退避重试；404 不重试。无 API key 时自动限速到 3 req/s。

## 原有两段式流程（保留未改）

```bash
# Phase 1：Word 里 (PMID: …) → 可导入 .enw
python scripts/workflow_cli.py prepare manuscript.docx --outdir out

# Phase 2：导入 EndNote 后 → 用真实 Record Number 替换成临时引文
python scripts/workflow_cli.py finalize manuscript.docx --state out/manuscript.state.json
```

Phase 2 需要 **EndNote 20 for Windows**，且需已导入库。

## 环境要求

- Python 3.10+
- `python-docx`（仅 DOCX 入口需要）：`pip install python-docx`

脚本只用标准库，不需要 `requests`。

## 作为技能安装

把整个文件夹放进你用的 agent 的技能目录：

- **Claude Code**：`~/.claude/skills/endnote-doi-workflow/`
- **WorkBuddy**：`~/.workbuddy/skills/endnote-doi-workflow/`

重启会话即可按名调用。也可以完全当普通命令行工具用。

## 上游与致谢

本项目衍生自 **[xfeng3097-beep/codex-endnote-workflow](https://github.com/xfeng3097-beep/codex-endnote-workflow)**（MIT 许可），原有的两段式 PMID 流程（`prepare.py`、`finalize.py`、`common.py`、`endnote_library.py`、`privacy_audit.py`）出自该项目。

本仓库新增：完整 DOI 入口（`prepare_doi.py`）、Crossref 兜底、文献对应校验、审计表与去重。

文献元数据来自 **NCBI E-utilities** 与 **Crossref**，批量使用时请遵守其使用政策，建议传 `--email` 或 NCBI API key。

## 许可

MIT，详见 [LICENSE](LICENSE)。上游的 MIT 版权声明与本仓库并列保留。
