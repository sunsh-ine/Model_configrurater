# 前端 V1.1 视觉验收截图

基线：`ef270d1`。截图由本机 Chrome headless 对离线 Demo 实拍，
数据来自仓库内的公开示例与归档模拟，不含真实定价。

| 文件 | 场景 |
| --- | --- |
| `desktop_home.png` | 1440px，生成前 |
| `desktop_result.png` | 1440px，生成后首页 |
| `laptop_1280.png` | 1280px，普通笔记本宽度 |
| `narrow_1024.png` | 1024px，上下布局 |
| `details_expanded.png` | 1440px，明细展开（性能/参考） |

## 代码结构（V1.1）

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `app/streamlit_app.py` | 505 | 控件、布局、生成与状态 |
| `app/ui_presenters.py` | 862 | 状态映射、格式化、纯 HTML 片段 |
| `app/ui_style.css` | 276 | 样式体系 |

注：`ef270d1` 提交信息中的“约 330 行”为笔误，实际 505 行。
