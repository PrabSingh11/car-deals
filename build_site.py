#!/usr/bin/env python3
"""
Turn the transcripts into a structured deals table + a GitHub Pages site.

  1. reads every transcripts/<handle>/<id>.txt
  2. asks the local Claude CLI (free, subscription) to pull the deal fields out
     of the spoken words
  3. writes deals.json and a static index.html (sortable table)

Run:  python3 build_site.py
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/root/clip-agent")
from llm import ask_json  # noqa: E402

ROOT = Path(__file__).resolve().parent
TRANS = ROOT / "transcripts"
DEALS_JSON = ROOT / "deals.json"
INDEX = ROOT / "index.html"

FIELDS_PROMPT = """You are extracting UK car-lease/finance deal details from the \
transcript of a short car-dealer TikTok video. Pull out ONLY what is actually said.

Return STRICT JSON with exactly these keys (use null for anything not stated):
  "brand"        : manufacturer, e.g. "Audi", "BMW" (null if none)
  "model"        : model/trim, e.g. "A3 Black Edition"
  "body_type"    : hatchback / SUV / saloon / estate / coupe / etc.
  "price_new"    : approx RRP/new price as a number in GBP (no symbols), e.g. 50000
  "monthly_cost" : monthly payment as a number in GBP, e.g. 250
  "deposit"      : upfront / deposit as a number in GBP, e.g. 3000
  "term_months"  : length of the deal in months if stated, e.g. 24
  "spec"         : one short line of standout spec/features
  "is_deal"      : true if this video is actually advertising a specific car deal, else false

Transcript:
---
%s
---"""


def parse_header(text):
    h = {}
    for line in text.splitlines():
        if line.startswith("-" * 10):
            break
        m = re.match(r"(\w+)\s*:\s*(.*)", line)
        if m:
            h[m.group(1).lower()] = m.group(2).strip()
    body = text.split("-" * 60, 1)[-1].strip()
    return h, body


def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"\d[\d,]*", str(v))
    return int(m.group(0).replace(",", "")) if m else None


# Known car brands, lowercase. Display casing overrides for the odd ones.
CAR_BRANDS = {
    "audi", "bmw", "mercedes", "mercedes-benz", "volkswagen", "vw", "ford", "vauxhall",
    "toyota", "honda", "nissan", "hyundai", "kia", "peugeot", "renault", "citroen",
    "seat", "cupra", "skoda", "volvo", "mazda", "mini", "land rover", "range rover",
    "jaguar", "porsche", "tesla", "lexus", "fiat", "jeep", "suzuki", "mitsubishi",
    "dacia", "alfa romeo", "abarth", "ds", "genesis", "bentley", "maserati", "ferrari",
    "lamborghini", "aston martin", "mclaren", "rolls-royce", "polestar", "byd", "mg",
    "smart", "subaru", "chevrolet", "chrysler", "ineos",
}
BRAND_DISPLAY = {"bmw": "BMW", "vw": "Volkswagen", "mercedes-benz": "Mercedes",
                 "mg": "MG", "ds": "DS", "byd": "BYD", "range rover": "Land Rover"}


def normalize_brand(b):
    """Return a canonical brand name, or None if it isn't a recognised car brand."""
    if not b:
        return None
    bl = b.strip().lower()
    for brand in sorted(CAR_BRANDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(brand)}\b", bl):
            return BRAND_DISPLAY.get(brand, brand.title())
    return None


