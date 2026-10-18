import io
s = io.open("_patch_modal.py", encoding="utf-8").read()
lines = s.split("\n")
out = io.open("_probe3.txt", "w", encoding="utf-8")
start = next(i for i,l in enumerate(lines) if l.strip().startswith("block = "))
for i in range(start, start+5):
    out.write("%d| %s\n" % (i+1, lines[i].encode("unicode_escape").decode("ascii")))
out.close()
print("ok")
