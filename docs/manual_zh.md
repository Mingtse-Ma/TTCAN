# TTCAN 调度工具详细说明

## 1. 工具用途和适用边界

本工具用 Python 标准库生成 Classical CAN / TTCAN 静态通信计划。核心输出是总线窗口、统一系统矩阵、各节点自己的消息资源映射和触发器内存表。HTML 是这些数据的离线浏览器，不是另外维护的一套演示数据。

运行需要 Python 3.10 或更新版本。运行和测试都不需要第三方 Python 包。查看 HTML 需要支持现代 JavaScript 的浏览器。解压后请保持主脚本、示例配置和 HTML 模板的相对位置。

当前时间模型是 Level 1 的静态预留模型，一 NTU 等于一 CAN 位时间。绝对时间是工具为了展开多个周期而建立的坐标，不表示已经实现 Level 2 的 Global_Time、TUR 校准或节点时钟同步。

代码不仿真总线逐位编码、CAN 错误重传、实际仲裁胜负、主节点故障切换、应用置位发送请求的执行过程或完整 FSE/Tx Handler 状态机。生成的表不是寄存器镜像，不能直接写入芯片。对于仲裁窗口，页面展示的是候选集合和预留预算，不承诺每个候选一定发送成功。

脚本、注释、配置、表头、报错和 HTML 全部使用英文。中文只保存在以 `_zh.md` 结尾的说明文档中。

## 2. 运行方法

在项目目录下执行：

```sh
python ttcan_scheduler.py
```

这条命令无须交互，读取 `examples/nodea_nodeb_demo.json`，向当前工作目录的 `output` 写入结果。打开 `output/schedule.html` 即可浏览。脚本从自身目录寻找示例和模板，因此也可以从其他目录调用脚本；相对输出路径仍相对于当前工作目录。

```sh
python ttcan_scheduler.py --config examples/nodea_nodeb_demo.json
python ttcan_scheduler.py --output-dir output_custom
python ttcan_scheduler.py --config examples/automatic_demo.json --output-dir output_auto
python ttcan_scheduler.py --interactive --output-dir output_interactive
python ttcan_scheduler.py --config examples/nodea_reference_raw.json --validate-only --output-dir diagnostics/raw
python -m unittest discover -s tests -v
python ttcan_scheduler.py --check-english
python ttcan_scheduler.py --help
```

`--interactive` 和 `--config` 互斥。交互模式询问节点角色、容量、候选主节点优先级和实际主节点，然后生成有来源标记的演示报文，并自动放置窗口。若需要精确报文、过滤器、截止时间或窗口位置，请编辑 JSON 配置后使用 `--config`。

`--validate-only` 只写 `validation_report.json`，不重新生成其他输出。建议使用独立诊断目录，避免把新校验报告与先前生成的 HTML 混在一起。普通失败编译同样只写失败报告，不生成新的有效计划。

退出码 0 表示校验成功，1 表示调度或语言检查失败，2 表示文件读写、命令输入或交互解析错误。原始冲突示例返回 1 是预期结果。

## 3. 文件之间如何协作

数据流如下：配置文件进入 `Scheduler`，完成规范化、资源分配、窗口放置、接收检查生成和控制触发生成；校验通过后建立矩阵和绝对时间事件；CSV/JSON 导出与 HTML 数据嵌入都使用这份内部模型。

| 文件 | 输入、职责及输出 |
| --- | --- |
| `ttcan_scheduler.py` | 命令行入口和调度核心；读取配置与模板，输出所有计划文件和校验报告 |
| `assets/report_template.html` | 纯英文静态模板；包含页面布局、CSS 和 JavaScript；唯一数据占位符由编译结果替换 |
| `examples/nodea_nodeb_demo.json` | 默认可运行双节点案例，包含明确的 Rx3 修正、符号时间点和补充参数来源 |
| `examples/nodea_reference_raw.json` | 保留原记录中的 Rx3 周期冲突；用于失败验证，不是有效默认计划 |
| `examples/automatic_demo.json` | 三节点自动放置案例；演示周期 1、2、4、自动 MNR 和自动接收检查 |
| `tests/test_scheduler.py` | 使用 `unittest` 验证语义、异常配置、导出、退出码和语言约束 |
| `tests/browser_smoke.cjs` | 可选开发验收脚本，用 Playwright 检查默认报告的真实浏览器交互，不是运行时依赖 |
| `README.md` | 英文快速开始、文件定位、边界说明和一手资料链接 |
| `docs/manual_zh.md` | 本中文详细文档；解释全部配置、源码、输出及修改方法 |
| `output/effective_config.json` | 最终采用的配置，包含默认参数、分配后的编号和确定的传输时间点，可重新作为输入 |
| `output/global_timeline.csv` | 已按绝对时间排序的窗口和触发机会；包含非执行型 Watch 监视事件 |
| `output/system_matrix.json` | 公共列定义和每个 Basic Cycle 的单元格列表 |
| `output/system_matrix.csv` | 同一矩阵的长表；一行对应一个周期中的一个列区间 |
| `output/nodes/NodeA_message_map.csv`、`.json` | NodeA 本地 Tx Buffer 和 Rx Filter 映射 |
| `output/nodes/NodeB_message_map.csv`、`.json` | NodeB 本地资源映射；编号不能拿来直接替代 NodeA 的编号 |
| `output/nodes/NodeA_trigger_list.csv`、`.json` | NodeA 排序后的内存条目，包括未用槽位填充 |
| `output/nodes/NodeB_trigger_list.csv`、`.json` | NodeB 的本地内存条目，不含参考报文发送触发 |
| `output/validation_report.json` | 校验结论、问题上下文、资源使用和模型范围 |
| `output/schedule.html` | 独立英文可视化，完整数据内嵌，不访问旁边的 CSV/JSON，不需要服务器 |
| `diagnostics/raw/validation_report.json` | 原始冲突案例的失败证据；以独立命令生成 |
| `verification_report.json` | 交付时的测试、英文扫描、浏览器检查、原始失败摘要和重要文件哈希，不由普通调度命令自动更新 |