def extract_deals():
    deals = []
    dropped = 0
    for txt in sorted(TRANS.rglob("*.txt")):
        raw = txt.read_text(encoding="utf-8")
        header, body = parse_header(raw)
        if len(body) < 20:
            continue
        print(f"[extract] {txt.relative_to(ROOT)} ...", flush=True)
        try:
            data = ask_json(FIELDS_PROMPT % body[:4000])
        except Exception as e:
            print(f"  skipped (LLM error: {e})")
            continue
        if not isinstance(data, dict) or not data.get("is_deal"):
            print("  not a deal, skipped")
            continue
        # quality gate: must be a recognised brand AND have a monthly cost,
        # otherwise it's almost certainly a transcription slip, not a real deal.
        brand = normalize_brand(data.get("brand"))
        monthly = num(data.get("monthly_cost"))
        if not brand or not monthly:
            dropped += 1
            print(f"  dropped (brand={data.get('brand')!r}, monthly={monthly})")
            continue
        deals.append({
            "brand": brand,
            "model": data.get("model"),
            "body_type": data.get("body_type"),
            "price_new": num(data.get("price_new")),
            "monthly_cost": num(data.get("monthly_cost")),
            "deposit": num(data.get("deposit")),
            "term_months": num(data.get("term_months")),
            "spec": data.get("spec"),
            "available": (header.get("pulled") or "").split()[0] or None,
            "creator": header.get("creator"),
            "video": header.get("video"),
        })
    deals.sort(key=lambda d: d.get("available") or "", reverse=True)
    print(f"[extract] kept {len(deals)} deals, dropped {dropped} junk/empty rows")
    return deals


def gbp(v):
    return f"£{v:,}" if isinstance(v, int) else "—"


def transcript_path(d):
    m = re.search(r"@([\w.\-]+)/video/(\d+)", d.get("video") or "")
    return f"transcripts/{m.group(1)}/{m.group(2)}.txt" if m else None


def row_html(d):
    term = f"{d['term_months']} mo" if d.get("term_months") else "—"
    spec = (d.get("spec") or "").replace("<", "&lt;")
    brand = d.get("brand") or "—"
    model = d.get("model") or "—"
    watch = f'<a href="{d["video"]}" target="_blank" rel="noopener">watch</a>' if d.get("video") else "—"
    tp = transcript_path(d)
    trans = f'<a class="btn" href="{tp}" target="_blank" rel="noopener">transcript</a>' if tp else "—"
    return f"""      <tr data-brand="{brand}">
        <td data-label="Brand">{brand}</td>
        <td data-label="Model">{model}</td>
        <td data-label="Type">{d.get('body_type') or '—'}</td>
        <td data-label="New price" class="num">{gbp(d.get('price_new'))}</td>
        <td data-label="Monthly" class="num hero">{gbp(d.get('monthly_cost'))}</td>
        <td data-label="Deposit" class="num">{gbp(d.get('deposit'))}</td>
        <td data-label="Term">{term}</td>
        <td data-label="Spec" class="spec">{spec}</td>
        <td data-label="Available">{d.get('available') or '—'}</td>
        <td data-label="Video">{watch}</td>
        <td data-label="Transcript">{trans}</td>
      </tr>"""


def build_html(deals):
    updated = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    rows = "\n".join(row_html(d) for d in deals) or '<tr><td colspan="11">No deals yet.</td></tr>'
    brands = sorted({d.get("brand") for d in deals if d.get("brand")})
    brand_opts = "".join(f'<option value="{b}">{b}</option>' for b in brands)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Car Deals</title>
