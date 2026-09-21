"""Fresh (uncached) LCSC product lookups: model, brand, package, stock, description."""
import json, sys, time, urllib.request
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}
for code in sys.argv[1:]:
    url = "https://wmsc.lcsc.com/ftps/wm/product/detail?productCode=" + code
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
            d = json.loads(r.read().decode("utf-8")).get("result") or {}
    except Exception as e:
        print("%-10s ERROR %s" % (code, e)); continue
    if not d:
        print("%-10s NOT FOUND" % code); continue
    print("%-10s %-32s %-22s %-14s stock %-8s %s" % (code, (d.get("productModel") or "")[:32], (d.get("brandNameEn") or "")[:22],
          (d.get("encapStandard") or "")[:14], d.get("stockNumber"), (d.get("productDescEn") or d.get("productIntroEn") or "")[:90]))
    time.sleep(0.5)
