import io
src = io.open("act.py", encoding="utf-8").read()
out = io.open("_probe4.txt", "w", encoding="utf-8")
verb = "\u70b9\u51fb"
blk = "    info, why = check_target(hwnd)\n    if why:\n        log(f\"\u26d4 \u62d2\u7edd" + verb + "\uff1a{why}\")\n        return 3"
out.write("blk escaped: " + blk.encode("unicode_escape").decode("ascii") + "\n")
out.write("find_all: %s\n" % [m for m in range(len(src)) if src.startswith(blk, m)][:5])
i = src.find("def do_click(")
out.write("i=%d  find from i: %d\n" % (i, src.find(blk, i)))
out.close()
print("ok")