<style>
  :root {{ color-scheme: light dark; --bg:#0f1115; --card:#171a21; --line:#2a2f3a;
    --txt:#e8eaed; --mut:#9aa3b2; --hero:#4ade80; --accent:#60a5fa; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:16px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
    background:var(--bg); color:var(--txt); }}
  header {{ padding:28px 20px 12px; max-width:1100px; margin:0 auto; }}
  h1 {{ margin:0 0 4px; font-size:26px; }}
  .sub {{ color:var(--mut); font-size:14px; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:12px 20px 60px; }}
  .controls {{ margin:10px 0 16px; }}
  .controls {{ display:flex; gap:10px; flex-wrap:wrap; }}
  input[type=search], select {{ padding:10px 12px; border-radius:10px;
    border:1px solid var(--line); background:var(--card); color:var(--txt); font-size:15px; }}
  input[type=search] {{ flex:1; min-width:200px; max-width:340px; }}
  .btn {{ display:inline-block; padding:5px 10px; border:1px solid var(--line);
    border-radius:8px; background:rgba(96,165,250,.12); text-decoration:none;
    font-size:13px; white-space:nowrap; }}
  .btn:hover {{ background:rgba(96,165,250,.22); }}
  table {{ width:100%; border-collapse:collapse; background:var(--card);
    border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
  th,td {{ padding:12px 14px; text-align:left; border-bottom:1px solid var(--line); }}
  th {{ font-size:12px; letter-spacing:.04em; text-transform:uppercase; color:var(--mut);
    cursor:pointer; user-select:none; white-space:nowrap; }}
  th:hover {{ color:var(--txt); }}
  tbody tr:hover {{ background:rgba(255,255,255,.03); }}
  td.num {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
  td.hero {{ color:var(--hero); font-weight:700; }}
  td.spec {{ color:var(--mut); font-size:14px; max-width:260px; }}
  a {{ color:var(--accent); }}
  .tablewrap {{ overflow-x:auto; -webkit-overflow-scrolling:touch;
    border:1px solid var(--line); border-radius:12px; }}
  .tablewrap table {{ border:none; border-radius:0; min-width:900px; }}
  @media (max-width:760px) {{
    th,td {{ padding:10px 11px; }}
    td.spec {{ font-size:13px; max-width:200px; }}
  }}
</style>
</head>
<body>
<header>
  <h1>🚗 Car Deals</h1>
  <div class="sub">{len(deals)} deals · updated {updated}</div>
</header>
<div class="wrap">
  <div class="controls">
    <input type="search" id="q" placeholder="Search model, type, spec…">
    <select id="brand"><option value="">All brands</option>{brand_opts}</select>
  </div>
  <div class="tablewrap">
  <table id="t">
    <thead><tr>
      <th>Brand</th><th>Model</th><th>Type</th><th>New price</th><th>Monthly</th><th>Deposit</th>
      <th>Term</th><th>Spec</th><th>Available</th><th>Video</th><th>Transcript</th>
    </tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>
  </div>
</div>
<script>
  const q=document.getElementById('q'),b=document.getElementById('brand'),t=document.getElementById('t');
  function applyFilter(){{const v=q.value.toLowerCase(),br=b.value.toLowerCase();
    t.tBodies[0].querySelectorAll('tr').forEach(r=>{{
      const okText=r.innerText.toLowerCase().includes(v);
      const okBrand=!br||(r.dataset.brand||'').toLowerCase()===br;
      r.style.display=(okText&&okBrand)?'':'none';}});}}
  q.addEventListener('input',applyFilter);
  b.addEventListener('change',applyFilter);
  // click a header to sort
  t.querySelectorAll('th').forEach((th,i)=>{{let asc=true;th.onclick=()=>{{
    const rows=[...t.tBodies[0].rows];
    rows.sort((a,b)=>{{const x=a.cells[i].innerText,y=b.cells[i].innerText;
      const nx=parseFloat(x.replace(/[^0-9.]/g,'')),ny=parseFloat(y.replace(/[^0-9.]/g,''));
      const both=!isNaN(nx)&&!isNaN(ny);
      return (both?nx-ny:x.localeCompare(y))*(asc?1:-1);}});
    asc=!asc;rows.forEach(r=>t.tBodies[0].appendChild(r));}};}});
</script>
</body>
</html>"""


def main():
    deals = extract_deals()
    DEALS_JSON.write_text(json.dumps(deals, indent=2), encoding="utf-8")
    INDEX.write_text(build_html(deals), encoding="utf-8")
    print(f"\n[done] {len(deals)} deals -> {DEALS_JSON.name}, {INDEX.name}")


if __name__ == "__main__":
    main()
