# 已发布的校准结果

用例本身**不入库**（含生产代码派生内容，见 `docs/REFERENCE.md` §8.8.1 `export-bundle`）。
但校准结果是**纯数字** —— case_id、p_hat、by_anchor、spread、r_pb、keep/reasons，无任何代码。
把它们放在这里，是为了让别人能**自己核对我们报的每一个比例**，而不必选择相信我们。

已核查：这四个文件不含 `def ` / `import ` / `assert ` / `Traceback` 等代码文本。
原始 `results.jsonl` **未发布** —— 它的 `grade.detail` 带 pytest 回溯，
实测 59 条失败行里有 27 条含测试源码。

## 文件

| 文件 | 集合 | 面板 | n |
|---|---|---|---:|
| `auto-v0_3anchor_20260805.json` | auto-v0 | 3 锚点单轮面板 | 126 |
| `disc-v0_2anchor_partial_20260805.json` | disc-v0 | **2 锚点**（见下） | 28 |
| `retrieval-v0_3anchor_20260805.json` | retrieval-v0 | 3 锚点检索面板 | 27 |
| `scaleprobe60_3anchor_20260807.json` | 新集合抽样 | 3 锚点单轮面板 | 60 |

`disc-v0` 那份是**进程被杀后重建的**：6 轮只完成 4 轮，`strong-full-loop` 从未运行。
文件里的 `anchors_missing` 如实记录了这一点。它描述的是一个**两锚点面板**，
不能与三锚点的结果直接比较。

## 如何复算我们报过的数字

```python
import json, collections, statistics
def band(p): return 'hard' if p < 0.2 else ('mid' if p <= 0.8 else 'easy')
j = json.load(open('auto-v0_3anchor_20260805.json'))
v = [c['p_hat'] for c in j['cases'] if c['p_hat'] is not None]
print(collections.Counter(band(x) for x in v), statistics.mean(v))
```

| 我们报过的 | 怎么复算 |
|---|---|
| `auto-v0` 易/中/难 = **75.2 / 16.2 / 8.6**，mean **0.811** | 用 `auto-v0_3anchor`，**只取有参考解的 105 条**（无参考解的 21 条 mean p_hat 仅 0.008，混进来会把分母搞错，见 HANDOFF §6.1） |
| 新集合 = **80.0 / 18.3 / 1.7**，mean **0.875** | 用 `scaleprobe60_3anchor` 全部 60 条 |
| `disc-v0` = **39.3 / 50.0 / 10.7** | 用 `disc-v0_2anchor_partial` 全部 28 条 |

## ⚠️ 「合成是否让用例更难」的 Δ：**−0.009 到 −0.028，取决于加权方式**

我们此前只报了 **−0.009**。发布数据后复算发现，这是两个都站得住的算法之一：

| 算法 | Δ |
|---|---:|
| 池化原始行（按尝试次数加权） | **−0.009** |
| 按锚点等权（`by_anchor` 取均值） | **−0.028** |

**差异来源是数据本身有缺损**：429 限流丢掉了部分行，27 条宿主用例里
**6 条只剩 1 次有效尝试**，合成侧也有 6 条只剩 2 次。尝试次数不齐时，
「按行池化」与「按锚点等权」必然给出不同的数。

**结论不变**（合成不使用例变难，两个算法都是小幅负值），
**但我们此前把两个可辩护数值中的一个当成了「那个数值」来报。**
用这份数据可以自己复算两种口径。n=27 远低于 §6.5 所需的 153，属欠功效。

复算方法：从 `by_anchor` 里只取 `weak-frugal-loop` 与 `mid-frugal-loop`
（`disc-v0` 那次唯一完成的两个锚点），按 `<host_id>` ↔ `<host_id>-retrieval` 配对。

## 面板身份

`anchor_fingerprint` 哈希的是**锚点配置文件的内容**，不只是路径。
两份指纹不同不一定意味着行为不同 —— 例如 `5961d65` 只给 agent 配置加了
`capability_axes` 声明（供 `unfit_anchors` 做适配性检查），不改 agent 行为。
比较前请先确认差异是什么，不要因为指纹相同就假定可比，也不要因为不同就放弃比较。
