import io
src = io.open("act.py", encoding="utf-8").read()
sig = "def do_click("
i = src.find(sig)
print("sig at", i)
block = '    info, why = check_target(hwnd)\n    if why:\n        log(f"X{verb}X")\n        return 3'
# 用真 verb 拼
verb = "click-zh"
zh = "\u62d2\u7edd\u70b9\u51fb"
real = '    info, why = check_target(hwnd)\n    if why:\n        log(f"\u26d4 '+zh+'\uff1a{why}")\n        return 3'
j = src.find(real, i)
print("zh block found at", j)
# 也试半角
real2 = real.replace("\uff1a", ":")
print("half block found at", src.find(real2, i))
# 打印真实片段
k = src.find("info, why = check_target(hwnd)", i)
print("snippet repr:", repr(src[k-8:k+90]))
