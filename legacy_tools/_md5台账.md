# 源→包 md5 映射台账

- 生成器：`tools\gen_legacy.py`（老线真源 → 打包源，自动生成，勿手工改）
- 净化只做一件事：绝对路径 → 环境变量可注入

| 文件 | 源 md5 | 源字节 | 包 md5 | 包字节 | 净化 |
|---|---|---:|---|---:|---|
| `gate.py` | `425d3a46c78a46f82d52e4792b155377` | 25262 | `425d3a46c78a46f82d52e4792b155377` | 25262 | 无规则 |
| `gate_lock.py` | `5b2c5ef3fa17e7ba505ed50705211385` | 4876 | `5b2c5ef3fa17e7ba505ed50705211385` | 4876 | 无规则 |
| `act.py` | `5bcb084f5bacff07367cc8eff462a7e4` | 124800 | `5bcb084f5bacff07367cc8eff462a7e4` | 124800 | 无规则 |
| `perceive.py` | `764828c984f4f67dc4c630ef4084ebad` | 39929 | `d04eb537504ccb37dcba1aaf081ce058` | 40351 | 截图目录：不写死本机路径=命中；树差量缓存目录：同上=命中 |
| `auth_popup.py` | `f6da33c61385fda937fcdcaf4aac9dd8` | 17384 | `f6da33c61385fda937fcdcaf4aac9dd8` | 17384 | 无规则 |
| `brake.py` | `87932ad923647187291667cab04adae6` | 6349 | `87932ad923647187291667cab04adae6` | 6349 | 无规则 |
| `cdp.py` | `5394ab26d4f52dcf90e9e926d6d8e707` | 17129 | `7a8a143a565507351dbbf74e51114dcf` | 17208 | 浏览器 profile：不写死本机路径=命中；净化引入的 os/subprocess 用法：就地补 import os=已补 |
| `coord.py` | `72d921b3fd29dc9cc747153433b6b3cd` | 5365 | `6ad092da4f6012571b9df0c605a98eaf` | 5365 | 自插老线路径：包内要指向自己=命中 |
| `option_locator.py` | `bfb9df9ef20f2390072e12b380ff536b` | 9002 | `4a7791e4dea07b431e77923fa261015a` | 9010 | 选图目录：同上=命中 |
| `selftest.py` | `b6a1e65d3e19bd68b849732ede00ec5e` | 7154 | `11bf0f35d90c63debbea3cefb371ba6e` | 7560 | 自测读截图目录：同上=命中；手册目录（铁律 10 的页脚自测）：不写死本机路径；打包后没有手册就整段跳过=命中 |
| `guardian.py` | `d265e1e96e9c0d8b2a09cba995e64285` | 16737 | `d265e1e96e9c0d8b2a09cba995e64285` | 16737 | 无规则 |
| `whitelist.json` | `cbb46ff90d8321b071bc6f712189f3d0` | 5040 | `cbb46ff90d8321b071bc6f712189f3d0` | 5040 | 无规则 |
| `click_option.py` | `38806c02b59bc8c8dc874bafbce15753` | 4136 | `38806c02b59bc8c8dc874bafbce15753` | 4136 | 无规则 |
| `formfill.py` | `a074272e99b0ff3ffc4810fe4b40a883` | 8795 | `a074272e99b0ff3ffc4810fe4b40a883` | 8795 | 无规则 |
| `padd2.py` | `b17514bbe061a569d934250a67e7daec` | 8117 | `b17514bbe061a569d934250a67e7daec` | 8117 | 无规则 |
| `preflight.py` | `65b3bd80d26080183ce58d45f3a946d6` | 8518 | `65b3bd80d26080183ce58d45f3a946d6` | 8518 | 无规则 |
| `next_question.py` | `35bbb01805b46dc9d32200d6fc5536bf` | 5938 | `35bbb01805b46dc9d32200d6fc5536bf` | 5938 | 无规则 |
| `minidump_probe.py` | `7d41a5c92cc84f880b4031236bdb197e` | 5303 | `7d41a5c92cc84f880b4031236bdb197e` | 5303 | 无规则 |
| `minidump_stack.py` | `728dfa763683b59af14783e4589465f3` | 7315 | `728dfa763683b59af14783e4589465f3` | 7315 | 无规则 |
| `pe_export_lookup.py` | `20ef61de26e303acf90962f925093812` | 4670 | `20ef61de26e303acf90962f925093812` | 4670 | 无规则 |
| `radio_finder.py` | `d59fb637d406fc2bbae711f8cb680a40` | 4279 | `d59fb637d406fc2bbae711f8cb680a40` | 4279 | 无规则 |
| `win_list.py` | `1e18dd1dfef3318fa39268add83b0870` | 3164 | `1e18dd1dfef3318fa39268add83b0870` | 3164 | 无规则 |
| `zoomgrid.py` | `b779a436dda7d03eb219d95484c91074` | 4780 | `b779a436dda7d03eb219d95484c91074` | 4780 | 无规则 |
| `post_compat.json` | `17a64c90aeae79a7fac10f029b30ce39` | 13225 | `17a64c90aeae79a7fac10f029b30ce39` | 13225 | 无规则 |
| `probe.py` | `853265fb82558057f7cc28ff52118b77` | 676 | `853265fb82558057f7cc28ff52118b77` | 676 | 无规则 |
| `probe2.py` | `d4e2829074fe52ca776d95b65c4819c8` | 830 | `d4e2829074fe52ca776d95b65c4819c8` | 830 | 无规则 |
| `probe3.py` | `add665ac8cd4f7f7bcc5d6fee5fde4a5` | 993 | `add665ac8cd4f7f7bcc5d6fee5fde4a5` | 993 | 无规则 |
| `probe4.py` | `3f973c424617d4335efa92dc6661d7ae` | 614 | `3f973c424617d4335efa92dc6661d7ae` | 614 | 无规则 |
| `probe5.py` | `da1ff03918eccbcb70b1d5ab32a0da91` | 365 | `da1ff03918eccbcb70b1d5ab32a0da91` | 365 | 无规则 |
| `probe6.py` | `3f6986f1049983f8c40135a0c0aa80b9` | 558 | `3f6986f1049983f8c40135a0c0aa80b9` | 558 | 无规则 |
