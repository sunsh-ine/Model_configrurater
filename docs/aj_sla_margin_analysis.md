# AJ3 SLA 裕量与安全容量

| 工作点 | 最差 TPOT P99 | 10% 裕量(≤36) | 5% 裕量(≤38) | 正式通过 |
|---|---|---|---|---|
| ISL2048/C10（exploratory） | 40.572 | False | False | 0/1 |
| ISL2048/C12（exploratory） | 39.078 | False | False | 0/1 |
| ISL2048/C2（exploratory） | 30.448 | True | True | 1/1 |
| ISL2048/C4（exploratory） | 31.180 | True | True | 1/1 |
| ISL2048/C4（exploratory） | 32.089 | True | True | 1/1 |
| ISL2048/C6（formal） | 35.488 | True | True | 4/5 |
| ISL2048/C8（legacy_unbound） | 39.616 | False | False | 5/5 |
| ISL2048/C8（formal） | 39.388 | False | False | 5/5 |
| ISL4096/C2（exploratory） | 30.746 | True | True | 1/1 |
| ISL4096/C4（exploratory） | 38.391 | False | False | 1/1 |
| ISL4096/C6（exploratory） | 46.204 | False | False | 0/1 |
| ISL4096/C8（exploratory） | 54.232 | False | False | 0/1 |
| ISL4096/C8（exploratory） | 54.637 | False | False | 0/1 |
| ISL8192/C1（exploratory） | 25.435 | True | True | 1/1 |
| ISL8192/C2（exploratory） | 34.957 | True | True | 1/1 |
| ISL8192/C2（exploratory） | 35.226 | True | True | 1/1 |

> 10%/5% 是内部风险等级，不是客户 SLA 的修改。