增加节点后，节点输出文件按相同命名规则增加。Python 运行时可能生成 `__pycache__`，它是解释器缓存，不是交付数据，不参与语言检查或打包。

## 4. 默认案例来源与修正

NodeA 是实际时间主节点，NodeB 是时间从节点。NodeB 沿用此前讨论中根据 NodeA 局部例子推导的配套方案，不标记为 Bosch 手册原始 NodeB 表。

原记录中 NodeA 的 Rx3 使用 `0b1000000`，在 Cycle 0、Mark1 激活。同时 Tx7 的 `0b0000100` 也在 Cycle 0、Mark1 激活。同一个节点、同一个周期、同一 Time Mark 出现两个有效触发，违反触发列表约束。配套 NodeB 在 Cycle 1 发送 Msg3，因此可运行示例将 Rx3 明确改成 `0b1000001`。这是演示案例的修正，不是对 Bosch 官方勘误的声明。

原始文件仍保留错误周期条件，主脚本深拷贝输入后处理，不回写来源文件。失败报告会同时指出同点冲突、目标消息未在相应周期完成，以及接收检查结果为空。

CAN ID、具体时间值、8 字节数据帧和资源容量都是为了使案例独立运行而补充的演示配置。`metadata.assumptions` 在有效配置和 HTML 中保留这些说明。

| 消息 | 演示 CAN ID | 发送节点 / Tx MNR | 接收节点 / Filter MNR | 是否配置接收检查 |
| --- | --- | --- | --- | --- |
| `Msg7` | `0x107` | `NodeA / 7` | `NodeB / 0` | 是，`Mark2` |
| `Msg3` | `0x103` | `NodeB / 0` | `NodeA / 3` | 是，`Mark1` |
| `Msg2` | `0x102` | `NodeA / 2` | `NodeB / 1` | 否，仲裁候选 |
| `Msg4` | `0x104` | `NodeA / 4` | `NodeB / 2` | 否，仲裁候选 |
| `Msg5` | `0x105` | `NodeB / 1` | `NodeA / 5` | 是，`Mark3` |
| `Msg6` | `0x106` | `NodeB / 2` | `NodeA / 6` | 是，`Mark4` |

接收节点列表表示应用关注及接受过滤器配置，CAN 总线仍是广播介质。存在过滤器只代表消息可以被接受；它不会自动产生一个定时 Rx_Trigger。

## 5. 时间单位、帧时间和周期

默认位率为 1 Mbps，一 CAN 位为 1 us，一 NTU 为 1000 ns。基本周期长 2000 NTU，64 个基本周期构成 128000 NTU，即 128 ms 的矩阵。

| 标记 | NTU | 在案例中的含义 |
| --- | ---: | --- |
| `T0` | 200 | NodeB 在 Cycle 1 请求发送 Msg3 |
| `Mark1` | 400 | NodeA 发送 Msg7 或检查 Msg3，生效周期互斥 |
| `Mark2` | 600 | NodeB 检查 Msg7 |
| `T23` | 800 | NodeB 在 Cycle 3 请求发送 Msg5 |
| `Mark3` | 1000 | Cycle 1 的 Merged 起始触发；Cycle 3 的 Msg5 检查 |
| `T34` | 1200 | NodeB 在 Cycle 4 请求发送 Msg6 |
| `Mark4` | 1400 | Cycle 1 的最终 Arbitration 触发；Cycle 4 的 Msg6 检查 |
| `Mark5` | 1600 | 合并仲裁预留窗口结束，不自动生成触发 |
| `Mark6` | 1998 | 请求下一参考报文 |
| `Mark7` | 2300 | 本轮参考报文超时监视位置，正常情况下不执行 |

工具采用关系 `Basic Cycle = reference Tx mark + 1 NTU + 1 CAN bit time`。默认两项延迟合计 2 NTU，因此 1998 的请求对应下一参考报文的计划 SOF 2000。Cycle Time 的参考原点对应参考报文 SOF，但硬件直到参考帧成功处理后才确认并重建参考标记；因此不能在参考帧尚未完成时安排普通触发。

Watch 在 2300 而不是 2000，是给下一参考帧完成保留监视余量。正常计划中的下一参考报文及时完成，旧周期的 Watch 不执行。它可以投影到后续周期的绝对时间区域，但不占用总线，也不是该时刻真的发送了某个帧。

无 Gap 时，绝对时间按下式计算：

```text
absolute_ntu = matrix_index * matrix_duration_ntu
             + cycle * basic_cycle_ntu
             + offset_ntu
time_us = absolute_ntu * ntu_ns / 1000
```

源代码时间计算使用整数及向上整除。HTML 为显示比例和小数单位使用浏览器浮点，不参与调度决策。

`frame_bits` 对 SOF 到 CRC 序列采用保守填充上界。标准帧可填充部分长度为 `34 + 8 * DLC`，扩展帧为 `54 + 8 * DLC`；额外填充预算采用该长度除以 4 向上取整，再增加 13 位，包含 CRC delimiter、ACK、EOF 和 3 位帧间隔。这是偏保守预留，不是某一 payload 的精确编码。

因此本实现对标准 8 字节数据帧预留 136 位，对参考报文 DLC=1 预留 66 位。帧时间由 `ceil(reserved_bits * 1e9 / (bitrate * ntu_ns))` 得到。显式 `worst_frame_ntu` 可以更保守，但不能小于计算上界。

