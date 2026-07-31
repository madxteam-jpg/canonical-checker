import asyncio
import io
import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urljoin, urlparse

import aiohttp
import pandas as pd
from bs4 import BeautifulSoup
from html2image import Html2Image
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


def get_chromium_path():
    """Detect binary path for Chromium/Chrome on Linux servers."""
    possible_paths = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    for path in possible_paths:
        if os.path.exists(path) or shutil.which(path):
            return path
    
    # Fallback search using system 'which'
    found_path = shutil.which("chromium") or shutil.which("chromium-browser")
    return found_path


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


def capture_dashboard_screenshot(df: pd.DataFrame) -> bytes:
    """Generates a styled HTML summary of the audit and captures it as a PNG screenshot."""
    total_scanned = len(df)
    indexable_count = int(df["is_indexable"].sum())
    self_ref_count = int((df["canonical_status"] == "Self-Referential").sum())
    conflicts_count = int((df["canonical_status"] == "Canonicalized Elsewhere").sum())
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Render HTML table rows (up to 25 preview rows for proof screenshot)
    table_rows = ""
    for _, row in df.head(25).iterrows():
        status_color = "#28a745" if row["is_indexable"] else "#dc3545"
        table_rows += f"""
        <tr>
            <td style="max-width: 300px; word-break: break-all;">{row['requested_url']}</td>
            <td>{row['status_code']}</td>
            <td>{row['canonical_status']}</td>
            <td style="max-width: 300px; word-break: break-all;">{row['canonical_found'] or '-'}</td>
            <td style="color: {status_color}; font-weight: bold;">{row['is_indexable']}</td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                background-color: #0e1117;
                color: #ffffff;
                padding: 30px;
                width: 1200px;
            }}
            .header {{
                border-bottom: 2px solid #333;
                padding-bottom: 15px;
                margin-bottom: 25px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .title {{ font-size: 24px; font-weight: bold; color: #4094f7; }}
            .badge {{ background: #1f2937; padding: 6px 12px; border-radius: 6px; font-size: 12px; color: #9ca3af; }}
            .metrics-grid {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 15px;
                margin-bottom: 30px;
            }}
            .metric-card {{
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 8px;
                padding: 20px;
                text-align: center;
            }}
            .metric-value {{ font-size: 32px; font-weight: bold; margin-top: 5px; color: #f0f6fc; }}
            .metric-label {{ font-size: 13px; color: #8b949e; text-transform: uppercase; }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: #161b22;
                border-radius: 8px;
                overflow: hidden;
                border: 1px solid #30363d;
                font-size: 13px;
            }}
            th {{ background: #21262d; color: #c9d1d9; text-align: left; padding: 12px; border-bottom: 1px solid #30363d; }}
            td {{ padding: 10px 12px; border-bottom: 1px solid #21262d; color: #8b949e; }}
            tr:nth-child(even) {{ background-color: #0d1117; }}
            .footer {{
                margin-top: 20px;
                font-size: 11px;
                color: #6e7681;
                text-align: right;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <div class="title">🔍 Quality Check Audit Proof</div>
            <div class="badge">Timestamp: {timestamp}</div>
        </div>

        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-label">Total Scanned</div>
                <div class="metric-value">{total_scanned}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Indexable URLs</div>
                <div class="metric-value" style="color: #3fb950;">{indexable_count}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Self-Referential</div>
                <div class="metric-value" style="color: #58a6ff;">{self_ref_count}</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Canonical Conflicts</div>
                <div class="metric-value" style="color: #f85149;">{conflicts_count}</div>
            </div>
        </div>

        <h3>Audit Results Summary (First 25 Rows)</h3>
        <table>
            <thead>
                <tr>
                    <th>Requested URL</th>
                    <th>Status</th>
                    <th>Canonical Status</th>
                    <th>Canonical Found</th>
                    <th>Indexable</th>
                </tr>
            </thead>
            <tbody>
                {table_rows}
            </tbody>
        </table>

        <div class="footer">Verified by Bulk Canonical & Indexability Audit Engine</div>
    </body>
    </html>
    """

    browser_path = get_chromium_path()

    with tempfile.TemporaryDirectory() as tmpdir:
        hti_kwargs = {"output_path": tmpdir}
        if browser_path:
            hti_kwargs["browser_path"] = browser_path

        hti = Html2Image(**hti_kwargs)
        # Linux container flags required for serverless/Streamlit environments
        hti.custom_flags = ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']

        output_filename = "audit_proof.png"
        hti.screenshot(html_str=html_content, save_as=output_filename, size=(1240, 1000))

        file_path = os.path.join(tmpdir, output_filename)
        with open(file_path, "rb") as f:
            image_bytes = f.read()

    return image_bytes


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
        st.subheader("Download Audit Exports")
        col_csv, col_img = st.columns(2)

        # 1. Retained CSV Export
        csv_data = df.to_csv(index=False).encode('utf-8')
        col_csv.download_button(
            label="📥 Download Data (CSV)",
            data=csv_data,
            file_name="canonical_audit_results.csv",
            mime="text/csv"
        )

        # 2. Screenshot Proof Export with Detailed Error Logging
        try:
            with st.spinner("Generating QA Screenshot Proof..."):
                screenshot_bytes = capture_dashboard_screenshot(df)

            col_img.download_button(
                label="📸 Download Proof Screenshot (PNG)",
                data=screenshot_bytes,
                file_name="canonical_audit_proof.png",
                mime="image/png"
            )
        except Exception as e:
            col_img.error(f"Screenshot generation failed: {str(e)}")
