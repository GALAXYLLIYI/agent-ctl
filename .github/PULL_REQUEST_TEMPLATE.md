## 改了什么

<!-- 一句话；多处改动请分条 -->

## 为什么

<!-- 对应 issue / 事件号 / 真机现象；没有依据就别提 PR -->

## 验过了什么（缺一项就说明为什么）

- [ ] `python -X utf8 tools\验收_发版.py` → 退出码：____（0=全绿，1=红，2=有验不了）
- [ ] 受影响判据单独跑过 + `--inject` 会变红（证明判据有牙）
- [ ] 改过 live 相关文件 → `python -X utf8 tools\check_live_sync.py` 37/37
- [ ] 改过代码 → **重启过 MCP server**
- [ ] `python -X utf8 tools\presubmit_scan.py` → CLEAN（无个人路径/密钥/凭据）

## 没验到的（如实写）

<!-- 例如：真机没跑、只在靶场验了；这一栏不许空着写“无” -->