独占窗口最小预算为 `frame_time_ntu + tx_enable_ntu + guard_ntu`。默认是 136+4+10=150 NTU，示例使用 160 NTU。最晚接收过滤完成预算为 `tx_mark + tx_enable_ntu + frame_time_ntu + filter_delay_ntu`，Rx 检查必须严格晚于这个值。

## 6. Cycle Code 的编码

Cycle Code 是 7 位字段。值 0 或 1 表示每周期有效；其他值的最高有效位表示重复周期，低位表示相位。

| Code | 周期 | 相位 | 在默认 64 周期矩阵中生效 |
| --- | ---: | ---: | --- |
| `0b0000000` | 1 | 0 | 全部 |
| `0b0000100` | 4 | 0 | 0、4、8、…、60 |
| `0b1000001` | 64 | 1 | 1 |
| `0b1000011` | 64 | 3 | 3 |
| `0b1000100` | 64 | 4 | 4 |
| `0b0010011` | 16 | 3 | 3、19、35、51 |

`encode_cycle_code` 对周期 1 使用规范编码 0。m_ttcan 模式允许 1、2、4、8、16、32、64 周期的硬件编码。custom 可以表达整除矩阵长度的其他逻辑周期，但此时 `cycle_code` 为 null，并保留周期、相位和展开集合；不假装存在对应硬件编码。

Cycle 0～4 只是局部视图，不是一个长度为 5 的循环矩阵。若将截图当成完整 5 周期矩阵，周期 4 和周期 64 的重复关系都会被破坏。

## 7. NodeA 每一条有效触发

文件按 Time Mark、类型码、消息名和相位稳定排序，所以相同时间点的排列不必与原文行序相同，语义由生效周期决定。

| 索引 | Time Mark | 类型 / MNR | 条件 | 作用 |
| --- | --- | --- | --- | --- |
| 0 | `Mark1 / 400` | `Single / 7` | 周期 4，相位 0 | Msg7 在 0、4、…、60 的发送机会 |
| 1 | `Mark1 / 400` | `Single / 7` | 周期 64，相位 3 | Msg7 在 Cycle 3 的额外发送机会，复用同一 Tx Buffer |
| 2 | `Mark1 / 400` | `Rx / 3` | 周期 64，相位 1 | 检查 NodeB 在 T0 发送的 Msg3；本地 Filter3，不是 NodeB 的 Tx0 |
| 3 | `Mark3 / 1000` | `Merged / 2` | 周期 64，相位 1 | Msg2 加入 Merge1 合并仲裁序列 |
| 4 | `Mark3 / 1000` | `Rx / 5` | 周期 64，相位 3 | 检查 T23 后已完成的 Msg5 |
| 5 | `Mark4 / 1400` | `Arbi / 4` | 周期 64，相位 1 | Msg4 是 Merge1 最后一个发送触发，关闭该节点的合并序列 |
| 6 | `Mark4 / 1400` | `Rx / 6` | 周期 64，相位 4 | 检查 T34 后已完成的 Msg6 |
| 7 | `Mark6 / 1998` | `Ref / N/A` | 每周期，正常状态 | 当前实际 master 请求下一参考报文 |
| 8 | `Mark7 / 2300` | `Watch / N/A` | 每周期，正常状态 | 参考报文缺失时的超时监视 |

9～15 是未使用槽位，填充 `End_of_List`，Time Mark、MNR 和周期条件均为空。不能把填充条目画成实际执行事件。

三个 Mark1 触发的生效集合彼此不交叉，因此修正后没有同点冲突。Mark3 的发送和检查分别在 Cycle 1、3；Mark4 的发送和检查分别在 Cycle 1、4，也互斥。

## 8. NodeB 每一条有效触发

| 索引 | Time Mark | 类型 / MNR | 条件 | 作用 |
| --- | --- | --- | --- | --- |
| 0 | `T0 / 200` | `Single / 0` | 周期 64，相位 1 | 发送 Msg3，供 NodeA 在 Mark1 检查 |
| 1 | `Mark2 / 600` | `Rx / 0` | 周期 4，相位 0 | 检查 Msg7，使用本地 Filter0 |
| 2 | `Mark2 / 600` | `Rx / 0` | 周期 64，相位 3 | 检查 Cycle 3 的额外 Msg7 |
| 3 | `T23 / 800` | `Single / 1` | 周期 64，相位 3 | 发送 Msg5 |
| 4 | `T34 / 1200` | `Single / 2` | 周期 64，相位 4 | 发送 Msg6 |
| 5 | `Mark7 / 2300` | `Watch / N/A` | 每周期，正常状态 | 从节点同样监视参考报文缺失 |

6～15 是填充。NodeB 是 slave，因此没有 Ref。Filter1、Filter2 分别接受 Msg2、Msg4，但没有对应 Rx_Trigger。Mark2 的两个检查使用同一个 Filter0，生效集合分别是 0、4、…、60 与 3，所以不会同时激活。

以 Msg3 为例：配置声明 NodeB Tx0 → NodeA Filter3；Cycle 1 的 T0=200 产生发送机会；保守的发送及过滤最晚完成预算为 200+4+136+5=345；NodeA 在 Mark1=400 检查。矩阵显示 Cycle 1 的 200～360 为独占预留窗口，而 Rx 检查是 400 的标记，不是从 400 才开始接收。

## 9. 类型与接收检查语义

