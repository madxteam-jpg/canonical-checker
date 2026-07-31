import asyncio
import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urljoin, urlparse

import aiohttp
import matplotlib.pyplot as plt
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


def generate_results_image_proof(df: pd.DataFrame) -> io.BytesIO:
    """Renders a visual proof image containing audit metrics and a table of top results."""
    total_scanned = len(df)
    indexable_count = int(df["is_indexable"].sum())
    self_ref_count = int((df["canonical_status"] == "Self-Referential").sum())
    conflicts_count = int((df["canonical_status"] == "Canonicalized Elsewhere").sum())
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Limit to top 20 rows for a clean quality report image
    preview_df = df[["requested_url", "status_code", "canonical_status", "is_indexable"]].head(20).copy()

    # Truncate overly long URLs for aesthetics
    preview_df["requested_url"] = preview_df["requested_url"].apply(lambda x: x[:45] + "..." if len(x) > 45 else x)

    # Figure Setup
    fig = plt.figure(figsize=(12, 10), facecolor="#0e1117")
    gs = fig.add_gridspec(2, 4, height_ratios=[1, 4])

    # Header & Metric Cards
    metrics_data = [
        ("TOTAL SCANNED", f"{total_scanned}", "#4094f7"),
        ("INDEXABLE", f"{indexable_count}", "#28a745"),
        ("SELF-REFERENTIAL", f"{self_ref_count}", "#17a2b8"),
        ("CONFLICTS", f"{conflicts_count}", "#dc3545"),
    ]

    for idx, (label, val, color) in enumerate(metrics_data):
        ax = fig.add_subplot(gs[0, idx])
        ax.set_facecolor("#161b22")
        ax.text(0.5, 0.65, val, fontsize=22, fontweight='bold', color=color, ha='center', va='center')
        ax.text(0.5, 0.25, label, fontsize=9, fontweight='bold', color="#8b949e", ha='center', va='center')
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color("#30363d")

    # Table View
    ax_table = fig.add_subplot(gs[1, :])
    ax_table.axis('off')

    table = ax_table.table(
        cellText=preview_df.values,
        colLabels=["Requested URL", "Status", "Canonical Status", "Indexable"],
        cellLoc='left',
        loc='center'
    )

    table.auto_set_font_size(False)
    table.set_細_size = 9
    table.scale(1, 1.8)

    # Style Table Cells
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#30363d")
        if row == 0:
            cell.set_text_props(weight='bold', color='#f0f6fc', size=10)
            cell.set_facecolor('#21262d')
        else:
            cell.set_text_props(color='#c9d1d9', size=9)
            cell.set_facecolor('#161b22' if row % 2 == 0 else '#0d1117')

    # Watermark Header/Footer
    plt.suptitle("🔍 Quality Check Audit Proof", fontsize=16, fontweight='bold', color="#ffffff", y=0.96)
    plt.figtext(0.95, 0.02, f"Verified: {timestamp}", fontsize=8, color="#8b949e", ha="right")

    plt.tight_layout(rect=[0, 0.03, 1, 0.93])

    # Save to BytesIO Memory Buffer
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=200, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


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

        # 2. Image Proof Export (Matplotlib Native Rendering)
        with st.spinner("Rendering Results Preview Image..."):
            image_buf = generate_results_image_proof(df)

        col_img.download_button(
            label="📸 Download Proof Image (PNG)",
            data=image_buf,
            file_name="canonical_audit_proof.png",
            mime="image/png"
        )
