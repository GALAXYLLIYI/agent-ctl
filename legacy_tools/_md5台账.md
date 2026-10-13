# 源→包 md5 映射台账

- 生成器：`tools\gen_legacy.py`（老线真源 → 打包源，自动生成，勿手工改）
- 净化只做一件事：绝对路径 → 环境变量可注入

| 文件 | 源 md5 | 源字节 | 包 md5 | 包字节 | 净化 |
|---|---|---:|---|---:|---|
| `gate.py` | `a07a91164835675bf5a3f58396e953f0` | 25262 | `a07a91164835675bf5a3f58396e953f0` | 25262 | 无规则 |
| `gate_lock.py` | `5b2c5ef3fa17e7ba505ed50705211385` | 4876 | `5b2c5ef3fa17e7ba505ed50705211385` | 4876 | 无规则 |
| `act.py` | `9b7e6a7d65ec7b409fb0a68256e421c0` | 126627 | `9b7e6a7d65ec7b409fb0a68256e421c0` | 126627 | 无规则 |
| `perceive.py` | `ea08ff72e22ce1db97cfecebf17ee97a` | 39929 | `ac8309d0c28234ce74e685c25bc8afcc` | 40351 | 截图目录：不写死本机路径=命中；树差量缓存目录：同上=命中 |
| `auth_popup.py` | `3f6221da5727a74de4b9d8b061cf1a18` | 17384 | `3f6221da5727a74de4b9d8b061cf1a18` | 17384 | 无规则 |
| `brake.py` | `87932ad923647187291667cab04adae6` | 6349 | `87932ad923647187291667cab04adae6` | 6349 | 无规则 |
| `cdp.py` | `37429adc8f95632e4cd8500fc4afb61e` | 17129 | `1ecc4f43d99b5adce7240fad7b75e39a` | 17208 | 浏览器 profile：不写死本机路径=命中；净化引入的 os/subprocess 用法：就地补 import os=已补 |
| `coord.py` | `72d921b3fd29dc9cc747153433b6b3cd` | 5365 | `6ad092da4f6012571b9df0c605a98eaf` | 5365 | 自插老线路径：包内要指向自己=命中 |
| `option_locator.py` | `886a43f9aefefb8b52c45a1c791d3178` | 9002 | `45836bc8ea66a337682a761b2cde1b3c` | 9010 | 选图目录：同上=命中 |
| `selftest.py` | `cef9044a0335b5b3ce99164549aa802f` | 14412 | `c9b5a6a9db1aa62cad6016e71a722e3c` | 14818 | 自测读截图目录：同上=命中；手册目录（铁律 10 的页脚自测）：不写死本机路径；打包后没有手册就整段跳过=命中 |
| `guardian.py` | `d265e1e96e9c0d8b2a09cba995e64285` | 16737 | `d265e1e96e9c0d8b2a09cba995e64285` | 16737 | 无规则 |
| `whitelist.json` | `816b3b1a073889a3472df479beb2b8d2` | 2121 | `816b3b1a073889a3472df479beb2b8d2` | 2121 | 无规则 |
| `click_option.py` | `f887323bbe25e896b68916a0dfb72010` | 4136 | `f887323bbe25e896b68916a0dfb72010` | 4136 | 无规则 |
| `formfill.py` | `289aa9bf0ab361317f404581d31d411a` | 8790 | `289aa9bf0ab361317f404581d31d411a` | 8790 | 无规则 |
| `padd2.py` | `9b39846fa5bf4ee446788748916e2f56` | 8117 | `9b39846fa5bf4ee446788748916e2f56` | 8117 | 无规则 |
| `preflight.py` | `65b3bd80d26080183ce58d45f3a946d6` | 8518 | `65b3bd80d26080183ce58d45f3a946d6` | 8518 | 无规则 |
| `next_question.py` | `887deacb2b414be8b74a6a3e93008392` | 5938 | `887deacb2b414be8b74a6a3e93008392` | 5938 | 无规则 |
| `minidump_probe.py` | `7d41a5c92cc84f880b4031236bdb197e` | 5303 | `7d41a5c92cc84f880b4031236bdb197e` | 5303 | 无规则 |
| `minidump_stack.py` | `728dfa763683b59af14783e4589465f3` | 7315 | `728dfa763683b59af14783e4589465f3` | 7315 | 无规则 |
| `pe_export_lookup.py` | `20ef61de26e303acf90962f925093812` | 4670 | `20ef61de26e303acf90962f925093812` | 4670 | 无规则 |
| `radio_finder.py` | `d59fb637d406fc2bbae711f8cb680a40` | 4279 | `d59fb637d406fc2bbae711f8cb680a40` | 4279 | 无规则 |
| `win_list.py` | `1e18dd1dfef3318fa39268add83b0870` | 3164 | `1e18dd1dfef3318fa39268add83b0870` | 3164 | 无规则 |
| `zoomgrid.py` | `b779a436dda7d03eb219d95484c91074` | 4780 | `b779a436dda7d03eb219d95484c91074` | 4780 | 无规则 |
| `post_compat.json` | `e430115c280facf853b154b17875656d` | 13225 | `e430115c280facf853b154b17875656d` | 13225 | 无规则 |
| `guardian_policy.json` | `5fef60b439a1d6f3b82d1c4da4515b84` | 5062 | `5fef60b439a1d6f3b82d1c4da4515b84` | 5062 | 无规则 |
| `_session.py` | `b9657603c6db1b308f1889341c62ad8f` | 6339 | `b9657603c6db1b308f1889341c62ad8f` | 6339 | 无规则 |
| `probe.py` | `853265fb82558057f7cc28ff52118b77` | 676 | `853265fb82558057f7cc28ff52118b77` | 676 | 无规则 |
| `probe2.py` | `d4e2829074fe52ca776d95b65c4819c8` | 830 | `d4e2829074fe52ca776d95b65c4819c8` | 830 | 无规则 |
| `probe3.py` | `add665ac8cd4f7f7bcc5d6fee5fde4a5` | 993 | `add665ac8cd4f7f7bcc5d6fee5fde4a5` | 993 | 无规则 |
| `probe4.py` | `3f973c424617d4335efa92dc6661d7ae` | 614 | `3f973c424617d4335efa92dc6661d7ae` | 614 | 无规则 |
| `probe5.py` | `da1ff03918eccbcb70b1d5ab32a0da91` | 365 | `da1ff03918eccbcb70b1d5ab32a0da91` | 365 | 无规则 |
| `probe6.py` | `3f6986f1049983f8c40135a0c0aa80b9` | 558 | `3f6986f1049983f8c40135a0c0aa80b9` | 558 | 无规则 |
| `验动作.py` | `fd630f7468fbeca0c70a7e90d7d5c66b` | 39777 | `fd630f7468fbeca0c70a7e90d7d5c66b` | 39777 | 无规则 |
| `闭环.py` | `8ef0bb265aea906676f2ed3300c1a06d` | 8071 | `8ef0bb265aea906676f2ed3300c1a06d` | 8071 | 无规则 |
| `bench.py` | `ad0b281ca7584ee465c0da8336027063` | 22797 | `ad0b281ca7584ee465c0da8336027063` | 22797 | 无规则 |