| Code | 类型 | 处理 |
| --- | --- | --- |
| `0x0` | `Tx_Ref_Trigger` | 非 Gap 边界的参考发送请求，仅候选 master 可配置 |
| `0x1` | `Tx_Ref_Trigger_Gap` | 静态 Gap 边界的参考发送请求 |
| `0x2` | `Tx_Trigger_Single` | 独占发送机会，成功后请求清除，应用须及时重新置位 |
| `0x4` | `Tx_Trigger_Arbitration` | 仲裁候选发送，也可作为合并序列末项 |
| `0x5` | `Tx_Trigger_Merged` | 合并仲裁候选，不合并 CAN 数据帧 |
| `0x6` | `Watch_Trigger` | 普通参考超时监视 |
| `0x7` | `Watch_Trigger_Gap` | Gap 参考超时监视 |
| `0x8` | `Rx_Trigger` | 检查上一次已接受报文的过滤结果 |
| `0xA`～`0xF` | `End_of_List` | 输入接受此范围，输出统一使用 `0xA` 填充 |

0x3 和 0x9 被项目明确排除，不能通过名称或数字绕过检查。

Rx 关注的是自本周期开始或上一个 Rx 检查清除之后的“最后接受过滤结果”。不是只要该区间曾收到目标就一定通过。例如先收到目标 Msg3，随后又接受另一报文，最后的结果可能已经改变。工具对独占发送建立最早/最晚完成区间，检查目标是否及时完成、是否有之后的接受报文覆盖结果，以及顺序能否确定。仲裁完成时间没有被仿真；如果此区间存在本节点接受的仲裁候选，报告不确定性警告，不伪造确定结论。

Merged 检查必须按每个节点、每个生效周期进行。默认 Merge1 在 Cycle 1 从 1000 预留到 1600；NodeA 的 Merged2 后接 Arbi4。其他周期不会因为静态文件里这两行相邻就错误地激活它们。候选实际 SOF 可能受预取、待处理请求和竞争影响，因此 Time Mark 不是精确上总线时间。完整 Tx Handler 请求队列和仲裁仿真不在本工具范围内。

## 10. 配置字段完整说明

### 10.1 顶层

| 字段 | 默认、类型与意义 |
| --- | --- |
| `schema_version` | 整数 1，仅支持此版 |
| `metadata` | 可选英文对象，`source`、`assumptions` 用于来源与演示补充说明 |
| `network` | 网络对象，缺省使用下面的默认参数 |
| `node_defaults` | 可选容量对象，支持四种容量键，逐节点覆盖优先 |
| `nodes` | 非空节点对象数组 |
| `messages` | 报文对象数组，可以为空，但至少仍有参考与监视控制 |
| `arbitration_groups` | 仲裁预留窗口对象数组，缺省为空 |
| `node_triggers` | 主要用于非法配置诊断；控制触发由网络字段生成，不允许重复手工定义。可注入带 Time Mark 的 end 检查提前扫描错误 |

节点 ID、消息名必须匹配 `[A-Za-z][A-Za-z0-9_-]*`，避免输出路径歧义。配置内所有字符串均不得含 CJK 文字。数值支持整数或十进制、`0x`、`0b`、`0o` 字符串；布尔值不能冒充整数。

### 10.2 network

| 字段 | 默认 | 约束与用途 |
| --- | --- | --- |
| `resource_mode` | `custom` | `custom` 或 `m_ttcan` |
| `controller_version` | 无 | m_ttcan 必须明确为 `3.3.0` |
| `level` | 1 | 本版本仅实现静态 Level 1 模型 |
| `bitrate` | 1000000 | 正整数，bit/s |
| `ntu_ns` | 1000 | 正整数；乘 bitrate 必须等于 1e9 |
| `basic_cycle_ntu` | 2000 | 正整数，与参考请求位置保持关系 |
| `matrix_cycles` | 64 | custom 最大 4096；硬件模式仅允许 1、2、4、8、16、32、64 |
| `expand_matrices` | 1 | 正整数，乘 matrix_cycles 不超过 16384，防止意外生成巨大报告 |
| `actual_master` | 最低优先级数值对应节点 | 必须是已配置候选 master |
| `tx_enable_ntu` | 4 | 正整数，允许启动发送的预算范围 |
| `guard_ntu` | 10 | 非负整数，用于预留和自动放置 |
| `filter_delay_ntu` | 5 | 非负整数，用于最晚过滤完成预算 |
| `min_trigger_spacing_ntu` | 1 | 正整数，检查有效触发间距，应按实际 RAM 延迟设置 |
| `time_marks` | 空对象 | 符号时间名到整数 NTU 的映射；没有强制生成对应触发 |
| `reference` | 对象 | 详见下表 |
| `gap` | 关闭 | 详见 Gap 模型 |

`reference` 字段：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `can_id` | `0x080` | 标准或扩展帧范围内，低三位与实际主节点优先级一致 |
| `frame_format` | `standard` | `standard` 或 `extended` |
| `dlc` | 1 | 1～8；Level 1 参考信息的演示预留 |
| `tx_mark_ntu` | basic_cycle_ntu−2 | 必须满足参考请求到下一 SOF 的关系 |
| `watch_mark_ntu` | basic_cycle_ntu+300 | 必须大于下一参考帧保守完成时刻 |
| `worst_frame_ntu` | 自动计算 | 可选，不能小于保守帧时间 |

参考 CAN ID 的高位匹配组不能与普通数据帧碰撞。工具禁止同帧格式下落入同一参考 ID 掩码组的普通消息。默认 reference 不占普通 Tx Buffer；本模型未实现从 Buffer0 提供额外参考 payload 的硬件功能。

### 10.3 nodes 和容量

| 字段 | 默认与限制 |
| --- | --- |
| `node_id` | 必需，唯一英文安全 ID |
| `node_name` | 默认 node_id，页面显示名，英文 |
| `role` | 必需，master 或 slave |
| `master_priority` | 候选 master 按输入顺序分配，0～7，不重复；至多 8 个候选 master |
| `tx_message_capacity` | 默认 8 |
| `rx_std_filter_capacity` | 默认 8 |
| `rx_ext_filter_capacity` | 默认 0 |
| `trigger_capacity` | 默认 16 |

