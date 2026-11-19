# GLOSSARY

本仓术语表。只收「这个词在本仓是什么意思」，不收怎么实现 —— 实现去哪层看，见 `docs/边界契约.md` 指到的那份文件。

| 词 | 意思 |
|---|---|
| 真源（source of truth） | 某类事实唯一允许被修改的地方；同一内容在别处出现，都是投影 |
| 投影（projection） | 从真源派生、只读的副本；只能由脚本生成，或退化成一句指针 |
| 落点（landing） | 一份技能或产物在磁盘上被某个客户端真正读取的位置；一个客户端一个落点 |
| 读数链（reading chain） | 把 PDF 上的图形读成参数的那条路径；权威只有一份（`footprint-check/SKILL.md` 第 3 步） |
| 形态路由（form） | 由 `pdf_probe.form` 判出的四种文件形态（cad_vector / printed / thin_text / scan）；只改前置步骤，不改读数不变式 |
| 通道级 / 任务级 | 通道级＝同一时刻只有一个会话持有 SkillBridge 通道（硬约束，锁保证）；任务级＝同一时刻只跑一个建图任务（软约束，只报告不排队） |
| 假绿（false green） | 检查显示通过，但实际没有任何东西被检查到（零落点、`N/A` 计通过） |
| 冻结基线（frozen baseline） | `eval/baselines/*.json`：只由 `--update` 写，用来和上一次被认可的行为对照，不是真源 |