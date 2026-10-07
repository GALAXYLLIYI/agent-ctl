import io
src = io.open("act.py", encoding="utf-8").read()
out = io.open("_probe.txt", "w", encoding="utf-8")
verb = "\u70b9\u51fb"
block = '    info, why = check_target(hwnd)\n    if why:\n        log(f"\u26d4 \u62d2\u7edd{verb}\uff1a{{why}}")\n        return 3'
tgt = block.replace("{verb}", verb)
out.write("found(intact template): %d\n" % src.find(tgt))
i = src.find("def do_click(")
k = src.find("info, why = check_target(hwnd)", i)
snip = src[k:k+len(tgt)]
out.write("file snippet == target ? %s\n" % (snip == tgt))
out.write("file  len=%d target len=%d\n" % (len(snip), len(tgt)))
for idx,(a,b) in enumerate(zip(snip, tgt)):
    if a != b:
        out.write("first diff at %d: file=%r(%s) target=%r(%s)\n" % (idx, a, hex(ord(a)), b, hex(ord(b))))
        break
else:
    out.write("no diff within min length\n")
out.write("--- file bytes around ---\n")
out.write(repr(src[k:k+140]))
out.write("\n--- target bytes ---\n")
out.write(repr(tgt))
out.close()
print("written _probe.txt")
