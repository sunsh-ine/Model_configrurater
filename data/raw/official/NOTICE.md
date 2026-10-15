# 关于本目录下的存档页面（NOTICE）

本目录中的 `*.html` 是**公开网页的存档副本**，来源方为各自的发布者
（SGLang / LMSYS、vLLM 等）。它们被保存下来只有一个目的：

> 为 `data/sources/official_manifest.yaml` 中登记的每个官方来源提供**可离线复核的原始证据**：
> 记录抓取日期与 `sha256`，使"这份数据从哪来、有没有被改过"可以被自动校验
> （见 `tests/test_official_manifest.py`）。

使用约定：

- **版权归原发布者所有。** 这些文件不是本项目的产出，本项目不对其主张任何权利；
  引用其中的指标口径时，请以原始 URL 为准（每条 manifest 记录都带有 `source_url`）。
- **不作为内容再分发。** 仓库不提供对这些页面内容的额外整理、翻译或再发布，
  只保留抓取当时的原始字节以便哈希校验。
- **可被删除，但需同步调整清单。** 若要移除某个页面：删文件，并把 manifest 中对应条目的
  `evidence_file` / `evidence_sha256` 置空；若该条目是 `partial_evidence`（入库为候选记录），
  还需把 `status` 降为 `pending_verification`，因为候选记录要求有可校验的证据文件。
  注意不要清空**全部**存档文件：`tests/test_official_manifest.py` 中的两条证据完整性测试
  需要有校验对象（一条要求至少校验 4 个文件，一条要求目录非空），清空会让它们失去意义。

对应的来源清单与逐条判定见：

- `data/sources/official_manifest.yaml`
- `docs/official_coverage_gaps.md`
