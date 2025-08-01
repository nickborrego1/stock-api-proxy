def scrape_investsmart(code: str):
    """
    Returns list of (ex_date, amount, franking%) for last 12 months.
    """
    url = f"https://www.investsmart.com.au/shares/asx-{code.lower()}/dividends"
    r   = requests.get(url, headers=UA, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.select("table tbody tr")
    print(f"InvestSMART rows for {code}: {len(rows)}")

    data  = []
    cutoff = datetime.utcnow().date() - timedelta(days=365)

    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue

        raw_date = tds[0].get_text(strip=True)
        raw_amt  = tds[1].get_text(strip=True).replace("$", "").replace(",", "")
        raw_fr   = tds[2].get_text(strip=True).replace("%", "").strip()

        # dash → 0
        if raw_amt in ("", "-"): raw_amt = "0"
        if raw_fr  in ("", "-"): raw_fr  = "0"

        # Parse
        try:
            # InvestSMART uses '1 Jul 2025' (no leading 0) sometimes
            for fmt in ("%d %b %Y", "%-d %b %Y"):
                try:
                    ex_date = datetime.strptime(raw_date, fmt).date()
                    break
                except ValueError:
                    continue
            amount  = float(raw_amt)
            frank   = float(raw_fr)
        except Exception:
            continue

        if ex_date >= cutoff:
            data.append((ex_date, amount, frank))

    return data