容量均是非负整数，custom 上限 65536 是工具规模保护。m_ttcan 3.3.0 模式分别限制为 32、64、128、64。硬件模式允许配置更小值，表示项目只开放其中部分资源。

容量 N 的编号范围是 0～N−1。容量为 8 不是最大编号等于 8。只占用 MNR7 仍然要求容量至少 8；资源统计里的已用数量则只增加 1。

### 10.4 messages

| 字段 | 默认与说明 |
| --- | --- |
| `name` | 必需，唯一英文消息名 |
| `can_id` | 必需；标准帧 0～0x7FF，扩展帧 0～0x1FFFFFFF |
| `frame_format` | standard |
| `dlc` | 8，允许 0～8 |
| `sender` | 必需，节点 ID |
| `tx_mnr` | 缺省稳定分配最小空闲编号 |
| `receivers` | 缺省空数组，接受节点 ID 字符串或对象 |
| `worst_frame_ntu` | 可选，显式保守帧时间 |
| `transmissions` | 可选，非空发送机会数组；缺省由消息级字段构造一项 |
| `trigger_type` | single；也允许 arbi、merged |
| `time_mark` | 缺省自动放置；整数 NTU 或 time_marks 中的标签 |
| `period_cycles` | 1 |
| `phase` | 0，小于 period_cycles |
| `cycle_code` | 可选，给定时解码为周期和相位；显式重复指定必须一致 |
| `window_length_ntu` | 独占时缺省采用帧时间+Tx Enable+guard |
| `deadline_ntu` | 缺省参考发送请求位置 |
| `group_id` | 仲裁组 ID；merged 必需，普通 arbi 可以自动生成独立组 |

`transmissions` 每项支持从 `trigger_type` 到 `group_id` 的上述调度字段。消息级周期、相位、类型、窗口长度、截止时间和 group_id 可作为默认；条目级覆盖优先。条目提供 cycle_code 时不继承冲突的消息级周期/相位；条目提供周期或相位时不继承消息级 cycle_code。消息级 time_mark 仅在没有 transmissions 数组时用于构造默认发送项。

接收对象字段：

| 字段 | 默认与说明 |
| --- | --- |
| `node_id` | 必需，不能重复，也不能等于 sender |
| `filter_mnr` | 缺省按帧格式在对应接收编号空间中分配 |
| `check_reception` | false；true 时为独占发送自动生成检查 |
| `rx_checks` | 显式检查数组，每项含 time_mark 和周期条件；非空时优先于自动生成 |

自动 Rx 检查放在最晚完成预算之后，并避让本节点已知的同周期触发时间。如果其他约束仍无法满足，报错而不静默放宽协议约束。仲裁报文不能使用自动确定性接收检查。

### 10.5 arbitration_groups

| 字段 | 默认与说明 |
| --- | --- |
| `group_id` | 必需，唯一 |
| `kind` | merged；也可为 arbitration |
| `start_ntu` | 可选整数或时间标签；有固定成员时间点时可由最早标记推导 |
| `end_ntu` | 可选结束边界，必须与 start 和 length 一致 |
| `length_ntu` | 缺省 start/end 差值或保守预算 |

组的生效周期是成员发送机会的并集；组预算取各生效周期的候选帧预算之和的最大值，再增加 Tx Enable。若仅给 end，可按预算反推 start。全部位置自动时，与自动独占窗口一样 first-fit。普通 arbi 未指定组时自动生成独立共享窗口。

自动成员时间点按组中配置顺序、`tx_enable_ntu + guard_ntu` 的间距生成。调用者仍应按节点安排 Merged 和最终 Arbi 的顺序；无法闭合时验证失败。

## 11. 自动调度和失败处理

先解析所有发送机会、周期条件和固定位置，再构造独占及仲裁组任务。固定任务优先；然后按截止时间、位置和窗口名稳定排序。自动任务只在受影响周期都空闲的候选区间中 first-fit。

候选起点包括参考帧+guard 的结束位置及已预留窗口的结束位置。相同绝对偏移可以在不相交的周期集合复用。窗口没有足够长度、固定窗口冲突或截止时间无法满足时，生成包含窗口、消息或周期上下文的错误，不移动用户固定标记，不改变报文周期。

矩阵列边界是所有窗口边界、数据区域内触发标记及周期边界的并集。每个周期的单元格使用同一组列，窗口可以跨多个相邻单元格。列宽不是“每个消息一样宽”。填充 free 区间后，每行完整覆盖 0～Basic Cycle。

## 12. 输出字段完整说明

### 12.1 effective_config.json

包含规范化后的原配置。network 增加缺省值，nodes 增加容量及默认名称，messages 增加已分配编号、接收对象、展开的 transmissions 和帧预算字段。重新作为输入时，计算字段会重新计算，不作为可信外部测量值。

派生帧字段 `unstuffed_bits` 是含 IFS 的未填充长度；`stuffing_bound_bits` 是保守填充数量；`reserved_bits` 为总位数预算；`frame_time_ntu` 为最终预留帧时间。

### 12.2 global_timeline.csv

| 字段 | 含义 |
| --- | --- |
| `matrix_index` | 从 0 开始的展开矩阵编号 |
| `cycle` | 本矩阵内 Cycle Count |
| `node_id` | 关联节点，多个节点用逗号连接 |
| `event_kind` | reference、exclusive、merged、arbitration、free 或 trigger |
| `status` | 事件是计划窗口、候选窗口、接收检查、请求机会还是非执行监视 |
| `start_ntu`、`end_ntu` | 绝对时间；点事件两者相同 |
| `offset_ntu` | 相对该基本周期参考原点的时间 |
| `window_id` | 窗口编号；接收检查可能无窗口编号 |
| `message_name` | 消息名，窗口可包含多个候选 |
| `trigger_index` | 节点本地内存索引；窗口为空 |
| `type_name` | 完整触发类型名；窗口为空 |
| `mnr` | 该节点本地资源编号；控制触发为空 |
| `can_id` | 触发关联 CAN ID 或参考帧 ID；多消息窗口为空 |

