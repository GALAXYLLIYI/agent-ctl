import io, re
src = io.open("act.py", encoding="utf-8").read()
lines = src.split("\n")
out = io.open("_probe2.txt", "w", encoding="utf-8")
# 找 do_click 起，打印到 return 3 那一行为止（不含 emoji 用码点表示）
start = next(i for i,l in enumerate(lines) if l.startswith("def do_click("))
for i in range(start, start+8):
    out.write("%d| %s\n" % (i+1, lines[i].encode("unicode_escape").decode("ascii")))
out.write("\n--- 模板（脚本 L125-128）---\n")
for i in range(124, 128):
    out.write("%d| %s\n" % (i+1, lines[i].encode("unicode_escape").decode("ascii")))
out.close()
print("ok")
