"""List the NCEI HF-radar THREDDS catalog for USWC, Aug 2021. Print only.
Makes ONE request to the catalog. No downloads. Run this yourself."""
import re
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

import requests

CATALOG_URL = ("https://www.ncei.noaa.gov/thredds-ocean/catalog/ioos/hfradar/rtv/"
               "2021/202108/USWC/catalog.xml")
HEADERS = {"User-Agent": "caltech-me295-student"}
NS = {"t": "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0",
      "xlink": "http://www.w3.org/1999/xlink"}
# Window is 2021-08-10 00:00 to 2021-08-12 00:00. Flag 2 km entries dated the 10th or
# 11th with "*", and 2 km entries dated the 12th with "?" (only its 00:00 hour is in window).
IN_WINDOW = re.compile(r"2021[-_]?08[-_]?(10|11)")
ON_12TH = re.compile(r"2021[-_]?08[-_]?12")
IS_2KM = re.compile(r"2km", re.IGNORECASE)

r = requests.get(CATALOG_URL, headers=HEADERS, timeout=30)
print(f"GET {CATALOG_URL} -> HTTP {r.status_code}, {len(r.content)} bytes")
if r.status_code != 200:
    sys.exit("Catalog request failed; stopping.")
root = ET.fromstring(r.content)

# The HTTP download base comes from the catalog's own <service> entries.
http_base = None
for s in root.iter(f"{{{NS['t']}}}service"):
    print(f"service: name={s.get('name')!r} type={s.get('serviceType')!r} base={s.get('base')!r}")
    if s.get("serviceType", "").lower() == "httpserver":
        http_base = s.get("base")
if http_base is None:
    print("No HTTPServer service in this catalog; download URLs not shown.")

print("\nflag  name | size | download URL   (* = 2km, 10th/11th; ? = 2km, 12th)")
n = 0
for d in root.iter(f"{{{NS['t']}}}dataset"):
    path = d.get("urlPath")
    if path is None:                       # container dataset, not a file
        continue
    n += 1
    name = d.get("name", "")
    size_el = d.find("t:dataSize", NS)
    size = f"{size_el.text.strip()} {size_el.get('units', '')}" if size_el is not None else "not stated"
    url = urljoin(CATALOG_URL, http_base + path) if http_base else "n/a"
    flag = " "
    if IS_2KM.search(name):
        flag = "*" if IN_WINDOW.search(name) else "?" if ON_12TH.search(name) else " "
    print(f"  {flag}  {name} | {size} | {url}")
print(f"{n} file entries.")

XL = "{" + NS["xlink"] + "}"
for ref in root.iter(f"{{{NS['t']}}}catalogRef"):
    print(f"sub-catalog: {ref.get(XL + 'title')!r} -> {urljoin(CATALOG_URL, ref.get(XL + 'href'))}")