status 的取值：`planned_window` 是计划预留，`candidate_window` 是仲裁共享区间，`planned_check` 是接收检查，`request_opportunity` 是数据发送请求机会，`arbitration_candidate` 是仲裁触发，`planned_reference_request` 是实际主节点参考请求，`standby_reference_configuration` 是候选备用主节点配置，`inactive_timeout_monitor` 是正常情况下不执行的监视。

### 12.3 system_matrix.json / csv

JSON 的 `columns` 数组定义公共列，每项是 `column_index`、`start_ntu`、`end_ntu`。`rows` 每项含 `cycle` 和 `cells`。每个 cell 另含 `window_id`、`kind`、`messages`、`nodes`、`group_id`。CSV 将这些 cell 展平成长表，再增加所属 cycle。多个相邻列可能属于同一窗口。

JSON 数组和对象在 CSV 单元格中以 JSON 文本保存，不能简单用逗号切割整行；应使用 CSV 解析器。

### 12.4 message_map.csv / json

| 字段 | 含义 |
| --- | --- |
| `message_name`、`can_id` | 全网报文标识 |
| `direction` | 本节点 tx 或 rx |
| `resource_type` | tx_buffer、rx_std_filter 或 rx_ext_filter |
| `mnr` | 该资源空间中的本地编号 |
| `peer` | 对端节点 ID；多个接收节点用逗号连接 |

### 12.5 trigger_list.csv / json

| 字段 | 含义 |
| --- | --- |
| `node_id` | 所属节点 |
| `alias` | 项目简写 |
| `time_mark_ntu` | 周期内标记；填充为空 |
| `period_cycles`、`phase` | 数据触发的逻辑周期条件；控制/填充为空 |
| `mnr`、`resource_type` | 引用的本地资源 |
| `message_name`、`can_id` | 关联报文；控制/填充为空 |
| `window_id`、`group_id` | 关联窗口或仲裁组 |
| `time_label` | 原输入符号标签；自动或数字时间为空 |
| `state` | both、normal、gap、padding；为本工具的静态适用条件，不是实际状态机轨迹 |
| `trigger_index` | 排序后的 Trigger Memory 索引 |
| `active_cycles` | 周期条件与配置状态匹配的周期；Watch 仍不代表实际执行 |
| `type_code`、`type_name` | 类型编码和完整名称 |
| `cycle_code` | 硬件可表示时给 7 位字符串，否则为空 |

### 12.6 validation_report.json

`valid`、`errors`、`warnings` 表示结果。`issues` 每项含 `severity`、`code`、`message`、`context`。`resources` 按节点统计，各资源对象含 `used`、`capacity`、`remaining`，另含 `expected_data_tx_triggers_per_matrix`。`timing_model`、`hardware_export` 和 `units` 说明解释边界。

默认 NodeA：Tx 使用 3/8、标准过滤器 3/8、Trigger Memory 9/16，每矩阵数据发送触发 19 次。NodeB：Tx 3/4、标准过滤器 3/4、Trigger Memory 6/16，数据发送触发 3 次。两个节点扩展过滤器均为 0/0。

这些数字不同：Tx7 被两条周期条件引用，但只占一个 Buffer；这两条触发占两个内存条目；展开后 Msg7 有 17 次机会。整个网络一矩阵共 22 次数据发送触发，参考请求不计入。

## 13. HTML 各视图与交互

页面顶部摘要显示节点数、基本周期数、矩阵时长和数据发送触发次数。控制栏含节点、报文、窗口类型、矩阵编号、周期起点/数量及缩放。

`First 5` 是默认局部视图，`Full matrix` 显示全部 64 行。修改 Start/Count 可查看任意连续区间。Matrix 切换展开的矩阵；Zoom 扩大水平画布，通过图表内部滚动查看。节点过滤会保留相关发送或接收窗口；节点事件轨道显示所选节点自己的 trigger。

Global Timeline 使用绝对时间，一条总线预留轨道加各节点事件轨道。System Matrix 每行对应一个 Basic Cycle，横向比例一致。紫色参考窗口和参考请求分开表示；橙色斜纹表示仲裁候选预留；粉色标记是 Rx；虚线 Watch 是未执行的监视。矩阵右侧淡紫区域是周期结束后的监视位置，不是额外总线占用。

点击窗口、触发或映射消息，右侧 Selection Inspector 展示记录与各节点编号。相关触发表和消息表高亮。点击具体触发会定位到所属节点的内存表。Clear 清除选择，Reset 恢复所有筛选和默认周期范围。

Node Trigger Lists 包括填充条目，可查看每节点完整容量；Node Message Maps 解释发送和接收空间；Resource Usage 展示资源使用。Configuration Summary 展示来源假设、预留窗口利用率及候选帧预算。后者不等于实测总线利用率，也不承诺仲裁成功。Validation Results 展示当前校验问题和来源修正说明。

HTML 已内嵌全量模型，打开后不读取配置、CSV、字体或网络脚本，因此单独复制一个 HTML 文件仍可使用。

## 14. 源码核心类和函数

