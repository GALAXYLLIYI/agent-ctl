import io
src = io.open("act.py", encoding="utf-8").read()
verb = "\u70b9\u51fb"   # 点击
block = '''    info, why = check_target(hwnd)
    if why:
        log(f"\u26d4 \u62d2\u7edd{verb}\uff1a{{why}}")
        return 3'''
print("block repr:", repr(block[:60]))
tgt = block.replace("{verb}", verb)
print("target repr:", repr(tgt[:60]))
print("found at:", src.find(tgt))
# 现在逐字节比对文件里那三行
i = src.find("def do_click(")
k = src.find("info, why = check_target(hwnd)", i)
snippet = src[k:k+len(tgt)+40]
print("file snippet repr:", repr(snippet[:120]))
print("equal?", snippet.startswith(tgt))
for a,b in zip(snippet, tgt):
    if a!=b:
        print("first diff:", repr(a), hex(ord(a)), "vs", repr(b), hex(ord(b)))
        break
else:
    print("no diff in compared range; lens", len(snippet), len(tgt))
