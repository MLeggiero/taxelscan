"""Fresh JLCPCB parts keyword search (read-only): code, model, brand, package, stock, library type."""
import json, sys, urllib.request
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json", "Content-Type": "application/json"}
for kw in sys.argv[1:]:
    body = json.dumps({"keyword": kw, "currentPage": 1, "pageSize": 12}).encode()
    req = urllib.request.Request("https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList", data=body, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.loads(r.read().decode("utf-8"))
        lst = d["data"]["componentPageInfo"]["list"] or []
    except Exception as e:
        print(kw, "ERROR", e); continue
    print("== %s (%d)" % (kw, len(lst)))
    for c in lst:
        print("   %-10s %-30s %-20s %-22s stock %-7s %s" % (c.get("componentCode"), (c.get("componentModelEn") or "")[:30], (c.get("componentBrandEn") or "")[:20],
              (c.get("componentSpecificationEn") or "")[:22], c.get("stockCount"), c.get("componentLibraryType")))
