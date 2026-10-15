# data/raw/vendor/ —— 厂商官方规格页存档（第二阶段 M14）

本目录保存 **GPU / 服务器厂商官方规格页**的存档副本，用于硬件规格的可追溯引用：

- 每条规格的容量、带宽、形态都引用这里的文件，并在
  `data/hardware/hardware_catalog.yaml` 中以 `source_id` + `sha256` 记录；
- 版权归原发布者（NVIDIA）所有，本项目只保留抓取当时的原始字节用于哈希校验，
  不额外整理、翻译或再发布其内容；
- 与 `data/raw/official/` 的区别：后者是第一阶段的 **Benchmark 来源**证据（由
  `data/sources/official_manifest.yaml` 索引），本目录是第二阶段的**厂商规格**证据
  （由硬件目录索引）。
- 若发布方要求移除，删除对应文件并清空硬件目录中该条目的 `source_id` 即可，
  相应条目会退回 `pending_verification`。

数据说明：规格页给出的数值**只作容量/带宽的标称来源**；推理真正可分配的显存必须实测，
厂商页面不提供该数值（见 `HardwareSpec.usable_memory_status`）。
