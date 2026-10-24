# 源→包 md5 映射台账

- 生成器：`tools\gen_legacy.py`（老线真源 → 打包源，自动生成，勿手工改）
- 净化只做一件事：绝对路径 → 环境变量可注入

| 文件 | 源 md5 | 源字节 | 包 md5 | 包字节 | 净化 |
|---|---|---:|---|---:|---|
| `gate.py` | `e874ca6c28f82c0a565fec6acf41f153` | 29266 | `e874ca6c28f82c0a565fec6acf41f153` | 29266 | 无规则 |
| `gate_lock.py` | `5b2c5ef3fa17e7ba505ed50705211385` | 4876 | `5b2c5ef3fa17e7ba505ed50705211385` | 4876 | 无规则 |
| `act.py` | `3030d4b6c56c9f8046ac73f62be52d77` | 216212 | `3030d4b6c56c9f8046ac73f62be52d77` | 216212 | 无规则 |
| `perceive.py` | `6b2651ce12c093b13a419e5dd27f379f` | 40129 | `6b2651ce12c093b13a419e5dd27f379f` | 40129 | 截图目录：不写死本机路径=已是净化态（跳过）；树差量缓存目录：同上=已是净化态（跳过） |
| `auth_popup.py` | `b9f278e84811df9773ead8a07cd556df` | 50941 | `b9f278e84811df9773ead8a07cd556df` | 50941 | 无规则 |
| `brake.py` | `714cc345ecb9177935c783b6a99a2d7b` | 7973 | `714cc345ecb9177935c783b6a99a2d7b` | 7973 | 无规则 |
| `cdp.py` | `41e0e804497300417f944df123d8b507` | 18626 | `41e0e804497300417f944df123d8b507` | 18626 | 浏览器 profile：不写死本机路径=已是净化态（跳过） |
| `coord.py` | `7f92afe2e8b4f37da63a419b5d9da931` | 5381 | `7f92afe2e8b4f37da63a419b5d9da931` | 5381 | 无规则 |
| `option_locator.py` | `0b8a51c6a80ed47c737c11fbf90f451b` | 9123 | `0b8a51c6a80ed47c737c11fbf90f451b` | 9123 | 选图目录：同上=已是净化态（跳过） |
| `selftest.py` | `1735278d9d3000ea095991b370f4698f` | 15266 | `1735278d9d3000ea095991b370f4698f` | 15266 | 自测读截图目录：同上=已是净化态（跳过）；手册目录（铁律 10 的页脚自测）：不写死本机路径；打包后没有手册就整段跳过=已是净化态（跳过） |
| `guardian.py` | `b9429ed26b6743c762002c78e4bd0acb` | 18986 | `b9429ed26b6743c762002c78e4bd0acb` | 18986 | 无规则 |
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
| `inject_magic.py` | `4053920ac8ecd2d58b0306ccbf7b97fd` | 1653 | `4053920ac8ecd2d58b0306ccbf7b97fd` | 1653 | 无规则 |
| `post_compat.json` | `e430115c280facf853b154b17875656d` | 13225 | `e430115c280facf853b154b17875656d` | 13225 | 无规则 |
| `guardian_policy.json` | `9e47426aa46079f549a7d1944c50154e` | 4899 | `9e47426aa46079f549a7d1944c50154e` | 4899 | 无规则 |
| `_session.py` | `b9657603c6db1b308f1889341c62ad8f` | 6339 | `b9657603c6db1b308f1889341c62ad8f` | 6339 | 无规则 |
| `probe.py` | `853265fb82558057f7cc28ff52118b77` | 676 | `853265fb82558057f7cc28ff52118b77` | 676 | 无规则 |
| `probe2.py` | `d4e2829074fe52ca776d95b65c4819c8` | 830 | `d4e2829074fe52ca776d95b65c4819c8` | 830 | 无规则 |
| `probe3.py` | `add665ac8cd4f7f7bcc5d6fee5fde4a5` | 993 | `add665ac8cd4f7f7bcc5d6fee5fde4a5` | 993 | 无规则 |
| `probe4.py` | `3f973c424617d4335efa92dc6661d7ae` | 614 | `3f973c424617d4335efa92dc6661d7ae` | 614 | 无规则 |
| `probe5.py` | `da1ff03918eccbcb70b1d5ab32a0da91` | 365 | `da1ff03918eccbcb70b1d5ab32a0da91` | 365 | 无规则 |
| `probe6.py` | `3f6986f1049983f8c40135a0c0aa80b9` | 558 | `3f6986f1049983f8c40135a0c0aa80b9` | 558 | 无规则 |
| `验动作.py` | `e517c1f0bd88816dbbf62060c1bda989` | 39777 | `e517c1f0bd88816dbbf62060c1bda989` | 39777 | 无规则 |
| `闭环.py` | `8ef0bb265aea906676f2ed3300c1a06d` | 8071 | `8ef0bb265aea906676f2ed3300c1a06d` | 8071 | 无规则 |
| `bench.py` | `825a7f030d5beee06f00c3bd7e7b9a92` | 32408 | `825a7f030d5beee06f00c3bd7e7b9a92` | 32408 | 无规则 |
| `authdir.py` | `141022ea097888f247b724d94f481ca9` | 3323 | `141022ea097888f247b724d94f481ca9` | 3323 | 无规则 |
