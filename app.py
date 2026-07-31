import asyncio
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import aiohttp
import pandas as pd
from bs4 import BeautifulSoup
import streamlit as st

# Set Streamlit Page Config
st.set_page_config(
    page_title="Bulk Canonical & Indexability Checker",
    page_icon="🔍",
    layout="wide"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def normalize_url(url: str) -> str:
    """Normalize URLs for accurate comparison."""
    if not url:
        return ""
    parsed = urlparse(url)
    scheme_netloc_path = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
    if parsed.query:
        scheme_netloc_path += f"?{parsed.query}"
    return scheme_netloc_path.lower()


async def check_single_url(session: aiohttp.ClientSession, url: str, timeout: int) -> dict:
    """Asynchronous worker to fetch and inspect a single URL."""
    result = {
        "requested_url": url,
        "status_code": None,
        "final_url": None,
        "canonical_found": None,
        "canonical_status": "UNKNOWN",
        "meta_robots": None,
        "x_robots_header": None,
        "is_indexable": True,
        "error": None
    }

    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True) as response:
            result["status_code"] = response.status
            result["final_url"] = str(response.url)

            # Check HTTP Headers for X-Robots-Tag
            x_robots = response.headers.get("X-Robots-Tag", "")
            result["x_robots_header"] = x_robots

            content_type = response.headers.get("Content-Type", "")
            if response.status == 200 and "text/html" in content_type:
                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")

                # Extract Canonical Tag
                canonical_tag = soup.find("link", rel=lambda x: x and "canonical" in x.lower())
                if canonical_tag and canonical_tag.get("href"):
                    raw_canonical = canonical_tag["href"].strip()
                    full_canonical = urljoin(str(response.url), raw_canonical)
                    result["canonical_found"] = full_canonical

                    norm_final = normalize_url(str(response.url))
                    norm_canonical = normalize_url(full_canonical)

                    if norm_final == norm_canonical:
                        result["canonical_status"] = "Self-Referential"
                    else:
                        result["canonical_status"] = "Canonicalized Elsewhere"
                else:
                    result["canonical_status"] = "Missing Canonical"

                # Extract Meta Robots
                meta_robots = soup.find("meta", attrs={"name": lambda x: x and x.lower() == "robots"})
                if meta_robots and meta_robots.get("content"):
                    result["meta_robots"] = meta_robots["content"].strip()

            # Evaluate Indexability
            robots_directives = f"{result['meta_robots'] or ''} {result['x_robots_header'] or ''}".lower()
            if "noindex" in robots_directives or result["status_code"] != 200:
                result["is_indexable"] = False

    except Exception as e:
        result["error"] = str(e)
        result["canonical_status"] = "Request Failed"
        result["is_indexable"] = False

    return result


async def run_bulk_check(urls: list, concurrency: int, timeout: int, progress_bar, status_text):
    """Run batch audit asynchronously with semaphore speed control."""
    semaphore = asyncio.Semaphore(concurrency)
    results = []
    completed = 0
    total = len(urls)

    async def sem_worker(session, url):
        nonlocal completed
        async with semaphore:
            res = await check_single_url(session, url, timeout)
            completed += 1
            progress_bar.progress(completed / total)
            status_text.text(f"Auditing... [{completed}/{total}] {url[:60]}...")
            return res

    async with aiohttp.ClientSession() as session:
        tasks = [sem_worker(session, url) for url in urls]
        results = await asyncio.gather(*tasks)

    return results


# --- STREAMLIT UI ---
st.title("🔍 Bulk Canonical & Indexability Checker")
st.markdown("Audit hundreds of URLs simultaneously to detect broken canonicals, `noindex` directives, and redirects.")

# Sidebar Configuration
st.sidebar.header("Audit Settings")
concurrency = st.sidebar.slider("Concurrent Requests", min_value=1, max_value=30, value=10, help="Higher values speed up checking but may trigger rate limits.")
timeout = st.sidebar.slider("Timeout Per Request (Sec)", min_value=3, max_value=30, value=10)

# Input Methods Tab
tab_paste, tab_upload = st.tabs(["📋 Paste URLs", "📁 Upload File / Sitemap"])

urls_to_check = []

with tab_paste:
    raw_urls = st.text_area("Paste URLs (one per line):", height=200, placeholder="https://example.com/\nhttps://example.com/about/")
    if raw_urls.strip():
        urls_to_check = [u.strip() for u in raw_urls.split("\n") if u.strip().startswith("http")]

with tab_upload:
    uploaded_file = st.file_uploader("Upload CSV, TXT, or XML Sitemap", type=["csv", "txt", "xml"])
    if uploaded_file:
        if uploaded_file.name.endswith(".xml"):
            # Simple XML Sitemap Parsing
            tree = ET.parse(uploaded_file)
            root = tree.getroot()
            namespaces = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
            urls_to_check = [elem.text.strip() for elem in root.findall('.//ns:loc', namespaces)]
        else:
            content = uploaded_file.read().decode("utf-8")
            urls_to_check = [u.strip() for u in re.split(r'[\r\n,]+', content) if u.strip().startswith("http")]

if urls_to_check:
    st.info(f"Loaded **{len(urls_to_check)}** valid URLs ready for inspection.")
    
    if st.button("🚀 Start Audit", type="primary"):
        progress_bar = st.progress(0)
        status_text = st.empty()

        # Run async loop inside Streamlit
        audit_results = asyncio.run(
            run_bulk_check(urls_to_check, concurrency, timeout, progress_bar, status_text)
        )

        status_text.success("✅ Audit complete!")
        df = pd.DataFrame(audit_results)

        # --- METRICS DASHBOARD ---
        st.markdown("---")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Scanned", len(df))
        m2.metric("Indexable URLs", int(df["is_indexable"].sum()))
        m3.metric("Self-Referential", int((df["canonical_status"] == "Self-Referential").sum()))
        m4.metric("Canonical Conflicts", int((df["canonical_status"] == "Canonicalized Elsewhere").sum()))

        # Display Data Table
        st.subheader("Results Preview")
        st.dataframe(df, use_container_width=True)

        # --- EXPORT OPTIONS ---
        col_csv, col_excel = st.columns(2)
        
        # CSV Export
        csv_data = df.to_csv(index=False).encode('utf-8')
        col_csv.download_button(
            label="📥 Download Results (CSV)",
            data=csv_data,
            file_name="canonical_audit_results.csv",
            mime="text/csv"
        )

        # Excel Export using openpyxl
        import io
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name="SEO Audit")
        col_excel.download_button(
            label="📊 Download Results (Excel)",
            data=buffer.getvalue(),
            file_name="canonical_audit_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
