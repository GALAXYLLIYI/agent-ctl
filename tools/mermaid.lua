-- 刀B · pandoc Lua 过滤器：mermaid 代码块 → PNG 嵌入（白底、嵌入型）；SVG 母版另存 work
-- 用法：mmdc --size <宽> -s 3 -b white -p tools/puppeteer-edge.json -o <png>
local tmpdir = os.getenv("AGENTCTL_MMD_TMP") or os.getenv("TEMP") or "."
local mmdc = os.getenv("AGENTCTL_MMDC") or "mmdc"
local size = os.getenv("AGENTCTL_MMD_WIDTH") or "1200"
local svg_dir = os.getenv("AGENTCTL_MMD_SVG_DIR") or tmpdir

local function write_file(path, text)
  local f = assert(io.open(path, "wb"))
  f:write(text)
  f:close()
end

local function run(cmd)
  local ok = os.execute(cmd)
  return ok
end

function CodeBlock(el)
  local is_mermaid = false
  for _, c in ipairs(el.classes) do
    if c == "mermaid" then is_mermaid = true end
  end
  if not is_mermaid then return nil end

  local base = "mmd_" .. tostring(os.time()) .. "_" .. tostring(math.random(100000))
  local mmd = tmpdir .. "/" .. base .. ".mmd"
  local png = tmpdir .. "/" .. base .. ".png"
  local svg = svg_dir .. "/" .. base .. ".svg"
  write_file(mmd, el.text)

  local p = os.getenv("AGENTCTL_PUPPETEER") or "tools/puppeteer-edge.json"
  local cmd = string.format(
    '%s -i "%s" -o "%s" --size %s -s 3 -b white -p "%s"',
    mmdc, mmd, png, size, p)
  local ok = run(cmd)
  if not ok then
    io.stderr:write("[mermaid] mmdc 失败：" .. cmd .. "\n")
    return nil
  end
  -- SVG 母版（同一图源只渲染一次；失败不阻断 PNG 嵌入）
  run(string.format('%s -i "%s" -o "%s" --size %s -b white -p "%s"',
                    mmdc, mmd, svg, size, p))

  return pandoc.Para({ pandoc.Image({ pandoc.Str("图") }, png) })
end
