# TTCAN 随机配置生成器详细教程

## 1. 功能和适用范围

`ttcan_random_config.py` 根据随机 seed 和约束范围构造 TTCAN 配置，调用现有 `ttcan_scheduler.py` 完成调度、校验、矩阵和节点 trigger list 生成。每个正式成功样本都包含完整 `config.json` 和自包含 `schedule.html`，可以继续用原调度器重放。

随机配置的“有效”表示通过当前调度器实现的静态 Level 1 模型及用户设置的要求。它不是 ISO 11898-4 的完整一致性证明，也不是实际 CAN 总线的仲裁、错误重传、主节点故障切换、时钟漂移或 FSE 状态机仿真。

配置、源码、注释、CLI 输出、表格和 HTML 使用英文。本文件是独立中文教程；命令和字段名保留英文。

## 2. 项目文件与数据流

| 文件 | 作用 |
| --- | --- |
| `ttcan_random_config.py` | 随机参数解析、种子派生、候选构造、调用原调度器、重试、去重和批次发布 |
| `ttcan_scheduler.py` | 原有静态调度、资源分配、触发检查和导出核心，随机生成器直接导入 |
| `assets/report_template.html` | 原有 HTML 模板；增加可选 Random Case 信息及空消息映射兼容处理 |
| `examples/random_generation_options.json` | 可编辑的完整随机范围、概率、容量和窗口权重示例 |
| `examples/random_nodes.json` | 固定 NodeA、NodeB、NodeC 角色、容量及实际主节点的示例 |
| `tests/test_random_config.py` | 随机生成、边界、回放、失败和重复检测测试 |
| `tests/run_random_regression.py` | 多 seed 内存回归与覆盖统计；输出统计报告，不发布样本配置 |
| `tests/browser_random_smoke.cjs` | 可选 Playwright 浏览器检查，不属于 Python 运行依赖 |
| `README.md` | 英文快速开始及限制 |
| `docs/manual_zh.md` | 原调度器的中文详细手册 |
| `docs/random_config_tutorial_zh.md` | 本文，解释随机生成器、输入、输出和验证 |
| `random_regression_report.json` | 交付时 100 个种子的独立内存回归统计 |
| `verification_report.json` | 交付时测试、浏览器检查和重要文件哈希快照 |
| `random_cases/run_<id>/` | 每次运行新建的独立批次目录，包含索引和各成功样本 |

数据流：`options + topology + seed → 候选配置 → Scheduler.compile() → 验证/特征检查 → 已解析 config → 独立重放比较 → export_schedule() → 英文报告和 HTML`。

生成器不复制原调度器的配置校验和排程引擎。帧保守位数、整数向上取整、Scheduler 和 export 接口直接复用。生成器负责资源预规划、随机选择、组装候选、重试、输出组织和重放验证。

## 3. 运行环境与最简单用法

Python 3.10 或更新版本即可，生成 JSON、CSV 和 HTML 不需要第三方 Python 包。解压整个项目，在项目目录执行：

```sh
python ttcan_random_config.py
```

不传 seed 时使用系统随机源产生 64 位 master seed；终端会打印它和批次目录。之后用该 seed、相同配置选项及相同软件版本即可重现。默认生成一个样本及其 HTML；已有批次不会被覆盖。

HTML 在浏览器中直接打开即可，无需服务器或网络。批次的 `index.html` 汇总所有样本，每一行链接独立的 `schedule.html`、`config.json` 和报告。

```sh
python ttcan_random_config.py --seed 20261029
python ttcan_random_config.py --seed 20261029 --count 10
python ttcan_random_config.py --seed 20261029 --case-start 7 --count 1
python ttcan_random_config.py --seed 20261029 --nodes 4 --masters 1 --messages-per-node 3
python ttcan_random_config.py --seed 20261029 --options examples/random_generation_options.json
python ttcan_random_config.py --seed 20261029 --nodes-file examples/random_nodes.json
python ttcan_random_config.py --seed 20261029 --max-attempts 100 --output-dir random_cases
python ttcan_random_config.py --seed 20261029 --require-features merged,rx,multi_node_arbitration
```

`--help` 列出全部参数。返回码：0 表示请求的正式样本全部成功；1 表示至少一个 case 无法发布；2 表示参数、文件或预先判定的约束错误。

