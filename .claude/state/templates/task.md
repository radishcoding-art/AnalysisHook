---
# 当前任务的元信息. 每次 /newAnalysis 后由用户填写.
# AI 必须先 read 此文件 + 输出 "任务理解 checklist" 到 plan.md, 用户审过才能开始 tool call.
task_name: "" # 简短任务名 (一行)
task_type: "" # 任务类型 (例如: 逆向分析 / Bug 修复 / 性能优化 / 协议解析 / 加密破解 / ...)
created_at: "" # ISO 8601 时间戳
status: not_started # not_started | active | completed | abandoned
priority: normal # low | normal | high | critical
---

# 任务

(用户填写以下内容. AI 不允许动这里, 只能 read.)

## 1. 任务目标 (一句话)

<例: "完全搞清楚目标程序的某端口应用层加密算法">

## 2. 详细描述

<在这里写任务的完整描述. 至少 3-5 段, 越详细越好. 包括:>

- 这个任务是什么 / 为什么要做
- 输入是什么 (代码 / 二进制 / 网络抓包 / 文档 ...)
- 输出是什么 (报告 / 代码 / 解密脚本 ...)
- 已知背景 (已经知道什么, 还不知道什么)
- 历史尝试 (之前试过什么, 结果如何)

## 3. 范围 (允许 / 禁止)

### 允许做的

<例:>

- 读 / 反汇编目标二进制
- 在 attached debugger 里设置断点
- 在 Python 里复现算法

### 禁止做的

<例: 任何与此无关的工作禁止开展. 不允许发散到其他端口 / 其他模块 / 其他工具.>

## 4. 输入

<列出 AI 可访问的资源 + 路径>
<例:>

- 二进制: `<路径, 例如 E:\Apps\MyApp\target.exe>`
- 抓包: `data/capture.pcapng`
- TLS keylog: `data/TlsKeyLog.txt`
- 参考项目: `<路径, 例如 E:\WorkSpace\ReferenceProject>`

## 5. 输出 / 验收标准

<明确说明任务什么时候算完成. 必须可证伪, 不允许"看上去搞定了">
<例:>

- 加密函数入口地址 + 完整反汇编 (写到 facts.md)
- AES key + IV (从内存提取, 写到 facts.md)
- Python 解密脚本 (能用 key/IV 解密 c2s.bin 的所有 385 帧)
- 至少解密 3 帧 + 输出 protobuf 内容验证

## 6. 关键约束 / 风险点

<列出对工作流的约束. 这些会变成 hook 的检查依据.>
<例:>

- 目标进程有反调试, 不能用 ScyllaHide / VMP unpacker
- 不能修改任何文件 (只读分析)
- 必须用 Python 计算地址, 禁止心算
- 最多用 8GB 内存

## 7. 工具与方法

<本任务用到的工具 + 它们的角色. 这部分跟 .claude/state/tool_constraints.md 配合.>
<例:>

- **x64dbg**: 唯一调试器. 所有 bp / step / run.
- **Cheat Engine**: 只读. 内存读取 / 反汇编 / AOB 扫描. 见 tool_constraints.md.
- **IDA Pro**: 静态分析.
- **Wireshark**: 网络抓包查看.
- **Python**: 算法复现 + 地址计算.

## 8. 关键已知 / 假设

<列出你已经知道的事实 + 仍然是假设的事>
<例:>

- 已知: 目标端口是 TLS 1.2 (Wireshark 验证过)
- 已知: 应用层 16 字节对齐 (统计验证)
- 假设: 加密算法是 AES-256-CBC (基于某参考项目推断, 未在当前版本验证)

## 9. 不明确的地方 (Open Questions)

<列出 AI 应该在执行过程中验证的开放问题. AI 写"任务理解 checklist" 时必须 echo 这些.>
<例:>

- 新版的 outer_type 是 4, 跟旧版的 3 有什么区别?
- 密钥是不是固定的, 还是每次会话生成?
- 心跳消息和 verify_lic 用同一个密钥吗?

## 10. 参考资料

<列出参考的代码 / 文档 / 论文 / Wiki>
<例:>

- `memory/<file>.md` (上次会话的工作记录, 内容未必全准)
- `docs/<protocol>.md` (协议参考文档)

---

# AI 必读 (写完任务理解前不允许做关键 tool call)

读完 task.md 后, AI 必须:

1. 在 `plan.md` 输出 "任务理解 checklist", 包含:
   - 我对**任务目标**的理解 (用自己的话复述)
   - 我对**范围**的理解 (允许/禁止)
   - 我对**输入/输出**的理解
   - 我列出的**关键假设** (我打算验证的, 不是默认相信的)
   - 我的**第一个 step 计划**草稿
2. 输出后**停下汇报**, 等待用户审 task understanding
3. 用户审过, 把 `plan.md` frontmatter 的 `task_understanding_acked` 改成 `true`
4. AI 收到信号后才能 advance 第一个 step
