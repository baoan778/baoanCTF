## 这个 PR 做了什么

（一句话）

## 类型

- [ ] 修 bug
- [ ] 加判据（**必须先在历史数据上回放过**，见 §4b）
- [ ] 加命令 / 工具
- [ ] 文档
- [ ] 部署 / 镜像

## 自检

跑过再勾：

- [ ] `bash tools/bin/selfcheck` 全绿（152 条回归）
- [ ] `python3 -m ruff check board tools` 通过
- [ ] `python3 tools/lint_sh.py` 通过
- [ ] **改了测试数** → `python3 tools/testcount.py --sync`
- [ ] **加了新命令** → 进了 `tools/make-bin.sh` 的 `HAND_TOOLS` 或 `self_tools`

## 如果是判据改动

> §4b：新判据必须先在**历史数据**上回放，合成断言全绿只算「能跑」，不算「对」。

| 项 | 填这里 |
|---|---|
| 用什么数据回放 | |
| 回放结果（命中/误判） | |
| 放宽或收紧了吗，为什么 | |

## 环境差异检查

本项目头号 bug 来源是「同一份代码在 Windows 和 Linux 行为不同」。如果你的改动涉及：

- [ ] 路径（`/tmp`、盘符、分隔符）
- [ ] 编码（`PYTHONIOENCODING`、中文输出）
- [ ] shell（`bash` 名解析、引号嵌套）
- [ ] 权限位（`chmod +x` 是否进了 git）

→ 请说明**两个平台都验证过**。

## 相关 issue