| 对象/函数 | 参数、返回与职责 |
| --- | --- |
| `number` | 输入整数或进制字符串，返回整数；拒绝布尔及其他类型 |
| `decode_cycle_code` | 输入代码，返回 period、phase |
| `encode_cycle_code` | 输入 period、phase，返回规范 7 位字符串；无法硬件表示时返回 None |
| `trigger_type` | 输入支持的别名、全名或数值，返回别名；拒绝项目不支持类型 |
| `frame_bits` | 输入 DLC 和帧格式，返回未填充、填充预算、预留位数 |
| `ceil_div` | 整数向上整除，用于时间计算 |
| `Issue` | 保存问题等级、稳定错误码、英文信息及结构化上下文 |
| `Trigger` | 保存本地内存条目及周期适用条件；record 返回可导出的字典 |
| `Window` | 保存某周期的总线预留区间、类型、候选消息及发送节点 |
| `Scheduler.__init__` | 深拷贝配置并建立模型容器，不修改来源输入 |
| `issue` / `valid` | 累积问题；只有 error 使计划失败，warning 保留模型不确定性 |
| `integer` / `mark` | 检查整数范围；将符号时间名解析为 NTU，同时保留标签 |
| `condition` | 解析周期条件、检查矩阵整除和硬件可编码性，返回周期、相位、生效集合 |
| `duration` / `check_can` | 计算保守帧时间；检查帧格式及 CAN ID 范围 |
| `normalize` | 解析全局默认、参考关系、节点角色及容量、消息和过滤器，生成规范配置 |
| `allocate` | 在指定节点和资源空间内保留显式编号或最小空闲编号，返回 MNR |
| `overlaps` | 输入区间和周期集合，返回冲突窗口集合 |
| `first_fit` | 输入长度、周期集合、截止时间，返回首个共同空闲偏移；失败抛出明确错误 |
| `reserve` | 校验总线数据边界和冲突，将窗口展开到生效周期 |
| `prepare_requests` | 将消息配置展开成发送请求，并进行类型和周期检查 |
| `place_requests` | 建立仲裁组和独占任务，稳定放置，并生成发送触发 |
| `generate_rx` | 根据显式或自动检查配置生成接收触发，检查最晚完成预算 |
| `controls` | 按节点角色自动生成 Ref/Watch 及可选 Gap 版本 |
| `applicable` | 判定一个条目在某周期和静态边界模式下是否适用 |
| `validate_triggers` | 排序、检查同点冲突、间距、Tx Enable、合并闭合、容量并填充，生成资源统计 |
| `validate_last_filter` | 依据独占完成区间和上一个 Rx 清除时间检查最后过滤结果，标记仲裁不确定性 |
| `finish_model` | 补齐参考/free 窗口，建立统一列矩阵，展开绝对事件和矩阵时长 |
| `compile` | 顺序调用所有阶段，将可预期配置异常转为报告，返回 Scheduler |
| `report` / `payload` | 生成校验报告及完整 HTML 数据；payload 仅在成功模型上调用 |
| `write_json` / `write_csv` | 创建父目录并以 UTF-8 写文件；JSON 字符串使用 ASCII 转义 |
| `export_schedule` | 写报告；失败停止正常导出，成功时写全部表格和独立 HTML |
| `scan_english` | 扫描受控文本文件的 CJK 字符及转义内容；专用中文文档除外 |
| `interactive_config` | 英文交互生成多节点演示配置，不覆盖指定默认双节点案例 |
| `main` | 解析 CLI、执行、输出英文状态并返回退出码 |

模板脚本中的 `charts` 绘制全局和逐周期图，`tables` 生成节点表，`resourceView` 绘制容量，`inspect` 建立选择关联，`render` 根据筛选刷新。`plotWindow` 和 `plotTrigger` 只读取编译后的区间及条目，不执行第二套排程算法。

## 15. 常见校验错误及处理

| Code | 原因与处理 |
| --- | --- |
| `CONFIGURATION` | 配置结构、字段、角色、编号、CAN ID 或周期非法；阅读 message 中的具体对象和限制 |
| `WINDOW_BOUNDS` | 窗口进入参考保护区、超过参考请求点或长度非正；调整位置/周期参数 |
| `WINDOW_OVERLAP` | 两个独立总线预留在同周期重叠；仲裁参与者应归入同一组而不是建立重叠独占窗口 |
| `WINDOW_BUDGET` | 预留比保守帧和间隔预算小；增加长度或改变负载 |
| `DEADLINE` | 窗口结束晚于配置截止点 |
| `UNSCHEDULABLE` | 受影响周期没有足够共同空闲区间；调整负载、基本周期或显式约束 |
| `GROUP_MARK` | 组内触发不在共享预留范围内 |
| `RX_TOO_EARLY` | 检查前没有完成的独占目标，或该周期根本不发送目标 |
| `RX_LAST_FILTER` | 在目标之后接受了别的消息，最后过滤结果不是目标 |
| `RX_FILTER_CLEARED` | 自周期起点/前次检查之后没有已完成的接受结果 |
| `RX_FILTER_ORDER_UNCERTAIN` | 最早/最晚完成区间不能确定唯一最后结果 |
| `RX_ARBITRATION` | 试图给仲裁候选自动生成确定性 Rx 检查 |
| `RX_ARBITRATION_UNCERTAIN` | 警告：未仿真的仲裁接受流量可能覆盖检查结果 |
| `TRIGGER_COLLISION` | 同节点、同周期、同 Time Mark 有多个有效条目；检查各 Cycle Code |
| `TRIGGER_SPACING` | 间距小于项目配置的访问延迟预算 |
| `TX_ENABLE_INTRUSION` | 活动触发落入本节点 Single/Arbi 的 Tx Enable 区间 |
| `TRIGGER_BOUNDS` | 数据触发早于参考处理结束或晚于下一参考请求 |
| `TIME_MARK_WIDTH` | m_ttcan 模式超出 16 位标记字段 |
| `MERGED_NOT_CLOSED` | 生效的合并序列被不兼容发送打断或没有最终 Arbi |
| `SLAVE_REF` | 从节点非法配置参考发送 |
| `GAP_DISABLED` | 关闭 Gap 却配置 Gap 触发 |
| `END_BEFORE_WATCH` | 填充项可能在正常 Watch 之前被扫描到 |
| `TRIGGER_CAPACITY` | 含控制类条目的有效内存数量超过容量 |
| `STATIC_GAP_MODEL` | 警告：正在使用固定边界延长模型，不是外部事件状态机 |

