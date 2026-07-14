# 第四阶段：Validity Critic

## 1. 为什么需要 Validity Critic

前三阶段完成了"剖析 → 规划 → 执行"，产出了 `prepared_panel.csv`。
但"生成了宽表"不等于"宽表可以安全建模"。

金融建模最致命的风险是**未来函数（look-ahead bias）**和**标签泄漏（label leakage）**：
- 用了未来才知道的财务数据 → 模型在回测里表现极好，实盘崩盘；
- 把未来收益标签当特征 → 训练集准确率近 100%，完全无效。

Validity Critic 的作用就是：**在交付建模前，对 panel 做最后一道有效性审查**，
专门盯住这些"普通质量检查发现不了"的泄漏与时间有效性问题。

---

## 2. 它和普通数据检查有什么区别

| 维度 | 普通数据检查 | Validity Critic |
|---|---|---|
| 关注点 | 缺失率、重复、dtype、异常值 | 未来函数、label leakage、时间有效性 |
| 失败后果 | 表不干净，但可清洗 | 模型看似有效实则无效，实盘灾难 |
| 检查对象 | 表本身 | 表 + 数据字典 + 执行日志 + plan + 源码 |
| 判定依据 | 统计阈值 | 时间因果性、role 标注、源码静态分析 |

普通检查问"数据对不对"，Critic 问"**这份数据能不能安全地喂给一个时间序列模型而不泄漏**"。

---

## 3. 它如何检查未来函数

Critic 从三个角度查未来函数：

1. **财务对齐**（`fundamentals_aligned_by_announce_date`）：
   对所有 `source_fundamental_available=True` 的行，验证 `announce_date <= date`。
   任意行 `announce_date > date` 即判 failed——这意味着用了未公告的财务数据。
2. **report_date 未被用于对齐**（`report_date_not_used_for_alignment`）：
   静态检查 `executor.py`：应出现 `announce_date` + `merge_asof`，且 `report_date`
   不出现在 merge/on 上下文，panel 不含 `report_date` 列。无法完全证明时给 warning。
3. **rolling 只用历史窗口**（`rolling_features_past_only_static_check`）：
   静态检查源码：rolling 特征应 `groupby("ticker")`，且不存在"非 label 上下文"的
   `shift(-k)`（k>0）。仅凭 panel 无法完全证明，故以源码静态检查为主，存疑给 warning。

---

## 4. 它如何检查 label leakage

1. **label role 正确**（`label_role_is_correct`）：data_dictionary 中 `label_next_5d` 的 role 必须是 `label`。
2. **label 不进特征**（`label_not_in_approved_features`）：approved_feature_columns 不得包含 `label_next_5d`。
3. **无未来命名列**（`no_future_named_columns_in_features`）：approved features 不得含 `future/next/label/target` 命名的字段。
4. **approved features role 合法**（`approved_features_have_valid_roles`）：approved features 只能来自 data_dictionary 中 role=feature 的列；primary_key/raw_input/label/source_flag 不得进入。
5. **label 由未来 shift 生成**（`label_created_with_future_shift`）：静态检查 `label_next_5d` 由 `shift(-5)` 或等价逻辑生成，且只作标签。

---

## 5. 它如何检查 announce_date 对齐

核心检查 `fundamentals_aligned_by_announce_date`：

- 取 `source_fundamental_available=True` 的行；
- 若 `announce_date` 非空，必须满足 `announce_date <= date`（否则 failed）；
- 若 `announce_date` 缺失但 pe/pb/roe 存在（无法证明无 look-ahead），给 warning。

辅以 `report_date_not_used_for_alignment` 静态检查，确保 executor 用的是 `announce_date + merge_asof`，
而非把 `report_date` 直接 merge 到 `date`。

---

## 6. 它如何生成 approved feature columns

`approved_feature_columns.json` 的生成逻辑：

1. 从 data_dictionary 取所有 `role=feature` 的列；
2. 与白名单（return_1d/return_5d/volatility_20d/turnover_20d/pe/pb/roe/industry_name）取交集，保持白名单顺序；
3. 其余所有列进入 `excluded_columns`（含主键、raw_input、label、source_flag、auxiliary）；
4. `label_column = label_next_5d`，并附注"标签不得作特征、必须用 time-based split"。

这样下游建模只需读 `approved_feature_columns.json` 作为 X，读 `label_column` 作为 y，
从结构上杜绝 label 进入特征矩阵。

---

## 7. 当前确定性 baseline 的限制

- Critic 为确定性规则，不调用 LLM。
- 对 rolling 是否完全无未来函数的判断**部分依赖源码静态检查**，无法 100% 证明（动态执行追踪未实现）。
- 未训练模型，未做真实 train/test 切分，只验证 plan 是否**要求** time-based split。
- 未做多 Agent 投票，未做 Streamlit。
- 使用真实市场数据（经适配器抓取），非合成样例数据。
- 不输出投资建议。

---

## 8. 后续如何扩展为 LLM Critic 或 Multi-Agent Critic

- **LLM Critic**：把 panel 摘要 + data_dictionary + executor 源码喂给 LLM，
  让其做语义级泄漏审查（如识别"这个特征其实用了未来信息"的隐蔽模式），
  输出与当前相同的 validation_report 结构。
- **Multi-Agent Critic**：多个 Critic 各从不同视角（时间有效性 / label 隔离 / 业务口径）审查，
  投票或择优，提升鲁棒性。
- **动态追踪**：不止静态查源码，而是真正 instrument executor 的执行轨迹，
  记录每个特征列在每行用到了哪些原始行的数据，从动态层面证明无未来函数。
- **baseline comparison**：rule-based vs single-agent vs multi-agent + critic，对比召回与误报。

---

## 9. 和临床 capstone 的迁移关系

金融 panel 有效性审查与临床 cohort 有效性审查同构：

| 金融场景 | 临床场景 | 共性问题 |
|---|---|---|
| label leakage（未来收益混入特征） | outcome leakage（未来结局混入特征） | 标签隔离 / 防 label leakage |
| announce_date <= date（财务可用日不晚于当前日） | 变量必须早于 prediction time（检验结果采集时间早于预测时点） | as-of 时间对齐 / 防时间泄漏 |
| ticker-date panel 有效性 | patient-time cohort 有效性 | 实体-时间宽表的可建模性 |
| Validity Critic（未来函数 + label 隔离 + 时间切分） | 临床统计/医学有效性审查器（时间泄漏 + 结局隔离 + 切分） | analysis-ready 的最后一道审查 |

迁移要点：把 Critic 的"检查项模板 + role 标注 + 源码静态检查"抽象成与领域无关的接口，
后续可同时审查金融 panel 与临床 cohort 两类 analysis-ready 表。
