import re

def scrape_investsmart(code: str):
    """
    Return list of (ex_date, amount, franking%) in last 12 months.
    """
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    r   = requests.get(url, headers=UA, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.select("table tbody tr")
    print(f"InvestSMART rows for {code}: {len(rows)}")

    data   = []
    cutoff = datetime.utcnow().date() - timedelta(days=365)

    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue

        raw_date = tds[0].get_text(strip=True)
        raw_amt  = tds[1].get_text(strip=True)
        raw_fr   = tds[2].get_text(strip=True)

        # Clean amount/franking → keep digits & dot
        amt_str = re.sub(r"[^0-9.]", "", raw_amt)
        fr_str  = re.sub(r"[^0-9.]", "", raw_fr)
        if amt_str == "":
            amt_str = "0"
        if fr_str == "":
            fr_str = "0"

        try:
            ex_date = pd.to_datetime(raw_date, dayfirst=True).date()
            amount  = float(amt_str)
            frank   = float(fr_str)
        except Exception:
            continue

        if ex_date >= cutoff:
            data.append((ex_date, amount, frank))

    print(f"Kept {len(data)} dividend rows for {code} within 12 months")
    return data