配置错误可能导致后续出现多个关联问题。原始 Rx3 案例的三条错误是同一错误周期选择导致的不同检查结果，不应只删除其中一个报告来“通过”。

## 16. 修改配置的方法

增加节点：复制节点对象，设置唯一 node_id、角色和容量；修改报文 sender/receivers。生成文件会按节点 ID 自动增加。无需改 HTML 或主脚本。

增加报文：选择唯一 CAN ID、发送节点和接收对象。省略 MNR 让工具自动分配。省略 time_mark 让工具放置独占窗口。需要自动 Rx 时在对应接收对象设置 `check_reception: true`。例如：

```json
{
  "name": "SensorA",
  "can_id": "0x220",
  "sender": "NodeB",
  "period_cycles": 4,
  "phase": 2,
  "receivers": [{"node_id": "NodeA", "check_reception": true}]
}
```

调整容量：容量增加不会自动改变已指定的 MNR；减少时可能因为最大使用编号越界而失败，即使总使用数量较少。调整周期时同步检查接收条件；显式 Rx 条件不会擅自随发送周期重写。

修改主节点：将实际主节点指向候选 master，并保持参考 ID 低三位与其优先级一致。普通报文发送者不会因为角色变化自动迁移。多个候选 master 都拥有参考配置，但全局正常轨迹只把实际主节点的参考请求标为计划发送，其他是 standby 配置。

切换 m_ttcan：添加 `resource_mode: "m_ttcan"` 和 `controller_version: "3.3.0"`。本工具检查公开资源上限、Time Mark、Cycle Code 和部分触发约束；实际 RAM 配置、请求预取队列、硬件最小访问间距及平台约束仍需结合手册与 RTL 单独验证。

## 17. 可选静态 Gap

network.gap 支持 `enabled` 布尔值、`duration_ntu` 正整数和 `after_cycles` 数组。启用时，指定周期边界增加固定空隙。缺省 duration 为 500，after_cycles 为矩阵最后一个周期。

工具推导 `ref_gap_mark_ntu = basic_cycle_ntu + duration_ntu - reference_delay` 和对应 Watch_Gap；指定边界选择 Gap 控制项，其他边界选择普通项，数据发送仍位于正常基本周期区域。绝对时间通过逐周期 origins 累加，不能再对所有周期机械地使用 cycle*basic。

这是为了计划固定空隙的逻辑扩展，没有模拟真实外部事件何时到来、Next_is_Gap 状态机或同步恢复。报告产生 STATIC_GAP_MODEL 警告，不能把它解释为完整 ISO 外部事件同步实现。

## 18. 测试和验收

测试使用标准库 `unittest`，覆盖默认完整矩阵、局部周期解释、本地编号空间、同点互斥、原始冲突保留、容量不足、类型拒绝、Merged 逐周期闭合、早期 Rx、过滤覆盖、固定冲突、空间不足、可复现自动排程、有效配置回放、标准/扩展资源、硬件模式、静态 Gap 与多矩阵 origins、提前 End、CSV/JSON/HTML 导出和 CLI 退出码。

语言扫描覆盖 py、json、csv、html、md、js、cjs、css、txt，检查字面 CJK 和常见 Unicode 转义；JSON 还会解析后再次检查。测试通过数值构造非法字符，不在脚本里写中文字面量。中文文档按 `_zh.md` 后缀排除。

HTML 的浏览器验收应检查：初始 5 行、切换 64 行、节点/报文/窗口筛选、自定义周期范围、缩放、消息选择后的本地映射与表格高亮、重置和浏览器控制台错误。浏览器验收工具不属于运行时依赖。

开发环境安装 Playwright 和对应 Chromium 后，可以执行 `node tests/browser_smoke.cjs`。可选环境变量 `TTCAN_BROWSER_EXECUTABLE` 指定浏览器可执行文件；`TTCAN_SCREENSHOT_DIR` 指定本地验收截图目录。脚本接受第一个位置参数作为 HTML 路径，默认检查 output/schedule.html，目标是指定的双节点 64 周期案例。所有截图仅用于开发检查，交付包中不包含截图。

`verification_report.json` 是交付时验收快照。`unit_tests` 记录标准库测试数量与返回码，`english_scan` 记录英文检查返回码，`default_validation` 是默认校验摘要，`raw_reference` 记录预期失败的返回码和问题码，`browser` 保存页面行数、筛选结果、映射关联、高亮、缩放、重置、移动端溢出、脚本错误和外部请求检查，`sha256` 给出重要文件的字节哈希。用户之后修改配置并运行时，应以新生成的 output/validation_report.json 为准，不把交付快照当成重新运行的结果。

## 19. 参考资料与后续集成

一手资料链接放在英文 README 中。主要依据 M_TTCAN 的 Trigger Memory、TTCAN Configuration 和 Message Handling，以及 Bosch 作者在 CAN in Automation 发表的 TTCAN 时间模型文章。

后续 RTL 集成建议从 JSON 模型读取节点内存条目及资源映射，单独实现寄存器/存储器适配层。不要将 CAN ID 当作 MNR，也不要把默认 Demo 的 Time Mark、容量或过滤延迟硬编码成 IP 固有限制。若要验证真实仲裁、错误或时钟同步，应扩展独立仿真层并记录实际总线事件，保留当前“计划/候选/监视”三者的区别。
