"""List one month of the NCEI HF-radar THREDDS catalog for the US West Coast (USWC). Print, and optionally save.
Makes ONE request to the catalog. No downloads. Run this yourself (the agent never touches the network).

  python scripts/list_ncei.py                                   # 2021-08 (the original fixture month), print only
  python scripts/list_ncei.py --month 202108 --save data/ncei_listing_202108.txt

The saved listing is what src/data/pull_hfr_ncei.py reads (one "name | size | url" line per file).
"*" marks the 2 km SIO hourly files that the pull script uses.
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin

import requests

CATALOG = "https://www.ncei.noaa.gov/thredds-ocean/catalog/ioos/hfradar/rtv/{y}/{ym}/USWC/catalog.xml"
NS = {"t": "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0",
      "xlink": "http://www.w3.org/1999/xlink"}
USED = re.compile(r"\d{12}_hfr_uswc_2km_rtv_uwls_SIO\.nc")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", default="202108", help="YYYYMM")
    ap.add_argument("--save", help="also write the listing to this file (e.g. data/ncei_listing_202108.txt)")
    ap.add_argument("--timeout", type=float, default=30, help="seconds to wait for the catalog (large months are slow)")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"\d{6}", a.month):
        sys.exit("--month must be YYYYMM")
    url = CATALOG.format(y=a.month[:4], ym=a.month)

    r = requests.get(url, timeout=a.timeout)
    print(f"GET {url} -> HTTP {r.status_code}, {len(r.content)} bytes")
    if r.status_code != 200:
        sys.exit("Catalog request failed; stopping.")
    root = ET.fromstring(r.content)

    http_base = None                       # the HTTP download base comes from the catalog's own <service> entries
    for s in root.iter(f"{{{NS['t']}}}service"):
        print(f"service: name={s.get('name')!r} type={s.get('serviceType')!r} base={s.get('base')!r}")
        if s.get("serviceType", "").lower() == "httpserver":
            http_base = s.get("base")
    if http_base is None:
        print("No HTTPServer service in this catalog; download URLs not shown.")

    lines = []
    for d in root.iter(f"{{{NS['t']}}}dataset"):
        path = d.get("urlPath")
        if path is None:                   # container dataset, not a file
            continue
        name = d.get("name", "")
        size_el = d.find("t:dataSize", NS)
        size = f"{size_el.text.strip()} {size_el.get('units', '')}" if size_el is not None else "not stated"
        link = urljoin(url, http_base + path) if http_base else "n/a"
        lines.append(f"{'*' if USED.fullmatch(name) else ' '}  {name} | {size} | {link}")
    print("\nflag  name | size | download URL   (* = 2 km SIO hourly, used by the pull script)")
    for line in lines:
        print("  " + line)
    print(f"{len(lines)} file entries, {sum(l.startswith('*') for l in lines)} marked *.")

    xl = "{" + NS["xlink"] + "}"
    for ref in root.iter(f"{{{NS['t']}}}catalogRef"):
        print(f"sub-catalog: {ref.get(xl + 'title')!r} -> {urljoin(url, ref.get(xl + 'href'))}")

    if a.save:
        p = Path(a.save)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"saved {p}")


if __name__ == "__main__":
    main()