## 4. 命令行参数与优先级

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--seed` | 系统随机 64 位值 | 显式范围为 0～2^256−1；以十进制输入 |
| `--count` | 1 | 本次要成功发布的不同 case 数量，允许 1～10000 |
| `--case-start` | 0 | 起始 case index；单独复现时保持原 index |
| `--nodes` | 无 | 固定节点数；相当于覆盖可变节点范围 |
| `--masters` | 无 | 固定候选 master 数量 |
| `--messages-per-node` | 无 | 固定每节点逻辑发送消息数量，包含作为仲裁候选的消息 |
| `--options` | 内建默认 | 从 JSON 文件读取生成策略 |
| `--nodes-file` | 无 | 加载固定节点及可选实际 master |
| `--max-attempts` | 100 | 每个 case 的候选尝试次数，1～10000 |
| `--output-dir` | `random_cases` | 批次根目录；每次创建新的唯一 run 目录 |
| `--allow-warnings` | 关闭 | 接受并保留调度器 warning；error 仍失败 |
| `--require-features` | 无 | 逗号分隔的特征名；每个正式 case 都必须满足 |

默认值先与 options 合并；CLI 的节点数、master 数和消息数覆盖 options 中的**可变范围**。若 options 将某项固定为 `[N,N]`，而 CLI 指定另一个固定值，则报 `CONFLICTING_INPUT`。

固定拓扑的节点数、角色、明确设置的优先级、容量和实际 master 是硬约束。`--nodes`、`--masters` 与拓扑不一致时报错；如果 options 明确提供的节点数或 master 数范围排除该拓扑，也报错。拓扑中未指定的容量从 options 的范围中选取；指定的容量不按默认随机范围放大或缩小。

CLI 的 `--require-features` 与 options 中的 `required_features` 取并集；`--allow-warnings` 可以把默认 false 显式变为 true。预先识别到明显不可能的组合时，程序在创建批次前返回错误。

## 5. 固定拓扑示例

`examples/random_nodes.json` 的顶层对象支持：

- `nodes`：节点对象数组。
- `node_defaults`：节点没有单独指定时使用的四类容量。
- `network.actual_master`：可选的实际 master。

节点支持 `node_id`、`node_name`、`role`、`master_priority`、`tx_message_capacity`、`rx_std_filter_capacity`、`rx_ext_filter_capacity`、`trigger_capacity`。角色必须是 master 或 slave，master 优先级是 0～7 且不重复。实际 master 必须位于候选 master 集合内。

```sh
python ttcan_random_config.py --seed 17 --nodes-file examples/random_nodes.json --messages-per-node 3
```

示例固定 NodeA 为当前 master、NodeB 和 NodeC 为 slave，同时固定每节点资源容量。报文、CAN ID、周期、窗口等仍在 options 的允许范围内随机选择。

`topology` 也可以直接写进 options JSON。如果 `topology` 与 `--nodes-file` 同时提供，内容必须一致。每个成功 case 保存的 `generator_options.json` 已包含最终拓扑，因此重现时不用依赖原始 nodes-file 路径。

## 6. 随机 options 字段完整说明

整数范围可以写 `[min,max]`；需要固定时可以写一个整数，或者 `[N,N]`。候选列表采用 JSON 数组。端点均包含在内。

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `schema_version` | 1 | 目前只支持版本 1 |
| `resource_mode` | `custom` | `custom` 或 `m_ttcan` |
| `controller_version` | `3.3.0` | hardware 模式必须选择该版本 |
| `node_count` | `[2,5]` | 节点数量范围，生成器支持 1～128 |
| `master_count` | `[1,2]` | 候选 master 数量范围，支持 1～8，不能多于节点数 |
| `messages_per_node` | `[2,3]` | 每节点逻辑发送消息数量，支持 0～128 |
| `matrix_cycles` | `[4,8,16]` | 从列表中选择矩阵周期数；生成器范围 1～64 |
| `basic_cycle_ntu` | `[2400,5000]` | Basic Cycle 长度范围，单位 NTU |
| `bitrate_choices` | `[500000,1000000]` | 位率列表，单位 bit/s；每个值必须使 NTU 纳秒数为整数 |
| `tx_enable_ntu` | `[2,6]` | Tx Enable Window 范围 |
| `guard_ntu` | `[10,18]` | 窗口保护预算 |
| `filter_delay_ntu` | `[1,6]` | 接收过滤处理延迟上界 |
| `min_trigger_spacing_ntu` | `[1,2]` | 节点有效触发最小间距 |
| `dlc` | `[0,8]` | Classical CAN 数据帧 DLC 范围 |
| `receiver_count` | `[1,2]` | 每条报文的接收节点数量范围，最多不超过其他节点数 |
| `periods` | `[1,2,4,8,16,32,64]` | 候选周期，单位 Basic Cycle；实际只选能整除所选矩阵的项 |
| `frame_formats` | `["standard","extended"]` | 允许的帧格式，至少一项 |
| `extended_probability` | 0.30 | 扩展帧优先选择概率；如果资源使优先格式不可行，会在允许格式中选择其他格式 |
| `rx_check_probability` | 0.60 | Single 报文的每个接收者被选中生成 Rx 检查的概率 |
| `explicit_number_probability` | 0.50 | 每个 Tx MNR 或 Filter MNR 采用显式编号的概率 |
| `repeat_probability` | 0.30 | 合适的 Single 消息新增互斥周期发送机会的概率 |
| `shared_group_probability` | 0.60 | 将合适的普通仲裁消息并入其他节点共享组的选择概率 |
| `window_extra_ntu` | `[5,30]` | 在最低预算上追加的随机窗口余量 |
| `window_start_ntu` | `[0,65535]` | 所有数据窗口起始 Time Mark 必须落入的范围 |
| `window_length_ntu` | `[1,65535]` | 每个数据窗口预留长度必须落入的范围 |
| `target_bus_load` | `[0.0,1.0]` | 数据窗口预留利用率的硬范围 |
| `window_mix` | `single=0.5, arbitration=0.2, merged=0.3` | 逻辑窗口组权重；不是每个样本的精确数量保证 |
| `node_capacity` | 见下一表 | 每类资源的独立随机容量范围 |
| `required_features` | `[]` | 每个成功 case 都必须具备的特征 |
| `allow_warnings` | false | true 时接受并记录现有调度器 warning |
| `topology` | null | 固定拓扑对象，结构与 nodes-file 一致 |

`node_capacity` 默认范围：

| 字段 | 默认范围 | 资源编号空间 |
| --- | --- | --- |
| `tx_message_capacity` | `[4,16]` | 节点 Tx Buffer |
| `rx_std_filter_capacity` | `[4,12]` | 标准帧接收过滤器 |
| `rx_ext_filter_capacity` | `[4,12]` | 扩展帧接收过滤器 |
| `trigger_capacity` | `[24,48]` | Trigger Memory，包括 Ref/Watch 等有效控制触发 |

例如容量为 8 表示合法编号 0～7。MNR 是节点内本地资源索引，CAN ID 是总线标识，trigger_index 是该节点内存条目索引，三者不同。

自研 custom 模式下，非 2 的幂周期可被随机选择，但必须整除矩阵长度；无法编码成现有 7 位 Cycle Code 时，原调度器以逻辑周期和相位表达。m_ttcan 模式只使用能由当前硬件规则编码的候选，并检查所选版本的资源上限。

生成器目前不随机启用 Gap，固定 `gap.enabled=false`。原调度器另有固定边界延长的简化 Gap 模型，不能当作本生成器已实现外部事件同步。

## 7. 修改常用约束

复制并编辑 `examples/random_generation_options.json`。例如，要求固定 4 节点、每节点 3 条报文、只使用标准帧、基本周期 3000 NTU，可以修改为：

```json
{
  "node_count": [4, 4],
  "master_count": [1, 1],
  "messages_per_node": [3, 3],
  "basic_cycle_ntu": [3000, 3000],
  "frame_formats": ["standard"]
}
```

options 文件可以只包含要修改的字段，缺失字段沿用内建默认。嵌套 `node_capacity` 和 `window_mix` 也支持只覆盖部分键。未知键会报错，防止拼写错误被忽略。

```sh
python ttcan_random_config.py --seed 80 --options examples/random_generation_options.json --nodes 4 --masters 1 --messages-per-node 3
```

CLI 固定数量不会强行扩大容量：例如每节点要求 17 条消息，而 Tx 容量上界为 16，会在生成前报不可行。使用固定拓扑时，某节点的固定 Tx 容量不足也会直接报错。

单节点场景必须把 `receiver_count` 的最小值设为 0，因为不存在其他节点可以接收。若需要多接收节点，至少需要三个节点，并让 receiver_count 上界至少为 2。

## 8. 三层 seed 与稳定复现

以稳定的 JSON 规范序列化：`sort_keys=true`、无多余空格、ASCII 编码，然后执行 SHA-256：

```text
case_seed = integer(SHA256(canonical(["ttcan-random-v1", master_seed, case_index])))
attempt_seed = integer(SHA256(canonical(["ttcan-random-v1", case_seed, attempt])))
```

每次 attempt 使用新的 `random.Random(attempt_seed)` 实例。master seed 控制批次；case seed 唯一确定 case index 对应的随机流；attempt seed 只控制该 case 的某次候选。

因此 case 0 重试 20 次也不会改变 case 1。批次生成 case 7 后，单独执行相同 seed 和 `--case-start 7 --count 1`，并提供相同最终 options，即可得到同一 case。

每个样本的 seed 以十进制字符串保存，而不是让 JavaScript 把 256 位整数当成普通浮点数处理。HTML 因此可以完整显示 seed。

复现依赖相同生成器版本、scheduler 源码、Python 版本和最终 options。时间戳、临时目录和运行目录不进入 config 的生成元数据。不同目录下重跑不会导致配置内容因路径不同而变化。

## 9. 已交付批次的真实示例

交付批次路径：

```text
random_cases/run_20261029_icw2pf45/
```

这个批次由 `--seed 20261029 --count 10` 生成，10 个 case 均已通过现有调度器校验。`case_0000` 是一个四节点、8 个 Basic Cycle 的案例：

- 位率：500000 bit/s。
- NTU：2000 ns。
- Basic Cycle：3953 NTU。
- 实际 master：Node1，优先级 7。
- 参考 CAN ID：0x087。
- 数据发送节点和接收节点根据随机配置分配。
- 包含 Single、普通 Arbitration、Merged、多节点共享仲裁、扩展帧和 Rx 检查。

观察 `case_0000/config.json` 中的 `Msg000`：

| 项目 | 实际值 |
| --- | --- |
| Sender / Tx MNR | `Node0 / 0` |
| CAN ID / DLC | `0x285 / 2` |
| Frame format | standard |
| Period / phase | 2 / 1，生效于 Cycle 1、3、5、7 |
| Single Time Mark | 1680 NTU |
| Exclusive reservation | 1680～1788 NTU |
| Conservative frame time | 76 NTU |
| Node2 Filter / Rx Time Mark | 0 / 1762 NTU |
| Node3 Filter / Rx Time Mark | 5 / 1762 NTU |

这里 Tx Enable=2、过滤延迟=3，因此最晚预算为 `1680 + 2 + 76 + 3 = 1761`，Rx 在 1762 检查。两个接收节点拥有各自的过滤器和触发器，虽然使用相同 Rx Time Mark，也不构成同节点冲突。该案例在初始布局上整体随机偏移了 1598 NTU，保持发送与接收检查的相对关系。

该消息的 Cycle Code 由 period 2、phase 1 编码得到 `0b0000011`。Cycle 1 的离线绝对发送请求坐标为 `3953 + 1680 = 5633 NTU`，即 11266 us。Rx 在 `3953 + 1762 = 5715 NTU`，即 11430 us。

打开该 case 的 HTML，在 Node Message Maps 里点击 Msg000，可同时查看 Node0 Tx0、Node2 Filter0 和 Node3 Filter5；表中的对应 trigger 会高亮。矩阵中的窗口是预留区间，标记是触发机会，不是总线实测波形。

## 10. 从生成到发布的算法

1. `resolve_options` 合并默认值、用户 options、CLI 和可选固定拓扑，检查格式、范围及明显冲突。
2. 根据 master seed 和 case index 派生 case seed；每次候选派生独立 attempt seed。
3. 确定节点数、候选 master、实际 master、每节点消息数量及资源容量。随机容量选择至少满足该节点已选的 Tx 消息数量；固定容量不足时失败。
4. 选择位率、NTU、矩阵长度、兼容周期、Basic Cycle 和时间预算。Level 1 下一个 NTU 对应一个 CAN 位。
5. 按可用接收过滤器容量选择接收者、帧格式和 DLC，选择唯一 CAN ID。显式编号先规划，未指定编号由现有调度器分配。
6. 按 Single / Arbitration / Merged 权重构造逻辑组。Merged 至少包含同一发送节点的一条 Merged 和最终 Arbitration；可以有其他节点的 Arbitration 候选加入共享组。
7. 对合适的 Single 消息新增互斥周期发送机会，复用原来的 Tx MNR。
8. Single 接收者可以设置自动 Rx 检查；Arbi/Merged 接收者仅保留过滤器映射。
9. 调用现有 `Scheduler.compile()` 自动放置窗口。多节点普通 Arbitration 组经一次重新编译，使不同节点的候选触发使用共同时间标记。
10. 按用户的窗口起点/长度范围检查预留；在可行范围内随机选择统一时间偏移，同步移动数据 Tx、Rx 和组边界，再调用原调度器验证。
11. 检查 scheduler 的 error/warning、hard features 和预留负载范围。
12. 用最终解析后的 config 再编译，比较矩阵、节点消息映射及 trigger list 的语义签名。
13. 在临时目录写完整文件，确认成套文件存在后，以目录重命名方式发布。任何一步失败都不会发布半成品 case。

这不是无条件穷举。对高负载、容量很紧或特征组合很苛刻的设置，可能在规定的 max-attempts 内找不到样本。生成失败不等于数学上已证明绝对不存在可行解；失败报告会说明实际发现的问题。

## 11. Merged、Arbitration 和 Rx 的边界

合并仲裁组的逻辑窗口是一个整体。它可以包含来自多个节点的候选；每个节点的 Merged 序列在自身生效周期内必须由 Arbitration 结束。生成器让同组基本成员采用兼容的周期和相位，再交给原调度器逐节点、逐周期检查。

普通共享 Arbitration 组的不同节点可在同一 Time Mark 发起候选。它们仍要通过 CAN 仲裁；本工具只生成候选窗口，不模拟哪一个实际胜出。Merged 的 Time Mark 同样不能直接当作真实报文 SOF。

目前原调度器只支持对 Single 独占报文生成通过校验的 Rx 检查。因此生成器不对 Merged/Arbi 报文本身配置 `check_reception=true` 或 `rx_checks`。它们仍然可以配置接收节点和本地过滤器。本条是当前工具实现边界，不是对所有 M_TTCAN 芯片的协议结论。

Single 的 Rx 检查还要考虑“上一次接受的过滤结果”：其他报文可能在目标之后完成并覆盖结果，或前一次检查已经清除结果。最终判定由现有调度器给出。如果候选的仲裁流量可能影响检查而只能得出不确定结论，默认把 warning 当成候选拒绝并重试；显式 `--allow-warnings` 可保留该样本及 warning。

## 12. 随机权重、特征和负载

`window_mix` 是逻辑窗口组选择权重，不是精确配额。一个 Merged 组中的最后一条 Arbitration trigger 仍计入 Merged 组，而不是再增加一个普通 Arbitration 逻辑窗口。

每个 case 的 `coverage.window_mix` 包括：
- `target_fraction`：权重归一化后的目标比例。
- `actual_fraction`：实际逻辑组数量除以逻辑组总数。
- `logical_count`：实际逻辑组数。
- `difference`：实际减去目标。

`expanded_window_counts` 是展开各 Basic Cycle 之后的窗口数量，与逻辑组数不同。`data_window_reservation_fraction` 是所有周期的数据窗口预留长度总和除以 `basic_cycle_ntu × matrix_cycles`，不含参考报文和空闲区间；它不等于实测 CAN 利用率。

`required_features` 和 `--require-features` 支持：

| 特征 | 判定 |
| --- | --- |
| `single` | 至少一个独占逻辑窗口 |
| `arbitration` | 至少一个普通 Arbitration 逻辑窗口 |
| `merged` | 至少一个 Merged 组且存在有效 Merged trigger |
| `rx` | 至少一个 Rx_Trigger |
| `extended` | 至少一条扩展帧 |
| `multiple_receivers` | 至少一条报文有两个或更多接收节点 |
| `shared_mnr` | 同一节点多个 Tx trigger 引用相同 Tx MNR |
| `shared_time` | 同一节点不同 trigger 共享 Time Mark，且生效周期集合互斥 |
| `multiple_masters` | 多个候选 master |
| `multi_node_arbitration` | 某个仲裁或合并窗口有多个发送节点 |
| `high_load` | 数据窗口预留利用率至少 0.70 |

这些特征逐 case 硬检查，不是只要求整批合计出现一次。如果要求 `extended` 但 frame_formats 排除 extended，或者 high_load 但 target_bus_load 上界低于 0.70，会直接报不可行。

高负载可以收紧 `target_bus_load`，同时提高允许的消息数、增加周期为 1 的机会或缩短允许的 Basic Cycle。所有条件仍受资源容量和窗口重叠检查限制，可能需要调整 max-attempts。不要把目标权重误认为特征硬保证；需要确定包含某类型时使用 required_features。

`window_start_ntu` 约束所有数据窗口的起点，`window_length_ntu` 约束各预留窗口的完整长度。工具先构造可行相对布局，再在满足起点范围和下一参考请求边界的公共偏移范围内随机选择一个偏移；各 Tx、Rx 和组的相对时序保持不变。不同窗口的相对位置还会随报文长度、周期、组成员和额外余量变化。最终结果由原调度器重新校验；如果不存在满足所有起点或长度范围的公共偏移，候选会被拒绝并记录原因。

## 13. 编号分配与固定容量

每个发送节点有独立 Tx 编号空间；每个接收节点分别有标准过滤器和扩展过滤器编号空间。生成器先为所有资源预规划互不冲突的槽位，按概率决定哪些编号写入候选，剩余编号由 scheduler 按其规则分配。scheduler 本身先保留显式编号，再处理自动编号。

相同消息对象出现多个周期机会时使用同一个 Tx MNR，但每个机会可能占用一个 trigger 条目。接收端如果启用多个互斥周期检查，同样可能有多个 Rx trigger 引用一个 Filter MNR。

固定拓扑的容量不被自动增加。标准或扩展过滤器数量不足时可能通过重试选择其他合法格式和接收者；如果所有允许格式都无法满足最少接收节点数量，该候选失败。若固定容量不可能容纳最低消息数量，配置阶段就报错。

## 14. 批次、样本和文件字段

每次在 output-dir 下创建唯一的 `run_<master_seed>_<suffix>`。后缀用于避免覆盖，与 seed 派生、配置内容无关。

| 文件 | 内容及作用 |
| --- | --- |
| `manifest.json` | 批次主种子、完整 options、源码/选项指纹、各 case 状态与汇总 |
| `index.html` | 英文批次总览，链接使用相对路径，可以随整个目录移动 |
| `case_NNNN/config.json` | 已分配资源、时间和周期的自包含配置，可以直接交给原调度器 |
| `case_NNNN/generator_options.json` | 该 case 最终随机策略及固定拓扑，用于生成器复现 |
| `case_NNNN/generation_report.json` | 种子、attempt、失败历史、配置哈希、coverage 和复现命令 |
| `case_NNNN/validation_report.json` | 原调度器的 valid、error、warning、资源和限制 |
| `case_NNNN/effective_config.json` | 原调度器导出的已解析配置 |
| `case_NNNN/schedule.html` | 自包含英文页面，内嵌该 case 的完整数据和生成信息 |
| `case_NNNN/global_timeline.csv` | 展开的绝对时间窗口及触发机会 |
| `case_NNNN/system_matrix.csv/json` | 全局系统矩阵的长表和结构化数据 |
| `case_NNNN/nodes/<node>_message_map.csv/json` | 每节点本地发送缓冲和接收过滤器映射 |
| `case_NNNN/nodes/<node>_trigger_list.csv/json` | 每节点 trigger 内存表及填充项 |
| `failures/case_NNNN.json` | 不能发布的 case 的原因、每次 attempt seed 和 issue；只有失败时存在 |

单样本成功发布必须包含以上必要文件。失败案例不会出现正式的 case_NNNN 目录；批次索引显示 FAIL 并链接 failure 文件。如果检测到去掉 metadata 后完全相同的配置，后一个 case 报 `DUPLICATE_CONFIG`，不会算成一个新的成功样本。

`manifest.summary` 的 `requested`、`succeeded`、`failed` 是样本数量；`retries` 是成功样本在成功前的失败 attempt 数。`cases_with_warnings` 和 `warning_count` 分别记录带 warning 的成功样本数量和 warning 总数；批次索引也逐项显示 warning 数。`attempt_issue_counts` 汇总所有候选 issue 的出现次数；同一个 attempt 在多个周期可能产生多条同码 issue，所以它不等于失败 attempt 数。还包含 feature、逻辑/展开窗口、帧格式、DLC、周期和节点数分布。

`config.json` 与 `effective_config.json` 都是最终调度配置；配置的 `metadata.random_generation` 记录 master seed、case index、case seed、attempt、attempt seed、生成器版本、Python 版本、scheduler/选项 SHA-256、特征和主要约束。配置中的 CAN ID、MNR、Time Mark、period、phase 均已具体化。

`generation_report.json` 还包含：`reproduce_command`、`success`、`retry_count`、`attempt_failures`、`resolved_options`、`config_sha256`、`config_semantic_sha256`、`core_signature`、`coverage`、`scheduler_validation`、`model_limitations` 和 `protocol_conformance_claim`。

`config_sha256` 对文件字节计算；`config_semantic_sha256` 去掉 metadata 后对规范 JSON 计算；`core_signature` 对矩阵、消息映射和 trigger list 的规范 JSON 计算。语义去重不依赖 seed 标签；报文标识、周期、时窗和其他配置仍属于语义内容。

原调度器各 CSV/JSON 详细字段及 trigger 内存语义见 `docs/manual_zh.md`。本教程的字段与原手册一起覆盖所有新增和复用文件。

## 15. HTML 操作和批次索引

在批次 `index.html` 中查看 master seed、请求/成功/失败数、每个 case 的节点数、消息数、逻辑窗口组成、预留负载、最大节点 trigger 容量使用率及特征。点击 View、Config、Report、Validation 进入相应文件；失败行链接失败报告。

打开一个 `schedule.html`，顶部 Random Case 区显示 case index、master/case seed、attempt、生成器版本、主要约束、实际特征及复现命令。主视图显示全局时间轴和系统矩阵，点击 `Full matrix` 查看该随机案例的全部周期，不假设一定有 64 行。

Node、Message、Window、Matrix、Start、Count 和 Zoom 控制筛选和缩放。点击窗口或节点表中的消息，Selection Inspector 显示关联配置，相关映射及触发记录高亮。Merged/Arbi 斜纹区域表示候选预留，Watch 虚线表示正常情况下不执行的监视。

HTML 含全部数据、样式和脚本，可以把单个 `schedule.html` 拷贝出来离线查看。批次 index 使用相对链接；若希望所有链接都有效，应移动整个 run 目录而不是只移动 index。

## 16. 复现与一致性比较

每个 `generation_report.json` 和对应 HTML 中都有 `reproduce_command`。从项目目录执行它，会创建一个新的 run 目录，不覆盖原样本。

已交付 case 0 的命令为：

```sh
python ttcan_random_config.py --seed 20261029 --case-start 0 --count 1 --max-attempts 100 --options random_cases/run_20261029_icw2pf45/case_0000/generator_options.json --output-dir random_cases
```

也可以不重新随机，直接将已解析配置交给原调度器：

```sh
python ttcan_scheduler.py --config random_cases/run_20261029_icw2pf45/case_0000/config.json --output-dir replay_case_0000
```

生成器在发布前已内部重编译 config，核对 `system_matrix`、各节点 message maps 和 trigger lists。用户手工重放后，可对比两次的 `system_matrix.json` 和 `nodes/` 下对应 JSON 的解析内容。目录、HTML 中的复现路径或运行报告可能不同，不应拿这些路径文本判断调度是否一致。

若将整个项目移到新位置，保持相对目录后从项目根目录运行；若只移动 case 到其他目录，将复现命令中的 `--options` 路径改为移动后 `generator_options.json` 的实际路径。

## 17. 常见错误和处理

| 代码 | 发生原因及处理 |
| --- | --- |
| `INVALID_OPTIONS` | 类型、范围、未知字段、空周期集合、非法权重或硬件版本不合法；核对 options 和 CLI |
| `CONFLICTING_INPUT` | 固定 CLI、options 与 topology 相互矛盾；统一固定值 |
| `INVALID_TOPOLOGY` | 节点 ID、角色、优先级、实际 master 或固定容量非法 |
| `INFEASIBLE_CONSTRAINT` | 可在生成前判定的固定要求不可能同时满足，例如消息最小数超过 Tx 容量上界 |
| `RESOURCE_EXHAUSTED` | 某个候选的 Tx 或接收过滤器资源不足；在允许范围内调整容量、接收者或帧格式 |
| `IDENTIFIER_EXHAUSTED` | 有界尝试中未找到可用 CAN ID |
| `FEATURES_MISSING` | 当前候选没有满足逐 case 要求的全部特征；根据概率和容量调整范围 |
| `TARGET_LOAD_UNMET` | 数据预留利用率不在要求范围；调整 Basic Cycle、消息数或周期分布 |
| `WINDOW_START_UNMET` | 没有统一偏移能同时满足全部窗口起点范围及下一参考请求边界 |
| `WINDOW_LENGTH_UNMET` | 某个候选的窗口长度超出请求范围；调整长度、负载或帧格式 |
| `REPLAY_MISMATCH` | 最终 config 再编译后的矩阵、映射或 trigger 不一致；该候选不会发布 |
| `MAX_ATTEMPTS` | 用完每 case 的有界尝试次数；检查失败历史及硬约束 |
| `DUPLICATE_CONFIG` | 批次中去除 metadata 后与早先 case 完全相同；该项不计作成功 |
| `HTML_TEMPLATE` | 原调度器的 HTML 模板缺少随机元数据插入标记 |
| `MISSING_ARTIFACT` | 一个必需输出未成功写出，不发布半成品 |
| `OUTPUT_COLLISION` | 目标 case 已存在；换批次目录，不要覆盖旧结果 |
| `LANGUAGE` | 输入或输出目录包含受限制的 CJK 文本 |

现有调度器产生的 `WINDOW_OVERLAP`、`TRIGGER_COLLISION`、`MERGED_NOT_CLOSED`、`RX_TOO_EARLY`、`RX_ARBITRATION_UNCERTAIN` 等 issue 会保留在 attempt 记录中。它们的详细解释见 `docs/manual_zh.md`。

## 18. 多种子回归与浏览器检查

```sh
python -m unittest discover -s tests -v
python ttcan_scheduler.py --check-english
python tests/run_random_regression.py --start-seed 0 --count 100 --max-attempts 100 --output random_regression_report.json
```

回归脚本在内存中调用候选生成、调度校验和重放签名，不发布 100 份 config 或 HTML；它只生成一个统计报告。正式样本仍由主随机生成器成套导出。

回归报告含 `generator_version`、`python_version`、`scheduler_sha256`、`generator_sha256`、`options_sha256`、`options`、`start_seed`、`requested`、`succeeded`、`failed`、`max_attempts`、`total_failed_attempts`、`issue_counts`、`feature_counts`、窗口/帧格式/周期/DLC/节点数分布、`seed_results` 和 `scope`。`seed_results` 每项记录 seed、case seed、成功状态、失败 attempt 数、问题码及成功样本的特征和签名。

实际浏览器验收使用 Playwright 开发依赖；它不是生成工具的运行依赖。安装 Playwright 和可用 Chromium 后可执行：

```sh
node tests/browser_random_smoke.cjs random_cases/run_20261029_icw2pf45/case_0000/schedule.html random_cases/run_20261029_icw2pf45/index.html
```

可通过 `TTCAN_BROWSER_EXECUTABLE` 指定 Chromium，`TTCAN_SCREENSHOT_DIR` 指定临时验收截图目录。测试检查实际节点数和周期行数、筛选、消息映射、Merged 与 trigger 关联、移动端溢出、批次索引链接、浏览器脚本错误和外部请求。交付包不包含截图。

## 19. 主要函数与扩展位置

| 函数/对象 | 职责 |
| --- | --- |
| `DEFAULT_OPTIONS` | 随机范围、概率、权重和默认资源容量 |
| `GenerationError` | 英文错误码、信息和附加字段 |
| `canonical` / `digest` / `source_hash` | 稳定规范 JSON、SHA-256 和 scheduler 源码指纹 |
| `derive_seed` | 对固定域标签与整数参数做 SHA-256，派生独立 seed |
| `read_json_file` | 加载英文 JSON 并检查 CJK 字符 |
| `merge_options` / `fixed_override` / `resolve_options` | 合并默认和用户选项，执行 CLI 优先级、硬约束及范围检查 |
| `validate_topology` | 检查固定节点、角色、优先级、容量及实际 master |
| `resource_slot` | 在节点本地资源空间选择未占用槽位，并随机决定显式还是自动编号 |
| `build_candidate` | 用独立随机实例构造网络、消息、收发关系和调度组 |
| `align_arbitration_candidates` | 对多节点普通仲裁组统一候选 Time Mark，重新调用现有 scheduler |
| `apply_window_bounds` | 检查长度范围，随机整体平移数据窗口及其 Tx/Rx 标记，并重新校验 |
| `coverage` | 从已编译调度数据提取特征、窗口权重实现情况和资源统计 |
| `semantic_hash` | 去除 metadata 后对配置做确定性语义去重 |
| `core_signature` | 对矩阵、节点消息映射和 trigger list 生成重放签名 |
| `make_case` | 独立 case 的有界尝试、scheduler 校验、硬特征检查和重放验证 |
| `promote_case` | 临时目录生成完整文件，注入页面生成信息，检查后原子发布 |
| `batch_index` / `run_batch` | 批次去重、失败汇总、manifest 和离线索引 |
| `reproduce_command` | 为已发布样本生成从项目目录运行的完整命令 |
| `main` | CLI 参数、配置加载、英文输出及退出码 |

后续如果原调度器增加新的受支持模式，应同时更新随机约束、样本构造、独立验收测试、HTML 展示和本教程。特别是仲裁结果、Merged 报文的确定性 Rx 检查以及真正外部事件 Gap，不能通过简单放宽现有检查来冒充已实现。
