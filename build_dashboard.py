# -*- coding: utf-8 -*-
"""組裝儀表板: indicators.json + dashboard_template.html → tw_deleverage_dashboard.html"""
import json, os, sys

def main():
    ind_path = sys.argv[1] if len(sys.argv) > 1 else "out/indicators.json"
    with open(ind_path, encoding="utf-8") as f:
        ind = f.read()
    with open("dashboard_template.html", encoding="utf-8") as f:
        tpl = f.read()
    out = tpl.replace("/*__DATA__*/null", ind, 1)
    out = out.replace("/*__SRC__*/null", "{}", 1)
    os.makedirs("out", exist_ok=True)
    with open("out/tw_deleverage_dashboard.html", "w", encoding="utf-8") as f:
        f.write(out)
    print("OK", len(out), "bytes -> out/tw_deleverage_dashboard.html")

if __name__ == "__main__":
    main()
